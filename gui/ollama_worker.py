"""
Background worker for Ollama translation calls with term protection
"""

import logging
import re
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import requests
from PySide6.QtCore import QMutex, QMutexLocker, QObject, Signal, Slot

from gui.en_word_checker import preload as _preload_en_dict
from gui.ru_word_checker import preload as _preload_ru_dict, text_has_russian_words
from gui.uk_word_checker import preload as _preload_uk_dict
from gui.de_word_checker import preload as _preload_de_dict
from gui.es_word_checker import preload as _preload_es_dict
from gui.fr_word_checker import preload as _preload_fr_dict
from gui.it_word_checker import preload as _preload_it_dict
from gui.pl_word_checker import preload as _preload_pl_dict
from gui.ptbr_word_checker import preload as _preload_ptbr_dict
from gui.glossary import GlossaryManager
from gui.term_protector import TermProtector
from gui.translation_cache import TranslationCache
from gui.translation_memory import TranslationMemory

logger = logging.getLogger(__name__)

# Signal for long strings that should be skipped
SKIP_SIGNAL = "__SKIP_TRANSLATION__"

# ── Mixed-script repair ────────────────────────────────────────────────────────
# Maps Latin letters to their Ukrainian Cyrillic equivalents.
# Visual homoglyphs first (a→а, e→е, o→о, …), then phonetic approximations
# (d→д, f→ф, h→г, …) for letters the model substitutes in Cyrillic words.
_LATIN_TO_UKR = str.maketrans(
    "aAbBcCdDeEfFgGhHiIjJkKlLmMnNoOpPqQrRsStTuUvVwWxXyYzZ",
    "аАвВсСдДеЕфФґҐгГіІйЙкКлЛмМнНоОрРкКрРсСтТуУвВвВхХуУзЗ",
)
_LATIN_ALPHA_RE    = re.compile(r"[A-Za-z]")
_CYRILLIC_ALPHA_RE = re.compile(r"[А-ЯЁа-яёЄєІіЇїҐґ]")
# Word = run of letters from either script, possibly joined by an apostrophe.
_MIXED_WORD_RE = re.compile(r"[A-Za-zА-ЯЁа-яёЄєІіЇїҐґ][A-Za-zА-ЯЁа-яёЄєІіЇїҐґʼ'’]*")


def _fix_mixed_script(text: str) -> str:
    """Convert stray Latin letters inside predominantly-Cyrillic words.

    Example: ``"dослідницький"`` → ``"дослідницький"``

    Only triggers when a word mixes both scripts AND Cyrillic characters
    outnumber Latin ones (≥ 2 Cyrillic, Cyrillic count > Latin count).
    Pure-Latin words (proper nouns, acronyms, tags) are left untouched.
    """
    def _fix(m: re.Match) -> str:
        word = m.group(0)
        lat = len(_LATIN_ALPHA_RE.findall(word))
        cyr = len(_CYRILLIC_ALPHA_RE.findall(word))
        if lat > 0 and cyr >= 2 and cyr > lat:
            return word.translate(_LATIN_TO_UKR)
        return word

    return _MIXED_WORD_RE.sub(_fix, text)


# ── Newline / spacing structure restoration ───────────────────────────────────
# Regex that splits a string into alternating [text, delimiter, text, …] lists
# where delimiters are one or more consecutive newlines.
_NL_SPLIT_RE = re.compile(r"(\n\n+|\n)")


def _restore_line_structure(translated: str, original_text: str) -> str:
    """Restore the newline pattern and per-line leading whitespace from *original_text*.

    Called when the model drops ``[[STRUCT_BREAK_*]]`` tokens, collapsing a
    multi-line source string into a single flat paragraph.

    Algorithm:
    1.  Parse *original_text* into segments + their trailing delimiters
        (``\\n`` or ``\\n\\n``) using a single regex split.
    2.  Flatten *translated* by replacing existing newlines with spaces.
    3.  Proportionally split the flat text into N segments, snapping each
        cut-point to the nearest word boundary.
    4.  Copy leading ``<space>/<tab>`` from the corresponding original segment.
    5.  Rejoin with the original delimiters.

    Empty segments in the original (e.g. a trailing ``\\n``) stay empty in
    the result so the trailing newline is preserved exactly.
    """
    if not translated or not original_text:
        return translated

    orig = original_text.replace("\r\n", "\n")
    trans = translated.replace("\r\n", "\n")

    expected_nl = orig.count("\n")
    if expected_nl == 0 or trans.count("\n") >= expected_nl:
        return translated  # nothing missing

    # Split original into [seg0, delim0, seg1, delim1, …, segN]
    raw = _NL_SPLIT_RE.split(orig)
    orig_segs = raw[0::2]   # text segments
    delimiters = raw[1::2]  # delimiter strings (\n / \n\n / …)
    n_segs = len(orig_segs)
    if n_segs <= 1:
        return translated

    # Flatten translated: collapse any existing newlines to a single space.
    flat = re.sub(r"\s*\n\s*", " ", trans).strip()
    if not flat:
        return translated

    # Proportional lengths — only non-empty (content-bearing) orig segments
    # contribute to the split ratio; empty ones (e.g. trailing \n) stay empty.
    seg_lens = [len(s.strip()) for s in orig_segs]
    total_orig = sum(seg_lens)
    if total_orig == 0:
        return translated

    content_indices = [i for i, ln in enumerate(seg_lens) if ln > 0]
    n_content = len(content_indices)
    if n_content <= 1:
        return translated

    total_trans = len(flat)

    # Find cut positions in `flat` for each content-segment boundary.
    cumulative = 0
    cuts: list[int] = []
    for ci in content_indices[:-1]:
        cumulative += seg_lens[ci]
        target = max(1, min(int(total_trans * cumulative / total_orig), len(flat) - 1))

        # Search outward from `target` for the nearest space, within ±40 % of
        # the expected segment width.
        snap = max(4, int(total_trans * seg_lens[ci] / total_orig * 0.4))
        lo = max(0, target - snap)
        hi = min(len(flat), target + snap)

        best = target
        for off in range(max(hi - target, target - lo) + 1):
            found = False
            for sign in (1, -1):
                pos = target + sign * off
                if lo <= pos < hi and flat[pos] == " ":
                    best = pos
                    found = True
                    break
            if found:
                break
        cuts.append(best)

    # Slice flat into parts for content segments.
    content_parts: list[str] = []
    prev = 0
    for cut in cuts:
        content_parts.append(flat[prev:cut].strip())
        prev = cut + 1  # skip the space at the cut point
    content_parts.append(flat[prev:].strip())

    # Build per-segment results, assigning content parts to non-empty slots.
    result_segs: list[str] = [""] * n_segs
    for part_i, ci in enumerate(content_indices):
        part = content_parts[part_i] if part_i < len(content_parts) else ""
        # Copy leading whitespace from the corresponding original line.
        orig_line = orig_segs[ci]
        leading = orig_line[: len(orig_line) - len(orig_line.lstrip(" \t"))]
        result_segs[ci] = leading + part

    # Reassemble with original delimiters.
    result = ""
    for i, seg in enumerate(result_segs):
        result += seg
        if i < len(delimiters):
            result += delimiters[i]
    return result


# ── Per-language prompt data ──────────────────────────────────────────────────

# Full display name used in the "To {Language}:" user-turn prefix that
# TranslateGemma was fine-tuned on.
_LANG_DISPLAY: dict[str, str] = {
    "en":     "English",
    "de":     "German",
    "es":     "Spanish",
    "fr":     "French",
    "it":     "Italian",
    "ja":     "Japanese",
    "pl":     "Polish",
    "ptbr":   "Portuguese (Brazilian)",
    "zhhans": "Chinese (Simplified)",
    "ru":     "Russian",
    "uk":     "Ukrainian",
}

# Rule 1 of the system prompt: target-language style / register guidance.
_TARGET_STYLE: dict[str, str] = {
    "de": (
        "Write formal Standard German (Hochdeutsch). Use 'Sie' when the source is "
        "formal, 'du' for casual dialogue. Preserve Starfield's NASApunk tone — "
        "technical, precise, modern. Technical readouts use present tense "
        "('Systeme normal'). Avoid unnecessary Anglicisms."
    ),
    "es": (
        "Write neutral Latin-American Spanish (avoid strong regional markers). "
        "Use 'tú' for casual address, 'usted' when the source is formal. "
        "Technical readouts use present tense. Avoid unnecessary Anglicisms."
    ),
    "fr": (
        "Write standard French (fr-FR). Use 'vous' when the source is formal, "
        "'tu' for casual dialogue. Preserve Starfield's NASApunk tone — precise "
        "and modern. Technical readouts use present tense ('Systèmes normaux'). "
        "Avoid unnecessary Anglicisms."
    ),
    "it": (
        "Write standard Italian appropriate to Starfield's NASApunk sci-fi tone. "
        "Use 'lei' (formal) or 'tu' (informal) matching the source register. "
        "Technical readouts use present tense. Avoid unnecessary Anglicisms."
    ),
    "ja": (
        "Write Japanese appropriate to Starfield's sci-fi tone. "
        "Use polite form (です/ます) for UI labels and system messages; "
        "match the register (丁寧語/普通体) of the source for dialogue. "
        "Use katakana for sci-fi terms and proper nouns (e.g. ニューアトランティス). "
        "Technical readouts use present tense (システム正常)."
    ),
    "pl": (
        "Write standard Polish with correct grammatical gender and case agreement. "
        "Match the source register: formal stays formal, casual stays casual. "
        "Technical readouts use present tense. Avoid unnecessary Anglicisms."
    ),
    "ptbr": (
        "Write Brazilian Portuguese (pt-BR). Use 'você' for direct address. "
        "Match the source register. Technical readouts use present tense. "
        "Prefer established Brazilian Starfield localization vocabulary."
    ),
    "zhhans": (
        "Write Simplified Chinese (简体中文) using modern standard Mandarin. "
        "UI labels and system messages should be concise; dialogue should match "
        "the source register. Technical readouts use present tense. "
        "Use standard game-localization sci-fi terminology."
    ),
    "ru": (
        "Write standard Russian matching the source register (formal stays formal, "
        "casual stays casual). Technical readouts use present tense ('Системы в норме'). "
        "Avoid excessive Anglicisms; prefer established Russian game-localization vocabulary."
    ),
    "uk": (
        "Write authentic, distinctly Ukrainian — not a transliteration of Russian. "
        "Use Ukrainian-specific vocabulary where it diverges from Russian "
        "(наразі not сейчас, завдяки not благодаря, але not однако). "
        "Technical readouts use present tense ('Системи в нормі'). "
        "Avoid magic/fantasy vocabulary and archaic phrasing in a sci-fi context."
    ),
}

# Extra notes inserted after the universal rules — for source languages that
# need special handling instructions.
_SOURCE_EXTRA: dict[str, str] = {
    "ru": (
        "Source text is Russian — produce natural target-language text, "
        "not a transliteration."
    ),
}

# Additional note for specific source→target pairs (appended to _SOURCE_EXTRA).
_PAIR_EXTRA: dict[tuple[str, str], str] = {
    # Russian → Ukrainian: provide the explicit Cyrillic letter-mapping rules.
    ("ru", "uk"): (
        "Convert Russian-specific letters: ы→и, э→е, ъ→(drop), "
        "ё→йо at word start/after vowel or ьо after consonant."
    ),
}

# One or two representative examples per (source, target) language pair.
# Examples are appended at the end of the system prompt.
_LANG_EXAMPLES: dict[tuple[str, str], str] = {
    ("en", "uk"): (
        "I'm heading to New Atlantis to meet with Sarah. "
        "→ Я прямую до Нової Атлантиди на зустріч із Сарою.\n"
        "[FAILED] Access denied. Entry unauthorized. "
        "→ [ЗБІЙ] Доступ заборонено. Вхід не дозволено.\n"
        "Mining Equipment → Гірниче обладнання"
    ),
    ("ru", "uk"): (
        "Склад оружия → Склад зброї\n"
        "Тюремная база Спейсеров → Тюремна база Спейсерів\n"
        "[Солгать] Я ничего не знаю. → [Збрехати] Я нічого не знаю.\n"
        "Добыча ресурсов → Видобування ресурсів"
    ),
    ("en", "de"): (
        "I'm heading to New Atlantis to meet with Sarah. "
        "→ Ich bin auf dem Weg nach Neu-Atlantis, um Sarah zu treffen.\n"
        "[FAILED] Access denied. Entry unauthorized. "
        "→ [FEHLER] Zugriff verweigert. Eintritt nicht autorisiert.\n"
        "Mining Equipment → Bergbauausrüstung"
    ),
    ("en", "es"): (
        "I'm heading to New Atlantis to meet with Sarah. "
        "→ Me dirijo a Nueva Atlántida para reunirme con Sarah.\n"
        "[FAILED] Access denied. Entry unauthorized. "
        "→ [FALLO] Acceso denegado. Entrada no autorizada.\n"
        "Mining Equipment → Equipo minero"
    ),
    ("en", "fr"): (
        "I'm heading to New Atlantis to meet with Sarah. "
        "→ Je me dirige vers la Nouvelle-Atlantide pour rencontrer Sarah.\n"
        "[FAILED] Access denied. Entry unauthorized. "
        "→ [ÉCHEC] Accès refusé. Entrée non autorisée.\n"
        "Mining Equipment → Équipement minier"
    ),
    ("en", "it"): (
        "I'm heading to New Atlantis to meet with Sarah. "
        "→ Mi sto dirigendo a Nuova Atlantide per incontrare Sarah.\n"
        "[FAILED] Access denied. Entry unauthorized. "
        "→ [ERRORE] Accesso negato. Ingresso non autorizzato.\n"
        "Mining Equipment → Attrezzatura mineraria"
    ),
    ("en", "ja"): (
        "I'm heading to New Atlantis to meet with Sarah. "
        "→ サラと会うためにニューアトランティスへ向かっています。\n"
        "[FAILED] Access denied. Entry unauthorized. "
        "→ [失敗] アクセス拒否。入場は許可されていません。\n"
        "Mining Equipment → 採掘装備"
    ),
    ("en", "pl"): (
        "I'm heading to New Atlantis to meet with Sarah. "
        "→ Zmierzam do Nowej Atlantydy, aby spotkać się z Sarah.\n"
        "[FAILED] Access denied. Entry unauthorized. "
        "→ [BŁĄD] Odmowa dostępu. Wejście nieautoryzowane.\n"
        "Mining Equipment → Sprzęt górniczy"
    ),
    ("en", "ptbr"): (
        "I'm heading to New Atlantis to meet with Sarah. "
        "→ Estou indo para Nova Atlântida encontrar Sarah.\n"
        "[FAILED] Access denied. Entry unauthorized. "
        "→ [FALHA] Acesso negado. Entrada não autorizada.\n"
        "Mining Equipment → Equipamento de mineração"
    ),
    ("en", "zhhans"): (
        "I'm heading to New Atlantis to meet with Sarah. "
        "→ 我正前往新亚特兰蒂斯与莎拉会面。\n"
        "[FAILED] Access denied. Entry unauthorized. "
        "→ [失败] 访问被拒绝。进入未经授权。\n"
        "Mining Equipment → 采矿设备"
    ),
}


@dataclass
class TranslationRequest:
    """Represents a single string translation request."""

    index: int
    original_text: str
    string_id: int
    source_lang: str
    target_lang: str
    context: str = ""
    quality_level: int = 7
    locale_hint: str = ""
    protected_terms_enabled: bool = True
    protect_english_text: bool = False
    term_protector: Optional[TermProtector] = None
    glossary_snippet: str = ""
    retry_hint: str = ""  # Quality feedback from a prior failed attempt
    model_override: str = ""  # Use a different model for this request (QA fixes)
    context_note: str = ""  # NLDT developer context note from ESP (explains variables)

    def to_prompt(self, text: Optional[str] = None) -> str:
        """Generate the user-turn prompt.

        TranslateGemma was fine-tuned on the English Anchor format:
        ``"To {Language}:\n{text}"`` — the target language name must be the
        full display name (e.g. "Ukrainian"), not the locale code ("uk").
        """
        content = text if text is not None else self.original_text
        tgt_name = _LANG_DISPLAY.get(self.target_lang, self.target_lang)
        if self.retry_hint:
            return f"To {tgt_name}:\n{self.retry_hint}\n\nText to translate:\n{content}"
        return f"To {tgt_name}:\n{content}"

    def to_system_prompt(self) -> str:
        """Build a language-pair-aware system prompt.

        The prompt has three layers:
        1. A universal header + rules (token preservation, punctuation, …).
        2. A target-language style rule (register, script, terminology).
        3. Optional source-language / pair-specific notes + examples.
        """
        src_name = _LANG_DISPLAY.get(self.source_lang, self.source_lang)
        tgt_name = _LANG_DISPLAY.get(self.target_lang, self.target_lang)

        style_rule = _TARGET_STYLE.get(
            self.target_lang,
            f"Write natural, polished {tgt_name} appropriate to Starfield's "
            f"NASApunk sci-fi setting. Match the register: formal stays formal, "
            f"casual stays casual.",
        )

        base = (
            f"You are a professional Bethesda Starfield game localization translator.\n"
            f"Translate the {src_name} text to natural, polished {tgt_name}. "
            f"Output ONLY the translated text — no preamble, no notes, no commentary.\n\n"
            f"Rules:\n"
            f"1. {style_rule}\n"
            "2. Preserve ALL formatting tokens unchanged: <Alias=…>, <font>, "
            "[[TK_…]], [[STRUCT_BREAK_SGL_N]], [[STRUCT_BREAK_DBL_N]], "
            "\\n, \\t, %s, %d, #IDs. Never alter, split, or translate these.\n"
            "3. Square brackets [] — four categories:\n"
            "   a) TRANSLATE dialogue-choice and skill tokens (keep the brackets, translate the word inside): "
            "[Lie]→[Збрехати], [Flirt]→[Залицятися], [Persuade]→[Переконати], "
            "[Intimidation]→[Залякування], [Speech]→[Промова], [Commerce]→[Торгівля], "
            "[Diplomat]→[Дипломат], [Distraction]→[Відволікання], [Extortion]→[Вимагання], "
            "[Piracy]→[Піратство], [Romance]→[Романтика], [Lethal]→[Летально], "
            "[Friendship]→[Дружба], [Commitment]→[Відданість], [Evidence]→[Доказ], "
            "[Security]→[Безпека], [Engineering]→[Інженерія], [Soldier]→[Солдат], "
            "[Surveying]→[Дослідження], [Scavenging]→[Мародерство], "
            "[Xenobiologist]→[Ксенобіолог], [Industrialist]→[Промисловець], "
            "[Robotics]→[Роботехніка], [Cyberneticist]→[Кібернетик], "
            "[MedPack]→[Аптечка], [Chems]→[Стимулятори], [Syringe]→[Шприц], "
            "[Wave]→[Помах], [Strikers]→[Страйкери].\n"
            "   b) PRESERVE unchanged — input-binding tokens (map to keyboard/gamepad keys): "
            "[Attack], [AltAttack], [SecondaryAttack], [PrimaryAttack], [Jump], [Sprint], [Sneak], [Back], "
            "[Melee], [Hack], [Pick], [Repair], [Sort], [Left], [Right], [Up], [Down], [Forward], "
            "[Move], [Look], [Boosters], [Cruise], [Steady], "
            "[L3], [R3], [LShoulder], [RShoulder], [LTrigger], [RTrigger], [XButton], "
            "[LeftStick], [RightStick], [StrafeLeft], [StrafeRight], [TogglePOV], [ReadyWeapon], "
            "[QuickInventory], [SelectTarget], [NextTarget], [PrevTarget], [TakeOff], "
            "[Accept], [Activate], [Cancel], [Confirm], [Reject], [Submit], [Edit], [Exit], "
            "[Play], [Leave], [Add], [All], [Yes], [No], [Click], "
            "[DataMenu], [Monocle], [SHMonocle], [Crew], "
            "[VehicleAim], [VehicleBoost], [VehicleExit], [VehicleFireWeapon], [VehicleHorn], "
            "[VehicleVertBoost], [VehicleResetCamera], "
            "[ExecuteJump], [StarbornPower], [WeaponGroup1], [WeaponGroup2], [WeaponGroup3], "
            "[WeaponReadyReload], [Space], [Mouse2], "
            "[RepairShip], [ShipBuilder], [ShipTransaction], [FastTravelShip], [CargoHold], "
            "[ToggleTracking], [ToggleView], [TogglePrecise], [ChangeMode], "
            "[PlaceBeacon], [PlaceMarker], [RotateLock], [RotatePick], "
            "[QuickkeyDown], [Quickkey10], [StartWait], [ApplyCritical], "
            "and all similar single-action bindings.\n"
            "   c) PRESERVE unchanged — NPC name references and variable placeholders: "
            "[Andreja], [Vasco], [Hunter], [Sakharov], [Starborn], [Name], "
            "[MALE], [FEMALE], [PLYR], [Firstname], [Secondname].\n"
            "   d) TRANSLATE — ALL-CAPS status/document codes and sentence-case labels: "
            "[CANCELED], [CRITICAL], [DELETED], [DELIVERED], [EMPTIED], [OPTIMIZED], "
            "[PARTICULATES], [PINGED], [PING], [REDACTED], [VATS]; "
            "also: [Restored], [Decrypted], [Redacted], [Deleted], [Signed], "
            "[Optional], [Unfinished], [Temporary], [Unknown], [Maintenance], "
            "[Volcanic], [Forest], [Desert], [Albino], [Common].\n"
            "   e) TRANSLATE any bracket content that is multiple words or full sentences "
            "(publisher notes, editorial remarks, book summaries, narrative asides). "
            "Keep the opening [ and closing ] exactly as in the source; translate ALL "
            "text between them, preserving any paragraph breaks inside. "
            "Example: [The second part is over 700 pages long.]→"
            "[Друга частина має понад 700 сторінок.]\n"
            "4. Do NOT add quotes not present in the source.\n"
            "5. Preserve leading and trailing spaces exactly as in the source.\n"
            "6. Match source punctuation and capitalization exactly.\n"
            "7. Translate content inside parentheses () — do not leave it in the source language.\n"
        )

        # Source-language note (e.g. Russian needs a "don't transliterate" reminder).
        src_extra = _SOURCE_EXTRA.get(self.source_lang, "")
        pair_extra = _PAIR_EXTRA.get((self.source_lang, self.target_lang), "")
        note = " ".join(filter(None, [src_extra, pair_extra]))
        if note:
            base += f"\nNote: {note}\n"

        # Translation examples (if we have a specific pair or a generic one).
        examples = _LANG_EXAMPLES.get((self.source_lang, self.target_lang), "")
        if examples:
            base += f"\nExamples:\n{examples}"

        result = base
        if self.context_note:
            result += f"\nDeveloper context note (explains variables/usage): {self.context_note}"
        if self.glossary_snippet:
            result += "\nGlossary (use these exact translations):\n" + self.glossary_snippet
        if self.retry_hint:
            result += self.retry_hint
        return result


class OllamaWorker(QObject):
    """Worker object for AI translation calls with term protection."""

    translation_ready = Signal(int, str, int)
    progress = Signal(int, int)
    error = Signal(str)
    finished = Signal(int, int)

    # TranslateGemma3-ST configuration (optimized for speed)
    MODEL_CONFIGS = {
        "translategemma3-st": {
            "temperature": 0.1,
            "num_predict": 4096,
            "num_ctx": 16384,
            "top_k": 40,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
            "recommended_quality": 7,
            "stops": [
                "<end_of_turn>",
                "<start_of_turn>",
                "user:",
                "model:",
                "<|user|>",
                "<|model|>",
            ],
        },
        "translategemma3-st-2": {
            "temperature": 0.1,
            "num_predict": 512,   # game strings are short; 4096 wastes time on padding
            "num_ctx": 8192,      # halved from 16384 — reduces KV cache pressure on GPU
            "top_k": 40,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
            "recommended_quality": 7,
            # 2.5× larger GGUF than translategemma3-st (16 GB vs 6.5 GB) — GPU inference
            # is serialised so raising max_concurrent beyond 4 just adds queue wait time.
            "timeout": 480,
            "max_concurrent": 4,
            "stops": [
                "<end_of_turn>",
                "<start_of_turn>",
                "user:",
                "model:",
                "<|user|>",
                "<|model|>",
            ],
        },
        # Google TranslateGemma 27B IT — official Google translation-specialized model.
        # Uses the exact user-turn instruction format extracted from the GGUF's embedded
        # tokenizer.chat_template (no system turn; language pair hardcoded in TEMPLATE).

        # Gemma 4 IT family — full instruction-following, supports system prompts.
        # Thinking mode is disabled so <think>…</think> blocks don't leak into output.
        # num_predict 8192: thinking models may generate ~2000-3000 reasoning tokens
        # before the translation; 4096 is not enough for the full output.
        "gemma4": {
            "temperature": 0.1,
            "num_predict": 8192,
            "num_ctx": 16384,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
            "think_disabled": True,
            "recommended_quality": 7,
            "timeout": 600,  # thinking chains add ~2000-4000 tokens before the translation
            "stops": ["<end_of_turn>", "<start_of_turn>"],
        },
        # Gemma 4 12B IT fine-tuned on Claude Opus 4.6/4.7/4.8 reasoning distillation.
        # Higher top_k/top_p match the author's training distribution; temperature
        # is lowered from the author's 1.0 to 0.1 for deterministic translation output.
        "gemma4-opus48-st": {
            "temperature": 0.1,
            "num_predict": 8192,
            "num_ctx": 16384,
            "top_k": 64,
            "top_p": 0.95,
            "repeat_penalty": 1.1,
            "think_disabled": True,
            "recommended_quality": 7,
            "timeout": 600,
            "stops": ["<end_of_turn>", "<start_of_turn>"],
        },
    }

    # Safe fallback for models not in MODEL_CONFIGS — no model-specific stop tokens
    # so we never truncate output accidentally.
    _DEFAULT_MODEL_CONFIG: Dict[str, object] = {
        "temperature": 0.1,
        "num_predict": 4096,
        "num_ctx": 16384,
        "top_p": 0.9,
        "recommended_quality": 7,
    }

    # Quote characters that count as "source has quotes" — apostrophes excluded
    # because Ukrainian uses ' as a soft-sign separator (e.g. "м'ясо").
    _QUOTE_CHARS_RE = re.compile(r'[«»“”„‟‘’"]')

    # Guillemets wrapping any word or phrase (possibly with leading/trailing spaces)
    _INLINE_GUILLEMET_RE = re.compile(r'«\s*([^»\n]+?)\s*»')

    # Ukrainian-specific characters — present in Ukrainian but absent in Russian.
    # Used to detect source text that is already in the target language so we
    # can skip a pointless AI call during retranslation.
    _UK_SPECIFIC_RE = re.compile(r"[іїєґІЇЄҐ]")
    # Russian-only characters — present in Russian but absent in Ukrainian.
    _RU_SPECIFIC_RE = re.compile(r"[ыэёъЫЭЁЪ]")

    # Patterns that indicate the model refused to translate instead of translating.
    # Matched against the raw response before any cleaning.
    # Strings that should never be sent to the model — adapted from xTranslator's
    # lRulesNoTransListInDefault patterns.  Return original text immediately.
    _INPUT_NOTRANS_RE = re.compile(
        r"^[\W\d.]*$"                        # only non-word chars, digits, dots  (e.g. "---", "42.0")
        r"|^\w.*[/\\].*\.\w+$"               # backslash/forward-slash paths with extension  (e.g. Data\Interface\x.dds)
        r"|^<[\w.]+(?:=[\w.]+)?/?>$"        # pure single-tag string  (e.g. <Global.PlayerName>)
        r"|^[A-Za-z\d]{3,}_[A-Za-z\d_]+$"  # VARIABLE_LIKE_NAMES  (e.g. NPC_Boss01, ACTOR_JOHNDOE)
        r"|^\w+[A-Z]+[_a-z\d]+[A-Z]+\w+$"  # CamelCase identifiers  (e.g. PlayerActorRef)
        r"|^.{1,2}$"                         # 1-2 char strings — too short to translate meaningfully
        r"|^<[^>]+$",                        # unclosed tag — malformed, skip to avoid breakage
        re.UNICODE,
    )
    # Only applied when source language is Cyrillic (Russian/etc.) — English strings in a
    # Cyrillic file are dev notes or codes and should pass through unchanged.
    # Must NOT be applied when source_lang == "English" or every English string is skipped.
    _INPUT_NOTRANS_NOCYRILLIC_RE = re.compile(r"^[^Ѐ-ӿ]+$", re.UNICODE)

    _REFUSAL_RE = re.compile(
        r"^(?:"
        r"I(?:'m| am| will| cannot| can't| won't|'ll not)\s+(?:not\s+)?(?:translat|able to translat|going to translat|sorry)|"
        r"(?:This|The)\s+(?:text|string|content|phrase)\s+(?:does\s+not|doesn't|cannot|can't|need\s+not)\s+(?:need\s+)?translat|"
        r"not\s+translating|"
        r"no\s+translation\s+(?:needed|required)|"
        r"Не\s+(?:можу|буду|стану)\s+переклад|"
        r"Цей\s+текст\s+(?:вже|не\s+потребує|не\s+треба)|"
        r"Відмовляюся\s+переклад|"
        r"не\s+перекладаю"
        r")",
        re.IGNORECASE,
    )

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "translategemma3-st",
        enable_term_protection: bool = True,
        term_protector: Optional[TermProtector] = None,
        translation_cache: Optional[TranslationCache] = None,
        max_workers: int = 10,
        ollama_num_thread: int = 0,
        ollama_num_predict: int = 4096,
        ollama_num_ctx: int = 16384,
        long_string_threshold: int = 1000,
        long_string_action: str = "Translate",
        protect_named_entities: bool = False,
    ):
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.ollama_num_predict = ollama_num_predict
        self.ollama_num_ctx = ollama_num_ctx
        self.long_string_threshold = long_string_threshold
        self.long_string_action = long_string_action
        self._stop_flag = False
        self._mutex = QMutex()
        self.max_workers = max(1, max_workers)
        self.ollama_num_thread = ollama_num_thread

        # Holds the active ThreadPoolExecutor so stop() can cancel futures
        # even when translate_batch() is blocked inside as_completed().
        # Written only from the translate_batch thread; read from any thread
        # under self._mutex.
        self._executor: Optional[ThreadPoolExecutor] = None

        self._session = self._make_session()

        self.enable_term_protection = enable_term_protection
        self.protect_named_entities = protect_named_entities
        self.term_protector = term_protector
        self.translation_cache = translation_cache
        self.translation_memory: Optional[TranslationMemory] = None
        self.tm_fuzzy_max_score: float = 3.0
        self.glossary_manager: Optional[GlossaryManager] = None
        # StringType names to skip (e.g. ["BOOK", "NOTE"]). Set from AppSettings.
        self.skipped_types: list = []

        logger.info(
            f"OllamaWorker initialized: url={self.base_url}, model={self.model}, "
            f"term_protection={enable_term_protection}, max_workers={self.max_workers}, "
            f"cache={'enabled' if translation_cache else 'disabled'}"
        )
        # Preload word dictionaries in the background to avoid blocking the
        # first translation request on dictionary load time.
        _preload_ru_dict()
        _preload_en_dict()
        _preload_uk_dict()
        _preload_de_dict()
        _preload_es_dict()
        _preload_fr_dict()
        _preload_it_dict()
        _preload_pl_dict()
        _preload_ptbr_dict()

    def _get_model_config(self, model_name: Optional[str] = None) -> Dict[str, Any]:
        """Return the best-matching config for *model_name* (or the worker's model).

        Matching order:
        1. Exact key match (case-insensitive).
        2. Prefix match after stripping quantization suffix (``name:tag`` → ``name``).
        3. Gemma 4 family detection by substring.
        4. Safe _DEFAULT_MODEL_CONFIG — no model-specific stop tokens.
        """
        model = (model_name or self.model).strip()
        model_lower = model.lower()
        model_base = model_lower.split(":")[0]  # strip tag / quantization

        # 1. Exact match (case-insensitive)
        for key, cfg in self.MODEL_CONFIGS.items():
            if key.lower() == model_lower:
                return cfg  # type: ignore[return-value]

        # 2. Base-name prefix match — e.g. "qwen35:30b-q4_K_M" → "qwen35"
        for key, cfg in self.MODEL_CONFIGS.items():
            key_base = key.lower().split(":")[0]
            if (
                model_base == key_base
                or model_lower.startswith(key_base + ":")
                or model_lower.startswith(key_base + "-")
                or model_base.startswith(key_base + "-")
            ):
                return cfg  # type: ignore[return-value]

        # 3. Safe default — no aggressive stop tokens
        return self._DEFAULT_MODEL_CONFIG  # type: ignore[return-value]

    def _make_session(self) -> requests.Session:
        """Create a fresh requests.Session with the current max_workers pool size."""
        session = requests.Session()
        session.timeout = 300  # pyright: ignore[reportAttributeAccessIssue]
        pool_size = max(20, self.max_workers + 5)
        adapter = requests.adapters.HTTPAdapter(  # pyright: ignore[reportAttributeAccessIssue]
            pool_connections=pool_size,
            pool_maxsize=pool_size,
            max_retries=3,
            pool_block=False,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    @Slot(list)
    def translate_batch(self, requests: list):
        """Process a batch of translation requests with parallel processing for maximum speed."""
        if not requests:
            self.finished.emit(0, 0)
            return

        # ── Reset state from any previous run ────────────────────────────────
        with QMutexLocker(self._mutex):
            self._stop_flag = False
        self._session = self._make_session()

        total = len(requests)
        successful = 0
        failed = 0
        completed_count = 0

        # ── Pre-flight scan ───────────────────────────────────────────────────
        # Resolve every request that can be answered without calling Ollama:
        # no-trans patterns, exact TM hits, and cache hits.  Each hit skips a
        # thread, an HTTP connection, and an Ollama queue slot entirely.
        #
        # Source deduplication: when the same source text appears multiple times
        # in the batch (common UI labels, item names), only one goes to Ollama;
        # the result fans out to all copies when it arrives.
        #
        # followers maps dedup_key → [follower TranslationRequests].  The first
        # occurrence is added to `pending`; subsequent ones are followers.
        pending: list = []
        followers: Dict[tuple, list] = {}

        for req in requests:
            with QMutexLocker(self._mutex):
                if self._stop_flag:
                    self.finished.emit(0, 0)
                    return

            # No-trans: pure punctuation, paths, identifiers, 1-2 char strings
            stripped = req.original_text.strip() if req.original_text else ""
            is_notrans = (
                not stripped
                or self._INPUT_NOTRANS_RE.fullmatch(stripped)
                or (
                    req.source_lang != "en"
                    and self._INPUT_NOTRANS_NOCYRILLIC_RE.fullmatch(stripped)
                )
            )
            if is_notrans:
                self.translation_ready.emit(req.index, req.original_text, req.string_id)
                successful += 1
                completed_count += 1
                self.progress.emit(completed_count, total)
                continue

            is_retry = bool(req.retry_hint)

            # Exact TM lookup (O(1) dict reads — no fuzzy here to keep pre-flight fast)
            if not is_retry and self.translation_memory:
                mem_hit = (
                    self.translation_memory.get_by_id(req.string_id)
                    or self.translation_memory.get_by_source(req.original_text)
                )
                if mem_hit:
                    self.translation_ready.emit(req.index, mem_hit, req.string_id)
                    successful += 1
                    completed_count += 1
                    self.progress.emit(completed_count, total)
                    continue

            # Cache lookup
            if not is_retry and self.translation_cache:
                cache_key = TranslationCache.make_key(
                    req.original_text, self.model, req.source_lang, req.target_lang
                )
                cached = self.translation_cache.get(cache_key)
                if cached:
                    self.translation_ready.emit(req.index, cached, req.string_id)
                    successful += 1
                    completed_count += 1
                    self.progress.emit(completed_count, total)
                    continue

            # Deduplication: retries always go through (they need fresh results)
            if not is_retry:
                dedup_key = (req.original_text, req.source_lang, req.target_lang)
                if dedup_key in followers:
                    followers[dedup_key].append(req)
                    continue
                followers[dedup_key] = []

            pending.append(req)

        if not pending:
            logger.info(
                "Batch complete (all %d resolved pre-flight): %d hits, %d failed",
                total, successful, failed,
            )
            self.finished.emit(successful, failed)
            return

        # Sort short strings first: they complete faster, keeping Ollama's queue
        # saturated with quick completions before tackling long dialogue strings.
        pending.sort(key=lambda r: len(r.original_text))

        # Per-model concurrency cap.
        # When every pending request uses the same model_override (e.g. a QA
        # retranslation batch), use that model's config rather than the main model's.
        override_models = {r.model_override for r in pending if r.model_override}
        batch_model = override_models.pop() if len(override_models) == 1 else None
        model_config = self._get_model_config(batch_model or self.model)
        model_max_concurrent = int(model_config.get("max_concurrent") or self.max_workers)
        effective_workers = min(self.max_workers, model_max_concurrent)

        dedup_count = sum(len(v) for v in followers.values())
        preflight_hits = completed_count
        logger.info(
            "Starting batch: %d total → %d to Ollama, %d pre-flight hits, "
            "%d dedup followers | model=%s workers=%d",
            total, len(pending), preflight_hits, dedup_count,
            self.model, effective_workers,
        )

        executor = ThreadPoolExecutor(max_workers=effective_workers)
        with QMutexLocker(self._mutex):
            self._executor = executor

        try:
            future_to_req: Dict[Future, TranslationRequest] = {
                executor.submit(self._translate_single, req): req for req in pending
            }

            for future in as_completed(future_to_req):
                with QMutexLocker(self._mutex):
                    stopping = self._stop_flag

                if stopping:
                    logger.info(
                        "Translation stopped by user — cancelling %d pending futures",
                        sum(1 for f in future_to_req if not f.done()),
                    )
                    for f in future_to_req:
                        f.cancel()
                    break

                req = future_to_req[future]
                dedup_key = (req.original_text, req.source_lang, req.target_lang)
                req_followers = followers.get(dedup_key, [])

                try:
                    translated = future.result()
                    if translated == SKIP_SIGNAL:
                        # Primary skipped; followers also skip
                        completed_count += 1 + len(req_followers)
                        self.progress.emit(completed_count, total)
                    elif translated:
                        self.translation_ready.emit(req.index, translated, req.string_id)
                        successful += 1
                        completed_count += 1
                        self.progress.emit(completed_count, total)
                        # Fan result out to all dedup followers
                        for follower in req_followers:
                            self.translation_ready.emit(
                                follower.index, translated, follower.string_id
                            )
                            successful += 1
                            completed_count += 1
                            self.progress.emit(completed_count, total)
                    else:
                        failed += 1 + len(req_followers)
                        completed_count += 1 + len(req_followers)
                        self.error.emit(f"Empty response for string {req.string_id}")
                        self.progress.emit(completed_count, total)
                except Exception as e:
                    failed += 1 + len(req_followers)
                    completed_count += 1 + len(req_followers)
                    error_msg = f"Failed to translate string {req.string_id}: {e}"
                    logger.error(error_msg, exc_info=True)
                    self.error.emit(error_msg)
                    self.progress.emit(completed_count, total)

        except Exception as exc:
            logger.error("Unexpected error in translate_batch: %s", exc, exc_info=True)
            self.error.emit(f"Translation batch failed unexpectedly: {exc}")

        finally:
            with QMutexLocker(self._mutex):
                self._executor = None
            executor.shutdown(wait=True, cancel_futures=True)
            logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
            logger.info("Batch complete: %d successful, %d failed", successful, failed)
            self.finished.emit(successful, failed)

    # ── Chunked translation for very long strings ─────────────────────────────

    # Texts below this threshold are sent to the model in a single call.
    # Fine-tuned models (translategemma3-st) produce garbage when given fragments
    # with the "Part X of Y" hint — they were trained on complete strings.
    # 12 000 chars comfortably fits in one 16 384-token ctx call (the app default).
    _CHUNK_TRANSLATE_THRESHOLD = 12000  # chars — above this, split automatically
    _MAX_CHUNK_CHARS           = 4000   # max chars per chunk (for truly large texts)
    _CHUNK_TIMEOUT             = 180    # seconds per-chunk (vs 300 global)

    @staticmethod
    def _split_text_into_chunks(text: str, max_chars: int) -> list:
        """Split text at natural paragraph / line / sentence boundaries.

        Supports both raw newlines and the structural-break tokens produced
        by newline tokenization (``[[STRUCT_BREAK_DBL_N]]`` / ``[[STRUCT_BREAK_SGL_N]]``).
        """
        if len(text) <= max_chars:
            return [text]
        chunks: list = []
        remaining = text
        while len(remaining) > max_chars:
            pos = remaining.rfind("[[STRUCT_BREAK_DBL_N]]", 0, max_chars)
            if pos == -1:
                pos = remaining.rfind("\n\n", 0, max_chars)
            if pos == -1:
                pos = remaining.rfind("[[STRUCT_BREAK_SGL_N]]", 0, max_chars)
            if pos == -1:
                pos = remaining.rfind("\n", 0, max_chars)
            if pos == -1:
                for delim in (". ", "! ", "? ", ".\n", "!\n", "?\n"):
                    p = remaining.rfind(delim, 0, max_chars)
                    if p != -1:
                        pos = p + 1  # keep delimiter, split after it
                        break
            if pos <= 0:
                pos = max_chars
            chunks.append(remaining[:pos])
            remaining = remaining[pos:]
        if remaining.strip():
            chunks.append(remaining)
        return chunks

    def _call_ollama_chunk(
        self,
        req: TranslationRequest,
        chunk_text: str,
        chunk_num: int,
        total_chunks: int,
    ) -> Optional[str]:
        """Send a single chunk to Ollama with a per-chunk timeout."""
        url = f"{self.base_url}/api/generate"
        effective_model = req.model_override or self.model
        model_config = self._get_model_config(effective_model)

        input_len = len(chunk_text)
        # Add ~1 200-token budget for the system prompt so the ctx calculation
        # does not undercount and cause mid-word generation cutoffs.
        estimated_tokens = input_len // 3 + 400 + 1200
        think_ctx_extra = max(2000, input_len // 2) if model_config.get("think_disabled") else 0
        required_ctx = estimated_tokens * 2 + think_ctx_extra + 512
        for _ctx in (8192, 16384, 32768):
            if required_ctx <= _ctx:
                adaptive_num_ctx = _ctx
                break
        else:
            adaptive_num_ctx = 32768
        model_max_ctx = int(model_config.get("num_ctx") or 32768)  # type: ignore[arg-type]
        user_max_ctx  = max(self.ollama_num_ctx, 4096)
        effective_num_ctx = max(8192, min(adaptive_num_ctx, model_max_ctx, user_max_ctx))

        # Use ×4 (matching the non-chunked path); add thinking overhead for
        # models that may generate chain-of-thought tokens before the translation.
        model_min_predict = int(model_config.get("num_predict") or 0)
        think_extra = max(2000, input_len // 2) if model_config.get("think_disabled") else 0
        adaptive_num_predict = max(
            model_min_predict + think_extra,
            min(self.ollama_num_predict, max(512, input_len * 4)) + think_extra,
        )

        # TranslateGemma models embed the language-pair instruction in the TEMPLATE;
        # the payload must carry raw source text only (no "To Ukrainian:" prefix).
        # Never inject the chunk hint into prompt_text — the model would translate it.
        if model_config.get("raw_prompt"):
            effective_prompt = chunk_text
            effective_system = ""
        else:
            effective_prompt = req.to_prompt(chunk_text)
            effective_system = req.to_system_prompt()
            # For instruction-following models, put the chunk hint in the system prompt
            # so it is treated as a meta-instruction, not as text to translate.
            if total_chunks > 1:
                effective_system += (
                    f"\n\nNote: You are translating part {chunk_num} of {total_chunks} "
                    "of a longer text. Translate only the text in the user message; "
                    "do not include this note in your output."
                )

        payload = {
            "model":  effective_model,
            "prompt": effective_prompt,
            "system": effective_system,
            "stream": False,
            "keep_alive": -1,
            "options": {
                "temperature": model_config.get("temperature", 0.1)
                               if req.quality_level >= 7 else 0.3,
                "num_predict": adaptive_num_predict,
                "num_ctx":     effective_num_ctx,
            },
        }
        for opt in ["top_p", "top_k", "min_p", "repeat_penalty", "repeat_last_n"]:
            if opt in model_config:
                payload["options"][opt] = model_config[opt]
        if "stops" in model_config:
            payload["options"]["stop"] = model_config["stops"]
        if model_config.get("think_disabled"):
            payload["think"] = False
        if self.ollama_num_thread > 0:
            payload["options"]["num_thread"] = self.ollama_num_thread

        with QMutexLocker(self._mutex):
            if self._stop_flag:
                return None

        _chunk_timeout = self._CHUNK_TIMEOUT
        if model_config.get("think_disabled"):
            _chunk_timeout = max(_chunk_timeout, 300 + input_len // 20)
        response = self._session.post(url, json=payload, timeout=_chunk_timeout)
        if response.status_code == 404:
            raise Exception(
                f"Model '{self.model}' not found in Ollama. "
                f"Install it with:\n  ollama create {self.model} -f Modelfile.{self.model}"
            )
        response.raise_for_status()
        result = response.json().get("response", "").strip()
        # Strip any leaked chunk-marker the model might have echoed at the start
        # (e.g. "[Part 2/3 of a longer text]" or its Ukrainian equivalent).
        if total_chunks > 1 and result:
            result = re.sub(
                r"^\[(?:Part\s+\d+/\d+[^\]]*|\S+\s+\d+/\d+[^\]]*)\]\s*",
                "",
                result,
            )
        return result or None

    def _translate_chunked(
        self,
        req: TranslationRequest,
        protected_text: str,
        token_map: dict,
        cache_key: Optional[str],
    ) -> Optional[str]:
        """Translate a long string by splitting it into chunks and reassembling."""
        chunks = self._split_text_into_chunks(protected_text, self._MAX_CHUNK_CHARS)
        total  = len(chunks)
        logger.info(
            f"String {req.string_id}: chunked translation — {len(protected_text)} chars "
            f"split into {total} chunk(s)"
        )

        # The splitter puts the paragraph delimiter at the START of the next chunk
        # (e.g. remaining[pos:] starts with "\n\n" or "[[STRUCT_BREAK_DBL_N]]").
        # _clean_translation strips leading whitespace, so "".join() would silently
        # collapse every paragraph boundary.  Peel each leading delimiter, translate
        # the bare content, then reattach the delimiter when joining.
        _SEP_TOKENS = (
            "[[STRUCT_BREAK_DBL_N]]",
            "[[STRUCT_BREAK_SGL_N]]",
            "\n\n",
            "\n",
        )
        chunk_prefixes: list = []
        chunk_contents: list = []
        for chunk in chunks:
            prefix = ""
            content = chunk
            for sep in _SEP_TOKENS:
                if content.startswith(sep):
                    prefix = sep
                    content = content[len(sep):]
                    break
            chunk_prefixes.append(prefix)
            chunk_contents.append(content)

        translated_chunks: list = []
        for i, (chunk, prefix) in enumerate(zip(chunk_contents, chunk_prefixes), 1):
            with QMutexLocker(self._mutex):
                if self._stop_flag:
                    return None
            try:
                t = self._call_ollama_chunk(req, chunk, i, total)
            except requests.exceptions.Timeout:
                raise Exception(
                    f"Chunk {i}/{total} timed out (>{self._CHUNK_TIMEOUT}s) for "
                    f"string {req.string_id}. Try reducing the chunk size."
                )
            if t is None:
                logger.warning(
                    f"String {req.string_id}: empty chunk {i}/{total}, using original chunk"
                )
                t = chunk  # fall back to original for this chunk
            # Reattach the prefix that was stripped before translation so paragraph
            # boundaries survive the _clean_translation .strip() call.
            translated_chunks.append(prefix + t)

        result = "".join(translated_chunks)

        # Restore protected terms across the whole reassembled text
        if token_map and self.term_protector is not None:
            result = self.term_protector.restore_text(result, token_map, protected_text)
        elif token_map:
            if "[[STRUCT_BREAK_DBL_N]]" in result:
                result = result.replace("[[STRUCT_BREAK_DBL_N]]", "\n\n")
            if "[[STRUCT_BREAK_SGL_N]]" in result:
                result = result.replace("[[STRUCT_BREAK_SGL_N]]", "\n")

        if result:
            result = self._clean_translation(result, req.target_lang, req.original_text, req.string_id)

        if result and cache_key and self.translation_cache is not None:
            self.translation_cache.set(cache_key, result)

        return result or None

    def _translate_single(self, req: TranslationRequest) -> Optional[str]:
        """Translate a single string with term protection and optional cache lookup."""
        # Respect stop flag before doing any work
        with QMutexLocker(self._mutex):
            if self._stop_flag:
                return None

        if not req.original_text or not req.original_text.strip():
            return None

        # Skip strings that are clearly non-translatable (xTranslator NoTrans patterns):
        # pure punctuation/numbers, file paths, or a single bare tag.
        stripped = req.original_text.strip()
        if self._INPUT_NOTRANS_RE.fullmatch(stripped) or (
            req.source_lang != "en"
            and self._INPUT_NOTRANS_NOCYRILLIC_RE.fullmatch(stripped)
        ):
            logger.debug(f"String {req.string_id}: matches NoTrans pattern, returning original")
            return req.original_text

        # For retranslation of strings already in the target language: if the source
        # text contains Ukrainian-specific chars (і/ї/є/ґ) and no Russian-only chars
        # (ы/э/ё/ъ), it is already Ukrainian — the AI cannot usefully "translate" it
        # and will return empty or a refusal.  Return SKIP_SIGNAL so the string keeps
        # its current translation and mechanical fixes (newlines, whitespace) are used.
        is_retry = bool(req.retry_hint)
        if is_retry and req.target_lang.lower() == "ukrainian":
            if self._UK_SPECIFIC_RE.search(stripped) and not self._RU_SPECIFIC_RE.search(stripped):
                logger.info(
                    "String %s: source already in Ukrainian — skipping AI retranslation "
                    "(use Auto-Fix for mechanical issues)", req.string_id
                )
                return SKIP_SIGNAL

        # Skip strings whose content type is in the configured skipped list.
        # string_id==-1 means this is a paragraph sub-request; skip the check
        # because the outer string was already allowed through.
        if self.skipped_types and req.string_id != -1:
            from gui.string_type_detector import classify
            _stype = classify(req.original_text)
            if _stype.name in self.skipped_types:
                logger.debug(
                    "String %s: type %s is in skipped list, skipping",
                    req.string_id, _stype.name,
                )
                return SKIP_SIGNAL

        # Retranslation requests (retry_hint is set) must reach the AI — skip
        # memory and cache so the previous bad result is never returned.
        # (is_retry is already set above)

        # Check translation memory first — exact hits skip cache and Ollama entirely.
        # Bypassed for retranslations: the memory may hold the same flawed translation.
        if not is_retry and self.translation_memory:
            mem_hit = self.translation_memory.get_by_id(req.string_id)
            if mem_hit is None:
                mem_hit = self.translation_memory.get_by_source(req.original_text)
            if mem_hit is None:
                mem_hit = self.translation_memory.get_fuzzy(
                    req.original_text, max_score=self.tm_fuzzy_max_score
                )
                if mem_hit is not None:
                    logger.debug(f"Memory fuzzy-hit for string {req.string_id}")
            if mem_hit is not None:
                logger.debug(f"Memory hit for string {req.string_id}")
                return mem_hit

        effective_model = req.model_override or self.model
        if self.translation_cache is not None:
            cache_key = TranslationCache.make_key(
                req.original_text, effective_model, req.source_lang, req.target_lang
            )
            if not is_retry:
                cached = self.translation_cache.get(cache_key)
                if cached is not None:
                    logger.debug(f"Cache hit for string {req.string_id}")
                    return cached
            else:
                # Evict the stale (bad) entry so the fresh result replaces it.
                self.translation_cache.delete(cache_key)
                logger.debug(f"Retranslation: evicted cache entry for string {req.string_id}")
        else:
            cache_key = None

        # Compute glossary snippet on the worker thread when not pre-supplied.
        # This keeps the main-thread request-building loop free of 20K-entry searches.
        if not req.glossary_snippet and self.glossary_manager is not None:
            req = req  # don't mutate the original dataclass
            snippet = self.glossary_manager.build_prompt_snippet(req.original_text)
            if snippet:
                from dataclasses import replace as _dc_replace
                req = _dc_replace(req, glossary_snippet=snippet)

        # Strip leading/trailing separator lines from the source text before sending
        # to the model so it doesn't echo them back or get confused by them.
        # Separators are stored and restored around the final translation.
        leading_seps, inner_text, trailing_seps = self._extract_separators(req.original_text)
        if leading_seps or trailing_seps:
            from dataclasses import replace as _dc_replace_sep
            req = _dc_replace_sep(req, original_text=inner_text)

        # Normalize CRLF/CR → LF before any tokenization.  Leaving \r in the text
        # causes the structural newline tokenizer to produce \r[[STRUCT_BREAK_SGL_N]],
        # which the model echoes as \r or drops, producing mismatched newline counts.
        protected_text = req.original_text.replace("\r\n", "\n").replace("\r", "\n")

        # Paragraph-by-paragraph translation: split on \n\n and translate each
        # paragraph independently so the app—not the model—controls paragraph
        # structure.  Eliminates reliance on the model preserving
        # [[STRUCT_BREAK_DBL_N]] tokens, which it routinely ignores.
        if "\n\n" in protected_text:
            _pp_segs = protected_text.split("\n\n")
            if sum(1 for _s in _pp_segs if _s.strip()) >= 2:
                from dataclasses import replace as _dc_pp
                _pp_results: list = []
                for _seg in _pp_segs:
                    if not _seg.strip():
                        _pp_results.append(_seg)
                        continue
                    _pp_req = _dc_pp(req, original_text=_seg, string_id=-1)
                    _pp_trans = self._translate_single(_pp_req)
                    _pp_results.append(_pp_trans if _pp_trans else _seg)
                _pp_result = "\n\n".join(_pp_results)
                _pp_result = leading_seps + _pp_result + trailing_seps
                if cache_key and self.translation_cache is not None:
                    self.translation_cache.set(cache_key, _pp_result)
                return _pp_result

        english_token_map = {}

        # Disable English protection if source is English
        should_protect_english = (
            req.protect_english_text and req.source_lang != "en"
        )

        if should_protect_english:
            protected_text, english_token_map = self._protect_english_text(
                protected_text
            )
            if english_token_map:
                logger.debug(
                    f"Protected {len(english_token_map)} English segment(s) in string {req.string_id}"
                )

        # Protect terms if enabled
        token_map = {}

        if (
            self.enable_term_protection
            and self.term_protector
            and req.protected_terms_enabled
        ):
            from gui.term_protector import SOFT_CATEGORIES
            exclude = [] if self.protect_named_entities else list(SOFT_CATEGORIES)

            protected_text, token_map = self.term_protector.protect_text(
                protected_text, exclude_categories=exclude
            )
            if token_map:
                logger.debug(
                    f"Protected {len(token_map)} terms in string {req.string_id}"
                )

        # Merge token maps
        token_map.update(english_token_map)

        # Tokenize structural newlines so the AI cannot collapse paragraph breaks.
        # Must happen after term protection so \\n escape sequences (already protected
        # as "newline" tokens) are not confused with actual newline characters.
        # The tokens are added to token_map so restore_text() restores them
        # automatically via the same anchor-based mechanism used for all other tokens.
        _DBL_NL = "[[STRUCT_BREAK_DBL_N]]"
        _SGL_NL = "[[STRUCT_BREAK_SGL_N]]"
        if "\n" in protected_text:
            protected_text = protected_text.replace("\n\n", _DBL_NL).replace("\n", _SGL_NL)
            token_map[_DBL_NL] = "\n\n"
            token_map[_SGL_NL] = "\n"

        # Check for long strings and apply configured action
        if len(protected_text) > self.long_string_threshold:
            if self.long_string_action == "Skip":
                logger.info(
                    f"String {req.string_id} exceeds threshold ({len(protected_text)} > {self.long_string_threshold}), skipping."
                )
                return SKIP_SIGNAL
            elif self.long_string_action == "Original":
                logger.info(
                    f"String {req.string_id} exceeds threshold ({len(protected_text)} > {self.long_string_threshold}), returning original."
                )
                return req.original_text

        # Very long strings (above chunk threshold) are split into chunks so each
        # Ollama call stays well within the per-request timeout.
        if len(protected_text) > self._CHUNK_TRANSLATE_THRESHOLD:
            translated = self._translate_chunked(req, protected_text, token_map, cache_key)
            if translated:
                # Restore structural newlines dropped by the model — same fallback
                # used in the non-chunked path but applied to the reassembled result.
                if token_map.get("[[STRUCT_BREAK_SGL_N]]") or token_map.get("[[STRUCT_BREAK_DBL_N]]"):
                    if translated.count("\n") < req.original_text.count("\n"):
                        translated = _restore_line_structure(translated, req.original_text)
                # Restore separator lines stripped before chunking.
                if leading_seps or trailing_seps:
                    translated = leading_seps + translated + trailing_seps
            return translated

        # OPTIMIZATION: If the entire text was protected (nothing left to translate),
        # skip the API entirely and return the original text.
        if token_map:
            # Remove all tokens from protected text
            remaining = protected_text
            for token in token_map:
                remaining = remaining.replace(token, "")
            # Check if only punctuation/whitespace remains (no actual text to translate)
            if not remaining.strip() or not any(c.isalnum() for c in remaining):
                # All content was protected → nothing to translate
                logger.debug(
                    f"String {req.string_id}: All content protected, returning original"
                )
                return req.original_text

        # Call AI API
        try:
            input_len = len(protected_text)
            url = f"{self.base_url}/api/generate"
            model_config = self._get_model_config(effective_model)

            # Adaptive num_predict: use input length as base, but never go below the
            # model config's num_predict (thinking models need extra budget for the
            # reasoning block that precedes the actual translation output).
            model_min_predict = int(model_config.get("num_predict") or 0)
            # Thinking-capable models (think_disabled=True means the model supports
            # chain-of-thought but we request think=false) may still generate internal
            # reasoning tokens before the translation — consuming num_predict budget.
            # Add an overhead so the full translation is never truncated mid-sentence.
            think_extra = max(2000, input_len // 2) if model_config.get("think_disabled") else 0
            adaptive_num_predict = max(
                model_min_predict + think_extra,
                min(self.ollama_num_predict, max(100, input_len * 4)) + think_extra,
            )

            # Per-model timeout: slow models (e.g. supergemma4-26b) need more than 300s
            # because the AMD GPU serialises requests — a queued string may wait several
            # minutes just for its GPU slot before generation even starts.
            model_timeout = int(model_config.get("timeout") or self._session.timeout)  # type: ignore[arg-type]
            # Thinking models generate a reasoning chain before the translation output;
            # scale timeout with text length so large strings don't time out mid-think.
            if model_config.get("think_disabled"):
                model_timeout += max(0, len(protected_text) // 20)

            # Adaptive num_ctx: allocate only what this string needs.
            # Cyrillic/Latin text ≈ 3 chars/token; add 1 200 tokens for the system
            # prompt (instructions + glossary) so we never undercount and truncate.
            estimated_tokens = len(protected_text) // 3 + 400 + 1200
            # For thinking models, the context must also hold chain-of-thought tokens
            # generated before the translation. Add an explicit budget for this.
            think_ctx_extra = max(2000, len(protected_text) // 2) if model_config.get("think_disabled") else 0
            required_ctx = estimated_tokens * 2 + think_ctx_extra + 512  # ×2 for output + buffer
            for _ctx in (4096, 8192, 16384, 32768):
                if required_ctx <= _ctx:
                    adaptive_num_ctx = _ctx
                    break
            else:
                adaptive_num_ctx = 32768
            # model config's num_ctx is the architecture ceiling; user limit further constrains.
            model_max_ctx = int(model_config.get("num_ctx") or 32768)  # type: ignore[arg-type]
            user_max_ctx = max(self.ollama_num_ctx, 4096)
            effective_num_ctx = max(4096, min(adaptive_num_ctx, model_max_ctx, user_max_ctx))

            # TranslateGemma models embed the language-pair instruction in the TEMPLATE;
            # the payload must carry raw source text only (no "To Ukrainian:" prefix).
            if model_config.get("raw_prompt"):
                effective_prompt = protected_text
                effective_system = ""
            else:
                effective_prompt = req.to_prompt(protected_text)
                effective_system = req.to_system_prompt()

            payload = {
                "model": effective_model,
                "prompt": effective_prompt,
                "system": effective_system,
                "stream": False,
                "keep_alive": -1,
                "options": {
                    "temperature": model_config.get("temperature", 0.1)
                    if req.quality_level >= 7
                    else 0.3,
                    "num_predict": adaptive_num_predict,
                    "num_ctx": effective_num_ctx,
                },
            }
            for opt in ["top_p", "top_k", "repeat_penalty", "min_p", "repeat_last_n"]:
                if opt in model_config:
                    payload["options"][opt] = model_config[opt]
            if "stops" in model_config:
                payload["options"]["stop"] = model_config["stops"]
            if model_config.get("think_disabled"):
                payload["think"] = False
            if self.ollama_num_thread > 0:
                payload["options"]["num_thread"] = self.ollama_num_thread

            # Last chance to bail before blocking on the network
            with QMutexLocker(self._mutex):
                if self._stop_flag:
                    return None

            response = self._session.post(url, json=payload, timeout=model_timeout)
            response.raise_for_status()
            translated = response.json().get("response", "").strip()
            # Retry 1: higher temperature — Gemma at 0.1 sometimes emits <eos> immediately.
            if not translated:
                logger.warning(
                    f"String {req.string_id}: Empty response from Ollama, "
                    f"retrying at temperature 0.3"
                )
                with QMutexLocker(self._mutex):
                    if self._stop_flag:
                        return None
                payload["options"]["temperature"] = 0.3
                payload["options"]["num_predict"] = min(
                    self.ollama_num_predict, max(200, input_len * 6)
                )
                response = self._session.post(url, json=payload, timeout=model_timeout)
                response.raise_for_status()
                translated = response.json().get("response", "").strip()

            # Retry 2: strip stop tokens entirely — in rare cases a stop token is the
            # very first output, making the model look silent even at T=0.3.
            if not translated:
                logger.warning(
                    f"String {req.string_id}: Still empty after T=0.3 retry, "
                    f"removing stop tokens for final attempt"
                )
                with QMutexLocker(self._mutex):
                    if self._stop_flag:
                        return None
                payload["options"].pop("stop", None)
                payload["options"]["temperature"] = 0.5
                payload["options"]["num_predict"] = min(
                    self.ollama_num_predict, max(400, input_len * 8)
                )
                response = self._session.post(url, json=payload, timeout=model_timeout)
                response.raise_for_status()
                translated = response.json().get("response", "").strip()

            if not translated:
                logger.debug(
                    f"String {req.string_id}: Empty translation from Ollama after all retries"
                )
                return None

            # Detect model refusals — model said it won't translate instead of translating.
            # Common with general-purpose Gemma 4 models that add explanatory commentary.
            if self._REFUSAL_RE.search(translated):
                logger.warning(
                    f"String {req.string_id}: Model refused to translate "
                    f"({translated[:80]!r}…) — treating as untranslated"
                )
                return None

            # Restore protected terms
            if token_map and self.term_protector is not None:
                translated = self.term_protector.restore_text(
                    translated, token_map, protected_text
                )
            elif token_map:
                # term_protector disabled — restore STRUCT_BREAK tokens manually.
                if "[[STRUCT_BREAK_DBL_N]]" in translated:
                    translated = translated.replace("[[STRUCT_BREAK_DBL_N]]", "\n\n")
                if "[[STRUCT_BREAK_SGL_N]]" in translated:
                    translated = translated.replace("[[STRUCT_BREAK_SGL_N]]", "\n")

            # Fix AI-garbled STRUCT_BREAK token names before counting newlines.
            # Models sometimes double the STRUCT_ prefix, e.g. [[STRUCT_STRUCT_BREAK_DBL_N]].
            # Must run here so _restore_line_structure sees the correct \n count.
            # Comprehensive cleanup of leaked/garbled [[...]] tokens.
            # Must run before _restore_line_structure so newline counts are accurate.
            if translated:
                # DBL/DOUBLE STRUCT_BREAK variants → \n\n
                # Catches: [[STRUCT__BREAK_DBL_N]], [[STRUCT_BREAK_DOUBLE_NEWLINE]], etc.
                if token_map.get("[[STRUCT_BREAK_DBL_N]]"):
                    translated = re.sub(
                        r'\[\[STRUCT\w*(?:DBL|DOUBLE)\w*\]{2,}', '\n\n',
                        translated, flags=re.IGNORECASE
                    )
                # SGL/SINGLE STRUCT_BREAK variants → \n
                # Catches: [[STRUCT_SGL_N]]], [[STRUCT__BREAK_SGL_N]], etc.
                if token_map.get("[[STRUCT_BREAK_SGL_N]]"):
                    translated = re.sub(
                        r'\[\[STRUCT\w*(?:SGL|SINGLE)\w*\]{2,}', '\n',
                        translated, flags=re.IGNORECASE
                    )
                # Any remaining [[STRUCT_*]] variants (e.g. [[STRUCT_REDACTED]]) → \n
                if token_map.get("[[STRUCT_BREAK_DBL_N]]") or token_map.get("[[STRUCT_BREAK_SGL_N]]"):
                    translated = re.sub(
                        r'\[\[STRUCT\w+\]{2,}', '\n', translated, flags=re.IGNORECASE
                    )
                # Restore any token_map tokens missed by restore_text() (e.g. leaked [[TK_*]])
                if token_map:
                    for _t, _v in list(token_map.items()):
                        if _t in translated:
                            translated = translated.replace(_t, _v)
                # Strip remaining [[...]] artifacts hallucinated by the model
                translated = re.sub(r'\[\[\w+\]{2,}', '', translated)

            # Post-translation: sub-translate any bracket spans still in English.
            # The model often preserves [multi-paragraph publisher/author notes] verbatim
            # because STRUCT_BREAK tokens inside them look like formatting tokens (rule 2).
            # After restore_text() the bracket inner content has actual terms (not [[TK_*]])
            # so the sub-call's term protection and STRUCT_BREAK work correctly.
            # Scoped to EN→UK; inner must have a newline (multi-paragraph) and no Cyrillic.
            if translated and req.source_lang == "en" and req.target_lang.lower() in ("uk", "ukrainian"):
                _post_bk_re = re.compile(r'\[((?:[^\[\]]|\[\[[^\[\]]*\]\])*)\]', re.DOTALL)

                def _retranslate_if_english(m: re.Match) -> str:  # type: ignore[type-arg]
                    inner = m.group(1)
                    if "\n" not in inner:
                        return m.group(0)  # single-line bracket — leave it
                    _cyr = sum(1 for c in inner if "Ѐ" <= c <= "ӿ")
                    _lat = sum(1 for c in inner if c.isalpha() and c.isascii())
                    if _cyr > 0 or _lat < 20:
                        return m.group(0)  # already translated or too short
                    from dataclasses import replace as _dc_replace_bk
                    _bk_req = _dc_replace_bk(
                        req, original_text=inner.strip(), retry_hint="", string_id=-1
                    )
                    _bk_trans = self._translate_single(_bk_req)
                    if _bk_trans and _bk_trans.strip():
                        return f"[{_bk_trans.strip()}]"
                    return m.group(0)

                translated = _post_bk_re.sub(_retranslate_if_english, translated)

            # Restore structural newlines + per-line leading spaces that the model
            # dropped (translategemma3-st routinely ignores [[STRUCT_BREAK_*]] tokens).
            # Only runs when those tokens were injected AND the output is short on \n.
            if translated and (
                token_map.get("[[STRUCT_BREAK_SGL_N]]")
                or token_map.get("[[STRUCT_BREAK_DBL_N]]")
            ):
                if translated.count("\n") < req.original_text.count("\n"):
                    translated = _restore_line_structure(translated, req.original_text)

            # Clean up only if we have text
            if translated:
                translated = self._clean_translation(
                    translated, req.target_lang, req.original_text, req.string_id
                )
                if (
                    req.source_lang == "ru"
                    and req.target_lang == "uk"
                    and self._needs_ru_to_uk_retry(req.original_text, translated)
                ):
                    logger.info(
                        f"String {req.string_id}: detected partial RU leakage, running multi-pass rewrite"
                    )
                    rewritten = self._force_ukrainian_rewrite(req, translated)
                    if rewritten:
                        cleaned_rewrite = self._clean_translation(
                            rewritten, req.target_lang, req.original_text, req.string_id
                        )
                        if cleaned_rewrite:
                            translated = cleaned_rewrite
                        else:
                            # Rewrite output was a prompt-echo artifact that cleaned to "".
                            # Keep the original (Russian-leaking) translation — it's
                            # better than returning None and losing the string entirely.
                            logger.warning(
                                f"String {req.string_id}: Rewrite result cleaned to empty "
                                f"(model echoed prompt), keeping original with leakage"
                            )
                elif (
                    req.source_lang == "en"
                    and req.target_lang == "uk"
                    and self._needs_en_to_uk_retry(req.original_text, translated)
                ):
                    logger.info(
                        f"String {req.string_id}: detected English echo (untranslated), retranslating"
                    )
                    rewritten = self._force_english_retranslate(req, translated)
                    if rewritten:
                        translated = rewritten

            # Restore separator lines that were stripped before sending to model
            if translated and (leading_seps or trailing_seps):
                translated = leading_seps + translated + trailing_seps

            if translated and cache_key and self.translation_cache is not None:
                self.translation_cache.set(cache_key, translated)

            return translated if translated else None

        except requests.exceptions.Timeout:
            raise Exception(
                f"Request timeout (>{self._session.timeout}s). Ollama is taking too long to respond."  # pyright: ignore[reportAttributeAccessIssue]
            )
        except requests.exceptions.ConnectionError:
            with QMutexLocker(self._mutex):
                if self._stop_flag:
                    return None
            raise Exception(
                f"Connection error: Cannot reach Ollama at {self.base_url}. Check that Ollama is running."
            )
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                raise Exception(
                    f"Model '{self.model}' not found in Ollama. "
                    f"Install it with:\n  ollama create {self.model} -f Modelfile.{self.model}"
                )
            raise Exception(f"Ollama HTTP error: {e}")
        except Exception as e:
            raise Exception(f"Ollama translation error: {e}")

    def _clean_translation(
        self, text: str, target_lang: str, original_text: str = "", string_id: int = 0
    ) -> str:
        """Remove common AI artifacts from translation output."""
        if not text:
            return ""

        # Safety net: restore structural newline tokens (all model-garbled variants).
        if "STRUCT" in text and "]]" in text:
            text = re.sub(r'\[\[STRUCT\w*(?:DBL|DOUBLE)\w*\]{2,}', '\n\n', text, flags=re.IGNORECASE)
            text = re.sub(r'\[\[STRUCT\w*(?:SGL|SINGLE)\w*\]{2,}', '\n', text, flags=re.IGNORECASE)
            text = re.sub(r'\[\[STRUCT\w+\]{2,}', '\n', text, flags=re.IGNORECASE)
        # Strip any remaining [[...]] artifact tokens the model may have hallucinated
        text = re.sub(r'\[\[\w+\]{2,}', '', text)

        # Strip thinking blocks emitted by reasoning-capable models (Gemma 4, QwQ, etc.).
        # Pass 1: remove properly closed blocks (non-greedy, requires closing tag).
        text = re.sub(
            r"<\|?think\|?>.*?<\|?/think\|?>",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        # Pass 2: remove an unclosed block whose closing tag was never generated
        # (happens when the reasoning chain hits num_predict and generation stops).
        # Greedy — removes from the opening tag to the end of the string.
        text = re.sub(
            r"<\|?think\|?>.*",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()

        # Strip model-invented markup artifacts that never appear in Bethesda game strings.

        # Markdown code fences — model wraps output in ``` blocks
        text = re.sub(r'```[\s\S]*?```', '', text)
        text = text.replace('```', '')

        # Garbled/invented image tags (e.g. <image|>) — not a valid Bethesda tag
        text = re.sub(r'<image\b[^>]*>', '', text, flags=re.IGNORECASE)

        # [Redacted] — model hallucinating a redaction instead of translating
        if not original_text or '[Redacted]' not in original_text:
            text = re.sub(r'\[Redacted\]', '', text, flags=re.IGNORECASE)

        # Extra </font> close tags beyond what the original had
        if original_text:
            _orig_fc = original_text.count('</font>')
            while text.count('</font>') > _orig_fc:
                _last = text.rfind('</font>')
                if _last == -1:
                    break
                text = text[:_last] + text[_last + 7:]

        # Hallucinated <Alias=...> tags not present in the original
        if original_text:
            _orig_al = {a.lower() for a in re.findall(r'<Alias=[^>]+>', original_text, re.IGNORECASE)}
            for _a in re.findall(r'<Alias=[^>]+>', text, re.IGNORECASE):
                if _a.lower() not in _orig_al:
                    text = text.replace(_a, '', 1)

        # Extra printf format specifiers (%s, %d, %1$s, %%, …) beyond the original count.
        # The model sometimes hallucinates these into translations.
        # We remove the rightmost extras (model artifacts land at the end of output).
        if original_text and '%' in text:
            _FMT_RE = re.compile(r'%(?:[1-9]\$)?[sdfioxXeEgGcpn%]')
            _orig_fmt_n = len(_FMT_RE.findall(original_text))
            _trans_fmt_m = list(_FMT_RE.finditer(text))
            _extra_fmt = len(_trans_fmt_m) - _orig_fmt_n
            if _extra_fmt > 0:
                _remove_spans = [(m.start(), m.end()) for m in _trans_fmt_m[-_extra_fmt:]]
                _parts: list = []
                _prev = 0
                for _s, _e in _remove_spans:
                    _parts.append(text[_prev:_s])
                    _prev = _e
                _parts.append(text[_prev:])
                text = ''.join(_parts)

        # Strip prompt-echo preambles (case-insensitive, handles with/without trailing newline).
        # The model sometimes echoes the "To Ukrainian:" instruction from to_prompt() back
        # into its output — this regex catches all known variants in one pass.
        _PREAMBLE_RE = re.compile(
            r"^(?:"
            r"Translate\s+to\s+\w+\s*:?\s*\n?"      # "Translate to Ukrainian:" / "Translate to Ukrainian\n"
            r"|Translated\s+to\s+\w+\s*:?\s*\n?"    # "Translated to Ukrainian:"
            r"|Translation\s+to\s+\w+\s*:?\s*\n?"   # "Translation to Ukrainian:"
            r"|To\s+\w+\s*:\s*\n?"                  # "To Ukrainian:\n"  ← main offender
            r")",
            re.IGNORECASE,
        )
        text = _PREAMBLE_RE.sub("", text).strip()

        # Strip trailing control characters (\x00-\x1f except \t and \n) that
        # the model sometimes copies from Bethesda source strings (stray \r, NUL, etc.).
        text = re.sub(r"[\x00-\x08\x0b-\x0d\x0e-\x1f]+$", "", text)

        # Strip mid-text model reasoning blocks — appear when the model echoes the
        # original, appends internal draft commentary, then echoes again:
        # e.g. "ЩИТ\n\nCURRENT DRAFT (contains Russian — fix it):\nЩИТ\nFULLY..."
        text = re.sub(
            r"\n+(?:CURRENT DRAFT\s*(?:\([^)]*\))?\s*:|"
            r"FULLY UKRAINIAN TRANSLATION\s*:|"
            r"COMPLETE UKRAINIAN TRANSLATION\s*:).*",
            "",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        ).strip()

        # Strip leading/trailing separator lines (lines of all underscores or dashes).
        # The model sometimes echoes the visual separator used in few-shot examples.
        lines = text.splitlines(keepends=True)
        while lines and re.fullmatch(r"[-_]+\s*", lines[0].rstrip("\n")):
            lines.pop(0)
        while lines and re.fullmatch(r"[-_]+\s*", lines[-1].rstrip("\n")):
            lines.pop()
        text = "".join(lines).strip()

        # If the entire output is a "no translation needed" explanation, discard it.
        _NO_TRANS_RE = re.compile(
            r"^\s*\(?no\s+(russian\s+words?|text)\b[^)]*\)?\s*$"
            r"|^\s*\(?no\s+text\s+provided[^)]*\)?\s*$",
            re.IGNORECASE,
        )
        if _NO_TRANS_RE.match(text):
            return ""

        # Common translated versions of "To X:" prefix
        lang_prefixes = [
            f"{target_lang}:",
            f"To {target_lang}:",
            f"Translate to {target_lang}:",
            f"Translated to {target_lang}:",
            "Translated:",
            "Translation:",
            f"[{target_lang}]",
            "→",
            "»",
            "До української:",
            "до української:",
            "до української",
            "На українську:",
            "на українську:",
            "на українську",
            "Переклад:",
            "переклад:",
            "Перекласти на українську:",
            "Переклад на українську:",
            "Перевод на украинский:",
            "Перевод:",
            "English:",
            "Source:",
            "Переклад з англійської:",
            # Gemma 4 verbose preambles
            "Here is the translation:",
            "Here is the Ukrainian translation:",
            "Here's the translation:",
            "Here's the Ukrainian translation:",
            "Certainly! ",
            "Sure! ",
            "Of course! ",
            # Model self-labelling artifacts
            "COMPLETE UKRAINIAN TRANSLATION:",
            "FULLY UKRAINIAN TRANSLATION:",
            "CURRENT DRAFT (contains Russian — fix it):",
            "CURRENT DRAFT:",
        ]

        # Remove from start
        for prefix in lang_prefixes:
            if text.startswith(prefix):
                text = text[len(prefix) :].strip()
            nl_prefix = prefix.rstrip() + "\n"
            if text.startswith(nl_prefix):
                text = text[len(nl_prefix) :].strip()

        # Remove from end
        for prefix in lang_prefixes:
            if text.endswith(prefix):
                text = text[: -len(prefix)].strip()

        # Remove duplicate leading terms, but preserve paragraph/newline layout.
        # Previous split/join on full text collapsed line breaks for long book-like strings.
        lines = text.splitlines(keepends=True)
        if lines:
            # Find first non-empty content line to apply lightweight dedupe only there.
            for idx, line in enumerate(lines):
                content = line.strip()
                if not content:
                    continue

                words = content.split()
                if len(words) >= 2:
                    deduped = [words[0]]
                    for w in words[1:]:
                        if w.lower() != deduped[-1].lower():
                            deduped.append(w)

                    for split_point in range(2, len(deduped) // 2 + 1):
                        first_phrase = " ".join(deduped[:split_point]).lower()
                        second_phrase = " ".join(
                            deduped[split_point : split_point * 2]
                        ).lower()
                        if first_phrase == second_phrase and first_phrase:
                            deduped = deduped[:split_point] + deduped[split_point * 2 :]
                            break

                    cleaned = " ".join(deduped)
                    # Preserve indentation and line ending from original line
                    leading_ws = line[: len(line) - len(line.lstrip())]
                    line_ending = "\n" if line.endswith("\n") else ""
                    lines[idx] = f"{leading_ws}{cleaned}{line_ending}"
                    text = "".join(lines)
                break

        # Strip echoed original text from start (case-insensitive fuzzy check)
        if original_text and len(original_text) > 3:
            orig_lower = original_text.lower().strip()
            temp_text = text.strip()
            if temp_text.lower().startswith(orig_lower):
                text = temp_text[len(orig_lower) :].strip()

            # Handle common separators after echoed text
            separators = [": ", " - ", " — ", " → ", " | ", ":", "-", "—", "→", "|"]
            for sep in separators:
                if text.startswith(sep):
                    text = text[len(sep) :].strip()
                    break

        # Strip wrapping quotes (straight or guillemets)
        if (text.startswith('"') and text.endswith('"')) or (
            text.startswith("'") and text.endswith("'")
        ) or (text.startswith("«") and text.endswith("»")):
            text = text[1:-1].strip()

        # Remove guillemets that the model added around proper nouns / loanwords
        # when the original text had no quotation marks at all.
        # Ukrainian grammar allows «Спейсерів» as a loanword quote, but game UI
        # strings should never gain quotes the source didn't have.
        if original_text and not self._QUOTE_CHARS_RE.search(original_text):
            text = self._INLINE_GUILLEMET_RE.sub(r"\1", text)

        # Strip trailing lines that verbatim echo a source line.
        # The model sometimes translates a line then echoes the Russian/English source
        # word on a separate trailing line, producing more lines than the original.
        # Only strip while there are more non-empty lines than the original had.
        if original_text:
            orig_lines_stripped = [l.strip() for l in original_text.splitlines() if l.strip()]
            orig_line_count = len(orig_lines_stripped)
            orig_lines_lower = {l.lower() for l in orig_lines_stripped}
            result_lines = text.splitlines()
            while result_lines:
                non_empty = [l for l in result_lines if l.strip()]
                if (
                    len(non_empty) > orig_line_count
                    and result_lines[-1].strip().lower() in orig_lines_lower
                ):
                    result_lines.pop()
                else:
                    break
            text = "\n".join(result_lines)

        # Remove trailing period if the source had none
        if (
            original_text
            and text.endswith(".")
            and not original_text.rstrip().endswith(".")
        ):
            text = text[:-1].strip()

        # Garbage detection
        if original_text and text:
            orig_len = len(original_text.strip())
            text_len = len(text.strip())

            if text_len == 1 and orig_len > 1:
                return ""
            if (
                orig_len <= 6
                and text_len < max(2, int(orig_len * 0.75))
                and not (orig_len == 1 and text_len == 1)
            ):
                return ""
            if text_len >= 2 and orig_len > text_len and orig_len <= 20:
                text_is_cyrillic = any("\u0400" <= c <= "\u04ff" for c in text)
                orig_is_cyrillic = any("\u0400" <= c <= "\u04ff" for c in original_text)
                if (
                    text_is_cyrillic
                    and orig_is_cyrillic
                    and text.lower() in original_text.lower()
                ):
                    return ""
            if 7 <= orig_len <= 15 and text_len < max(3, int(orig_len * 0.4)):
                return ""
            if orig_len > 15 and text_len < max(3, int(orig_len * 0.12)):
                return ""
            if not any(c.isalnum() for c in text):
                return ""
            if orig_len > 3:
                orig_cyrillic_count = sum(
                    1 for c in original_text if "\u0400" <= c <= "\u04ff"
                )
                text_latin_count = sum(1 for c in text if c.isascii() and c.isalpha())
                if (
                    orig_cyrillic_count / orig_len > 0.5
                    and text_latin_count / text_len > 0.5
                ):
                    return ""
            if orig_len > 50 and text_len < max(10, int(orig_len * 0.3)):
                return ""

        text = self._fix_known_errors(text, original_text)
        text = self._fix_truncated_tags(text)
        text = self._restore_paragraph_structure(text, original_text)

        # Fix stray Latin letters inside Cyrillic words (e.g. "dослідницький" → "дослідницький").
        # Only applied for Cyrillic-script target languages.
        if target_lang in ("uk", "ru", "Ukrainian", "Russian", "Belarusian", "Bulgarian", "Serbian"):
            text = _fix_mixed_script(text)

        result = text.strip()
        # Restore the original's leading/trailing whitespace frame.
        # Game UI strings often start or end with spaces for alignment; stripping
        # them causes visual layout shifts that are hard to spot during review.
        if original_text:
            orig_leading = len(original_text) - len(original_text.lstrip(" \t"))
            orig_trailing = len(original_text) - len(original_text.rstrip(" \t"))
            if orig_leading:
                result = original_text[:orig_leading] + result
            if orig_trailing:
                result = result + original_text[len(original_text) - orig_trailing :]
        return result

    # Separator line: 3+ underscores filling a whole line (game UI dividers).
    _SEP_LINE_RE = re.compile(r"^[-_]{3,}\s*$")

    def _extract_separators(self, text: str):
        """Split *text* into (leading_sep_str, inner_text, trailing_sep_str).

        Separator lines (lines of 3+ underscores) at the very start and end are
        extracted so they can be stripped before sending to the model and restored
        afterwards.  Separators in the middle of the text are left in place.
        """
        lines = text.splitlines(keepends=True)
        leading = []
        while lines and self._SEP_LINE_RE.match(lines[0].rstrip("\n")):
            leading.append(lines.pop(0))
        trailing = []
        while lines and self._SEP_LINE_RE.match(lines[-1].rstrip("\n")):
            trailing.insert(0, lines.pop())
        return "".join(leading), "".join(lines), "".join(trailing)

    def _restore_paragraph_structure(self, text: str, original_text: str) -> str:
        """Restore paragraph spacing based on source structure for long texts."""
        if not text or not original_text:
            return text

        src = original_text.replace("\r\n", "\n")
        out = text.replace("\r\n", "\n")

        src_breaks = src.count("\n\n")
        out_breaks = out.count("\n\n")

        # Only intervene when source clearly has paragraph breaks and output lost them.
        if src_breaks == 0 or out_breaks >= src_breaks:
            return text

        src_paras = [p.strip() for p in src.split("\n\n") if p.strip()]
        out_lines = [ln.strip() for ln in out.splitlines() if ln.strip()]

        if len(src_paras) < 2 or len(out_lines) < 2:
            return text

        # Distribute output lines across source paragraph count by relative size.
        src_lens = [max(1, len(p)) for p in src_paras]
        total_src = sum(src_lens)
        total_out = sum(len(ln) for ln in out_lines)
        if total_out <= 0:
            return text

        target_lens = [max(1, int(total_out * (ln / total_src))) for ln in src_lens]

        groups = []
        i = 0
        for p_idx, target in enumerate(target_lens):
            remaining_groups = len(target_lens) - p_idx
            remaining_lines = len(out_lines) - i
            if remaining_lines <= 0:
                groups.append([])
                continue

            # Keep at least one line for each remaining group.
            max_take = max(1, remaining_lines - (remaining_groups - 1))
            taken = []
            acc = 0
            while len(taken) < max_take:
                line = out_lines[i]
                taken.append(line)
                i += 1
                acc += len(line)
                # For non-final groups, stop near expected size.
                if p_idx < len(target_lens) - 1 and acc >= int(target * 0.9):
                    break
                if i >= len(out_lines):
                    break
            groups.append(taken)

        # Any leftovers go to the last paragraph.
        if i < len(out_lines):
            if groups:
                groups[-1].extend(out_lines[i:])
            else:
                groups = [out_lines[i:]]

        rebuilt_paras = []
        for g in groups:
            if not g:
                continue
            # Heading-like blocks stay line-by-line; prose paragraphs are single wrapped line.
            if len(g) > 1 and all(len(x) <= 60 for x in g):
                rebuilt_paras.append("\n".join(g))
            else:
                rebuilt_paras.append(" ".join(g))

        if not rebuilt_paras:
            return text

        rebuilt = "\n\n".join(rebuilt_paras)
        return rebuilt

    # ── Russian detection patterns (compiled once) ────────────────────────────
    _RU_CHARS = re.compile(r"[ыэёъЫЭЁЪ]")

    # Distinctly Russian words absent or spelled differently in Ukrainian.
    # Uses word boundaries for precision.
    _RU_WORDS = re.compile(
        r"\b(?:"
        # Demonstratives / pronouns
        r"это|этот|эта|эти|этого|этому|этой|этих|этим|этими"
        r"|который|которая|которое|которые|которого|которому|которой|которых|которым|которыми"
        r"|такой|такая|такое|такие|такого|такому|такой|таких|таким"
        r"|чей|чья|чьё|чьи"
        # Conjunctions / particles unique to Russian
        r"|хотя|чтобы|также|тоже|уже|ещё|еще|даже|только|очень|совсем|вовсе|весьма"
        r"|потому|поэтому|почему|зачем|откуда|наверх|ниже|выше|далее|далеко"
        r"|однако|впрочем|кстати|наконец|итак|итого|наоборот|вдруг|снова|опять|снова"
        r"|сейчас|сегодня|вчера|завтра|здесь|туда|оттуда|отсюда"
        r"|просто|именно|вообще|обычно|особенно|конечно|конечно|наверное|видимо"
        # Prepositions shaped distinctly in Russian
        r"|между|среди|около|против|вместо|вместе|кроме|через|вдоль|мимо"
        r"|изо|ото|ради|насчёт|насчет"
        # Negative / indefinite pronouns
        r"|никто|ничего|никогда|нигде|никуда|некто|нечто|несколько|кое-кто|кое-что"
        r"|кто-то|что-то|кто-нибудь|что-нибудь|где-то|куда-то|когда-то"
        # Quantifiers
        r"|каждый|каждая|каждое|каждые|каждого|каждому|каждой|каждых|каждым"
        r"|любой|любая|любое|любые|любого|любому|любой|любых|любым"
        r"|весь|вся|всё|всего|всему|всей|всех|всем|всеми"
        r"|самый|самая|самое|самые|самого|самому|самой|самых|самым"
        r"|больше|меньше|лучше|хуже|раньше|позже|скорее|быстрее|медленнее"
        r"|многие|многих|многим|многое|многого"
        # Verb forms distinctly Russian
        r"|нельзя|можно|нужно|надо|хочется|кажется|получается|придётся|придется"
        r"|является|являются|является|являлся|являлась|являлось|являлись"
        r"|будет|будут|буду|будешь|будем|будете"
        r"|стала|стали|стало|сталось|стань|стаёт|стает|становится|становятся"
        r"|приходит|уходит|говорит|знает|может|хочет|имеет|живёт|живет|несёт|несет"
        r"|пришёл|пришел|пришла|пришли|пришло|придёт|придет|придут|придёшь|придешь"
        r"|приводится|представляется|называется|оказывается|считается|получается"
        r"|хотел|хотела|хотели|хотело|хочу|хочешь|хочет|хотят"
        r"|смотрит|смотрят|слышит|слышат|видит|видят|знает|знают"
        r"|говорит|говорят|думает|думают|делает|делают|берёт|берет|берут"
        # Nouns / adjectives with distinctly Russian spelling
        r"|жизнь|лицо|голова|сердце|душа|мысль|сила|власть|правда|свобода"
        r"|ребёнок|ребенок|дети|ребят|ребята"
        r"|девушка|женщина|мужчина|парень|юноша"
        r"|город|страна|земля|мир|свет|небо|море|лес|поле|дорога|путь"
        r"|книга|слово|дело|время|место|начало|конец|сторона|часть"
        r"|человек|люди|народ|семья|друг|враг|брат|сестра"
        r"|отрывок|произведения|приводится|спросила|сказала|ответила|подумала"
        r")\b",
        re.IGNORECASE,
    )

    # Russian verb infinitives end in -ть/-ться; Ukrainian use -ти/-тися.
    # Only fire when the word looks Cyrillic and is not a Ukrainian noun ending in -ть.
    _RU_INFINITIVE = re.compile(
        r"\b[а-яёА-ЯЁ]{3,}(?<!с)(?<!з)(?<!б)(?<!м)(?<!т)ть(?:ся)?\b"
    )

    # Ukrainian-specific characters that confirm the text is already (partially) Ukrainian
    _UK_SPECIFIC = re.compile(r"[іїєІЇЄ]")

    def _needs_ru_to_uk_retry(self, original_text: str, translated_text: str) -> bool:
        """Detect when RU->UK output still contains Russian text."""
        if not original_text or not translated_text:
            return False

        t = translated_text.strip()
        if not t:
            return False

        # 1. Instant fail: Russian-exclusive characters.
        if self._RU_CHARS.search(t):
            return True

        lower = t.lower()
        # 2. Distinctly Russian words — but many words in the list are common Slavic roots
        # that appear in Ukrainian too (мир, земля, сила, душа, свобода…).  Only treat
        # them as Russian evidence when the output has NO Ukrainian-specific characters
        # (і/ї/є/ґ), because real Ukrainian sentences always contain at least one.
        if not self._UK_SPECIFIC.search(t) and self._RU_WORDS.search(lower):
            return True

        # 3. Russian verb infinitives (-ть / -ться).
        # Only count when the text has no Ukrainian-specific chars — that means it
        # is probably fully Russian-transcribed, not a genuine Ukrainian -ть noun.
        if not self._UK_SPECIFIC.search(t):
            inf_hits = len(self._RU_INFINITIVE.findall(t))
            if inf_hits >= 2:
                return True

        # 4. Unchanged original lines (echo = untranslated).
        #    Threshold 8 (was 12) so short single-word/phrase strings are also caught.
        orig_lines = [
            ln.strip() for ln in original_text.splitlines() if len(ln.strip()) >= 8
        ]
        if orig_lines:
            unchanged = sum(1 for ln in orig_lines if ln in t)
            if unchanged >= max(1, len(orig_lines) // 4):
                return True

        # 4b. Exact single-string echo for very short Russian strings (< 8 chars per line).
        if original_text.strip() == t and sum(1 for c in t if "Ѐ" <= c <= "ӿ") >= 2:
            return True

        # 5. Long Cyrillic text with zero Ukrainian-specific chars — only fire when
        #    also the infinitive check (pass 3 above) was inconclusive.  A single
        #    sentence without і/ї/є is normal Ukrainian; a long paragraph with none
        #    is a strong signal of Russian.  Require ≥ 80 Cyrillic chars to avoid
        #    false positives on short strings.
        if not self._UK_SPECIFIC.search(t):
            cyrillic_count = sum(1 for c in t if "Ѐ" <= c <= "ӿ")
            if cyrillic_count >= 80:
                return True

        # 6. Full Russian-dictionary scan (1.5 M words, loaded lazily).
        #    Only fires when the text has no Ukrainian-specific chars, because
        #    text_has_russian_words already does that internal guard.
        #    Catches "cleaned" Russian text where ы→и substitution was applied
        #    but the words themselves are still Russian.
        if text_has_russian_words(t, threshold=3):
            return True

        # 7. Quoted Russian words left untranslated.
        #    Model sometimes treats text in quotes as a proper name and skips it.
        #    Detect: a quoted substring from the original appears unchanged in
        #    translation AND contains Cyrillic but no Ukrainian-specific chars.
        _QUOTED_RE = re.compile(r'["«»“”]([^"«»“”]+)["«»“”]')
        for m in _QUOTED_RE.finditer(original_text):
            word = m.group(1).strip()
            if (
                len(word) >= 3
                and any("Ѐ" <= c <= "ӿ" for c in word)
                and not any(c in word for c in "іїєґІЇЄҐ")
                and word in t
            ):
                return True

        # 8. Abbreviation expansion: original has multiple words but translation
        #    is a short all-caps Cyrillic acronym (model compressed instead of translating).
        orig_words = original_text.split()
        if (
            len(orig_words) >= 3
            and 1 <= len(t) <= 6
            and t.isupper()
            and all("Ѐ" <= c <= "ӿ" for c in t if c.isalpha())
        ):
            return True

        return False

    # ── Character/word substitution table used in rewrite prompts ─────────────
    _RU_TO_UK_MAP = (
        "MANDATORY substitutions — apply every single one:\n"
        "  CHARACTERS: ы→и  Ы→И  э→е  Э→Е  ё→ьо(mid-word)/йо(word-start)  Ё→Йо  ъ→(delete)  Ъ→(delete)\n"
        "  WORDS:\n"
        "    это/этот/эта/эти → це/цей/ця/ці\n"
        "    который/которая/которые → який/яка/які\n"
        "    чтобы → щоб        хотя → хоча       также/тоже → також/теж\n"
        "    уже → вже          ещё/еще → ще       даже → навіть\n"
        "    только → тільки    очень → дуже       совсем → зовсім\n"
        "    между → між        среди → серед       около → близько\n"
        "    нельзя → не можна  можно → можна       нужно/надо → треба/потрібно\n"
        "    если → якщо        когда → коли        пока → поки\n"
        "    потому → тому      поэтому → тому      почему → чому\n"
        "    здесь → тут        туда → туди         откуда → звідки\n"
        "    сейчас → зараз     уже → вже           снова → знову\n"
        "    конечно → звичайно никто → ніхто       ничего → нічого\n"
        "    никогда → ніколи   нигде → ніде        вдруг → раптом\n"
        "    однако → однак     впрочем → втім      наконец → нарешті\n"
        "    каждый/каждая → кожний/кожна           любой/любая → будь-який/будь-яка\n"
        "    весь/вся/всё → весь/вся/все (keep)     самый/самая → найбільш/найкращий\n"
        "    больше → більше    лучше → краще        хуже → гірше\n"
        "    будет/будут → буде/будуть              была/были → була/були\n"
        "  VERB INFINITIVES: replace -ть→-ти  -ться→-тися  (говорить→говорити, быть→бути)\n"
    )

    def _call_ollama_rewrite(
        self,
        system: str,
        prompt: str,
        input_len: int,
        temperature: float = 0.15,
    ) -> Optional[str]:
        """Single Ollama call used by the rewrite pipeline."""
        adaptive_num_predict = min(
            self.ollama_num_predict, max(150, input_len * 5)
        )
        model_config = self._get_model_config()
        # Adaptive num_ctx: rewrite prompt includes original + draft, so 2× input_len chars.
        estimated_tokens = (input_len * 2) // 3 + 400
        required_ctx = estimated_tokens * 2 + 512
        for _ctx in (4096, 8192, 16384, 32768):
            if required_ctx <= _ctx:
                rewrite_num_ctx = _ctx
                break
        else:
            rewrite_num_ctx = 32768
        model_max_ctx = int(model_config.get("num_ctx") or 32768)  # type: ignore[arg-type]
        rewrite_num_ctx = max(4096, min(rewrite_num_ctx, model_max_ctx, max(self.ollama_num_ctx, 4096)))
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
            "keep_alive": -1,
            "options": {
                "temperature": temperature,
                "num_predict": adaptive_num_predict,
                "num_ctx": rewrite_num_ctx,
            },
        }
        for opt in ["top_p", "top_k", "min_p", "repeat_penalty"]:
            if opt in model_config:
                payload["options"][opt] = model_config[opt]
        if "stops" in model_config:
            payload["options"]["stop"] = model_config["stops"]
        if model_config.get("think_disabled"):
            payload["think"] = False
        if self.ollama_num_thread > 0:
            payload["options"]["num_thread"] = self.ollama_num_thread

        model_timeout = int(model_config.get("timeout") or self._session.timeout)  # type: ignore[arg-type]
        resp = self._session.post(
            f"{self.base_url}/api/generate",
            json=payload,
            timeout=model_timeout,
        )
        if resp.status_code == 404:
            raise Exception(
                f"Model '{self.model}' not found in Ollama. "
                f"Install it with:\n  ollama create {self.model} -f Modelfile.{self.model}"
            )
        resp.raise_for_status()
        result = resp.json().get("response", "").strip()
        return result or None

    def _needs_en_to_uk_retry(self, original_en: str, translated: str) -> bool:
        """Detect when EN→UK output is substantially untranslated (model echoed English back).

        Note: protected proper nouns (tokenised by TermProtector before the API call)
        are restored as English AFTER translation, so they are never a false echo.
        The only English that can appear here from a genuine translation is restored
        tokens — and those strings also have Ukrainian Cyrillic content alongside them.
        """
        if not original_en or not translated:
            return False

        t = translated.strip()
        orig = original_en.strip()
        if not t or not orig:
            return False

        # 1. Exact/near-exact whole-string match — catches short echoes like "Attack"→"Attack".
        #    Require at least 4 alphabetic chars so single-letter strings don't fire.
        orig_alpha = sum(1 for c in orig if c.isalpha())
        if orig_alpha >= 4 and t.lower() == orig.lower():
            return True

        # 2. Character-distribution test: if output is overwhelmingly Latin with no
        #    Ukrainian-specific chars, the text was not translated.
        cyrillic = sum(1 for c in t if "Ѐ" <= c <= "ӿ")
        latin = sum(1 for c in t if c.isalpha() and c.isascii())
        total_alpha = cyrillic + latin

        if total_alpha < 5:
            return False  # Too few alphabetic chars to judge (e.g. "OK", "1/2")

        uk_specific = sum(1 for c in t if c in "іїєґІЇЄҐ")
        if uk_specific == 0 and latin / total_alpha >= 0.75:
            return True

        # 3. Multi-line echo: original lines appear verbatim in the output.
        orig_lines = [ln.strip() for ln in orig.splitlines() if len(ln.strip()) >= 8]
        if orig_lines:
            unchanged = sum(1 for ln in orig_lines if ln in t or ln.lower() in t.lower())
            if unchanged >= max(1, len(orig_lines) // 3):
                return True

        # 4. Mixed-script words: Latin character embedded inside a Cyrillic word.
        # e.g. "будь-dе" (Latin 'd' inside Ukrainian "де") — the ratio test misses
        # single-char leaks in long Cyrillic texts, but this is a clear translation error.
        # All-Latin words (proper nouns, game tags) are never flagged here.
        for word in re.findall(r"\S+", t):
            alpha = re.sub(r"[^a-zA-ZА-ЯҐЄІЇа-яґєії]", "", word)
            if not alpha:
                continue
            has_cyr = any("Ѐ" <= c <= "ӿ" for c in alpha)
            has_lat = any(c.isalpha() and c.isascii() for c in alpha)
            if has_cyr and has_lat:
                return True

        return False

    def _force_english_retranslate(
        self, req: TranslationRequest, text: str
    ) -> Optional[str]:
        """Two-pass retry for EN→UK strings that the model echoed back untranslated."""
        orig = req.original_text
        input_len = max(len(text), len(orig))
        string_id = req.string_id

        BASE_SYSTEM = (
            "You are a professional Ukrainian localization translator for Bethesda Starfield.\n"
            "Translate the English text into natural, polished Ukrainian.\n"
            "Output ONLY the Ukrainian translation — no English words except game tags and proper nouns, "
            "no preamble, no commentary.\n"
            "Preserve all game tags (<Alias=…>, <font>), escape sequences (\\n \\t), and structure exactly."
        )

        # Pass 1: explicit instruction with different framing
        try:
            p1_prompt = (
                f"Ukrainian translation of the following English text:\n\n{orig}\n\n"
                "Ukrainian:"
            )
            result = self._call_ollama_rewrite(BASE_SYSTEM, p1_prompt, input_len, 0.2)
            if result:
                cleaned = self._clean_translation(result, req.target_lang, orig, string_id)
                if cleaned and not self._needs_en_to_uk_retry(orig, cleaned):
                    logger.debug(f"String {string_id}: EN retranslate pass 1 succeeded")
                    return cleaned
            logger.debug(f"String {string_id}: EN retranslate pass 1 still English, trying pass 2")
        except Exception as e:
            logger.debug(f"String {string_id}: EN retranslate pass 1 failed: {e}")

        # Pass 2: higher temperature, minimal framing
        try:
            p2_prompt = f"Перекласти українською:\n{orig}"
            result = self._call_ollama_rewrite(BASE_SYSTEM, p2_prompt, input_len, 0.4)
            if result:
                cleaned = self._clean_translation(result, req.target_lang, orig, string_id)
                if cleaned:
                    logger.debug(f"String {string_id}: EN retranslate pass 2 done")
                    return cleaned
        except Exception as e:
            logger.debug(f"String {string_id}: EN retranslate pass 2 failed: {e}")

        return None

    def _force_ukrainian_rewrite(
        self, req: TranslationRequest, text: str
    ) -> Optional[str]:
        """
        Multi-pass pipeline that eliminates all Russian from a translation.

        Pass 1 — rich retranslation from the original Russian source with explicit
                  character-map rules; temperature 0.15 for consistency.
        Pass 2 — if Russian remains, re-translate directly from original at higher
                  temperature (0.3) so the model explores different phrasing.
        Pass 3 — if Russian still remains, send only the leaking segments for
                  focused correction, then stitch back into the best candidate.

        Returns the cleanest result found (may still be imperfect if the model
        stubbornly leaks on a particular string).
        """
        string_id = req.string_id
        orig = req.original_text
        input_len = max(len(text), len(orig))

        # ── Shared system prompt ───────────────────────────────────────────────
        BASE_SYSTEM = (
            "You are an expert Ukrainian localization editor for Bethesda Starfield.\n"
            "Your sole task: produce a COMPLETE, NATURAL Ukrainian translation.\n"
            "Zero Russian words, letters, or grammatical forms are allowed in the output.\n"
            f"{self._RU_TO_UK_MAP}\n"
            "PRESERVE EXACTLY: game tags ([TK:…], <Alias=…>, {var}),\n"
            "  IDs, escape sequences (\\n \\t), punctuation, and paragraph structure.\n"
            "Square brackets with Cyrillic text are dialogue choices — translate inside but keep brackets: [Соврать] -> [Збрехати].\n"
            "Output ONLY the translated Ukrainian text — no explanations or commentary."
        )

        best: Optional[str] = None

        # ══ Pass 1: retranslate from original + use current draft as context ══
        try:
            p1_prompt = (
                "To Ukrainian:\n"
                "Translate the Russian source text below into FULLY Ukrainian.\n"
                "The draft shows the current output — use it only as a style reference;\n"
                "fix every Russian word/character found in it.\n\n"
                f"RUSSIAN SOURCE:\n{orig}\n\n"
                f"CURRENT DRAFT (contains Russian — fix it):\n{text}\n\n"
                "FULLY UKRAINIAN TRANSLATION:"
            )
            result = self._call_ollama_rewrite(BASE_SYSTEM, p1_prompt, input_len, 0.15)
            if result:
                best = result
                if not self._needs_ru_to_uk_retry(orig, result):
                    logger.debug(f"String {string_id}: rewrite pass 1 succeeded")
                    return result
                logger.debug(f"String {string_id}: rewrite pass 1 still has Russian, trying pass 2")
        except Exception as e:
            logger.debug(f"String {string_id}: rewrite pass 1 failed: {e}")

        # ══ Pass 2: direct retranslation from Russian original, higher temperature ══
        try:
            p2_prompt = (
                "To Ukrainian:\n"
                "Translate EVERY word of the following Russian text into natural Ukrainian.\n"
                "Apply ALL character substitutions: ы→и, э→е, ё→ьо, ъ→(remove),\n"
                "  verb infinitives -ть→-ти, -ться→-тися.\n"
                "Replace all Russian-specific words with their Ukrainian equivalents.\n"
                "Do not leave a single Russian letter or word in the output.\n\n"
                f"RUSSIAN TEXT TO TRANSLATE:\n{orig}\n\n"
                "COMPLETE UKRAINIAN TRANSLATION:"
            )
            result = self._call_ollama_rewrite(BASE_SYSTEM, p2_prompt, input_len, 0.3)
            if result:
                # Keep this result if it's cleaner than pass 1
                if best is None or not self._needs_ru_to_uk_retry(orig, result):
                    best = result
                if not self._needs_ru_to_uk_retry(orig, result):
                    logger.debug(f"String {string_id}: rewrite pass 2 succeeded")
                    return result
                logger.debug(f"String {string_id}: rewrite pass 2 still has Russian, trying pass 3")
        except Exception as e:
            logger.debug(f"String {string_id}: rewrite pass 2 failed: {e}")

        # ══ Pass 3: targeted segment correction of the best candidate so far ══
        candidate = best or text
        try:
            # Identify which lines still contain Russian
            lines = candidate.splitlines()
            dirty_lines = [
                ln for ln in lines
                if self._RU_CHARS.search(ln)
                or (not self._UK_SPECIFIC.search(ln) and self._RU_WORDS.search(ln.lower()))
            ]
            if dirty_lines:
                dirty_block = "\n".join(dirty_lines)
                p3_system = (
                    BASE_SYSTEM + "\n"
                    "These specific segments still contain Russian — rewrite them into Ukrainian.\n"
                    "Return ONLY the corrected segments in the same order, one per line."
                )
                p3_prompt = (
                    "To Ukrainian:\n"
                    "The following segments are still in Russian. "
                    "Translate each one into natural Ukrainian.\n"
                    "Apply: ы→и, э→е, ё→ьо, ъ→(remove), -ть→-ти, -ться→-тися.\n\n"
                    f"SEGMENTS TO FIX:\n{dirty_block}\n\n"
                    "UKRAINIAN CORRECTIONS (same number of lines):"
                )
                fixed_block = self._call_ollama_rewrite(
                    p3_system, p3_prompt, len(dirty_block) * 5, 0.2
                )
                if fixed_block:
                    fixed_lines = fixed_block.splitlines()
                    fi = 0
                    rebuilt = []
                    for ln in lines:
                        if (
                            fi < len(fixed_lines)
                            and (
                                self._RU_CHARS.search(ln)
                                or (not self._UK_SPECIFIC.search(ln) and self._RU_WORDS.search(ln.lower()))
                            )
                        ):
                            rebuilt.append(fixed_lines[fi])
                            fi += 1
                        else:
                            rebuilt.append(ln)
                    result = "\n".join(rebuilt)
                    if best is None or not self._needs_ru_to_uk_retry(orig, result):
                        best = result
                    if not self._needs_ru_to_uk_retry(orig, result):
                        logger.debug(f"String {string_id}: rewrite pass 3 succeeded")
                        return result
        except Exception as e:
            logger.debug(f"String {string_id}: rewrite pass 3 failed: {e}")

        if best:
            logger.debug(f"String {string_id}: rewrite returning best of {3} passes (may still have leakage)")
        return best

    def _fix_truncated_tags(self, text: str) -> str:
        """Fix unclosed HTML and Bethesda tags caused by truncation."""
        if not text:
            return text
        font_opens = len(re.findall(r"<font", text, re.IGNORECASE))
        font_closes = len(re.findall(r"</font>", text, re.IGNORECASE))
        if font_opens > font_closes:
            text = re.sub(r"<font[^>]*$", "", text, flags=re.IGNORECASE)
            for _ in range(font_opens - font_closes):
                text += "</font>"
        if text.count("[") > text.count("]"):
            if not text.endswith("]"):
                last_open = text.rfind("[")
                if last_open > text.rfind("]"):
                    text += "]"
        if text.count("{") > text.count("}"):
            if not text.endswith("}"):
                last_open = text.rfind("{")
                if last_open > text.rfind("}"):
                    text += "}"
        text = re.sub(r"<[a-zA-Z0-9._]+$", "", text)
        return text

    def _fix_known_errors(self, text: str, original_text: str = "") -> str:
        """Fix known semantic mistranslations and Russian letter leakage."""
        if not text:
            return text
        semantic_fixes = [
            ("Сонячна спалах", "Сонячний спалах"),
            ("Вічне врожай", "Вічний врожай"),
            ("Вічне врожайність", "Вічний врожай"),
            ("Термінал зламано", "Термінал завдань"),
            ("Бомбардування з бордів", "Больєдна бомбардування"),
            ("Льві ноги", "Ліві ноги"),
            ("Випускний отвір", "Газовідвід"),
            ("Поле спотворене", "Поле викривлень"),
            ('Станція "Стрілка"', "Станція Стрільця"),
            ("Незнищний", "Несмертельний"),
            ("Чудова кімната", "Класна кімната"),
            ("Стандартна панель", "Стандартний слайд"),
            ('"Муха"', '"Мушка"'),
            ("Звёздорождённый", "Зоренароджений"),
            ("Звездорождённый", "Зоренароджений"),
            ("Звёздорожденный", "Зоренароджений"),
            ("Звездорожденный", "Зоренароджений"),
            ("Звездороджений", "Зоренароджений"),
            ("Звездороджених", "Зоренароджених"),
            ("Храм Зоренароджених", "Храм Зоренароджених"),
            ("Храм Звездорожденных", "Храм Зоренароджених"),
            ("Багрового флота", "Багряного флоту"),
            ("Багровый флот", "Багряний флот"),
            ("Кривавий флот", "Багряний флот"),
            ("Кривавого флоту", "Багряного флоту"),
            ("флоту Багровых", "Багряного флоту"),
            ("Comspike", "КомСпайк"),
            ("ComSpike", "КомСпайк"),
            ("Эклиптика", "Екліптика"),
            ("[УДАЛЕНО]", "[ВИДАЛЕНО]"),
            ("[РАЗРЕШЕНО]", "[ДОЗВОЛЕНО]"),
            ("[ОТМЕНЕНА]", "[СКАСОВАНО]"),
            ("[ОТМЕНЕНО]", "[СКАСОВАНО]"),
            ("[ЗАБЛОКИРОВАНО]", "[ЗАБЛОКОВАНО]"),
            ("[ВЫПОЛНЕНО]", "[ВИКОНАНО]"),
            ("[ОЖИДАНИЕ]", "[ОЧІКУВАННЯ]"),
        ]
        for wrong, correct in semantic_fixes:
            if wrong in text:
                text = text.replace(wrong, correct)

        # ── Step 1: Position-aware ё substitution.
        # Ukrainian rule: ё → йо at word-start or after a vowel; → ьо after a consonant.
        # A simple global replace("ё","ьо") is wrong for word-initial ё (gives ьожик
        # instead of йожик) and for post-vowel ё (gives своьо instead of своє).
        _UK_VOWELS = "аеиоуєіїюяАЕИОУЄІЇЮЯ"
        text = re.sub(r"\bё", "йо", text)                              # word-start
        text = re.sub(r"\bЁ", "Йо", text)                              # word-start capital
        text = re.sub(f"(?<=[{_UK_VOWELS}])ё", "йо", text)            # after vowel
        text = re.sub(f"(?<=[{_UK_VOWELS}])Ё", "Йо", text)            # after vowel capital
        text = text.replace("ё", "ьо").replace("Ё", "Ьо")             # after consonant

        # ── Step 3: Remaining single-character Russian substitutions.
        russian_fixes = [
            ("ы", "и"),
            ("Ы", "И"),
            ("э", "е"),
            ("Э", "Е"),
            ("ъ", ""),
            ("Ъ", ""),
        ]
        for ru_char, ua_char in russian_fixes:
            if ru_char in text:
                text = text.replace(ru_char, ua_char)

        # ── Step 4: Common leakage words — use \b word boundaries so partial
        # matches (e.g. "Мир." or "Мир\n") are caught alongside "Мир ".
        text = re.sub(r"\bПризнаки\b", "Ознаки", text)
        text = re.sub(r"\bМир\b", "Світ", text)
        text = re.sub(r"\bМира\b", "Світу", text)
        text = re.sub(r"\bНикто\b", "Ніхто", text)
        text = re.sub(r"\bНикогда\b", "Ніколи", text)
        text = re.sub(r"\bНичего\b", "Нічого", text)
        text = re.sub(r"\bЛицо\b", "Обличчя", text)
        text = re.sub(r"\bГород\b", "Місто", text)
        text = re.sub(r"\bВремя\b", "Час", text)

        # ── Step 4b: Russian-influenced Ukrainian imperatives.
        # "Погляди" does not exist as a Ukrainian imperative — it is the noun
        # (views/opinions).  The correct imperative of поглянути is "Поглянь".
        # Fix only at line/string start followed by punctuation or end-of-string
        # so that the noun form "погляди" mid-sentence is untouched.
        text = re.sub(r"(?m)^Погляди(?=[.!?]|\s*$)", "Поглянь", text)
        text = re.sub(r"(?m)^погляди(?=[.!?]|\s*$)", "поглянь", text)

        # ── Step 5: Safe Russian 'и' → Ukrainian 'і' for specific proper names.
        text = re.sub(r"\b(Д)и(ана)\b", r"\1і\2", text)   # Диана → Діана
        text = re.sub(r"\b(Л)и(н)\b",   r"\1і\2", text)   # Лин → Лін
        text = re.sub(r"\b(Р)и(к)\b",   r"\1і\2", text)   # Рик → Рік
        text = re.sub(r"\b(В)и(ктор)\b",r"\1і\2", text)   # Виктор → Віктор

        planet_suffix_fixes = [
            ("-а ", "-a "),
            ("-а.", "-a."),
            ('-а"', '-a"'),
            ("-а<", "-a<"),
            ("-б ", "-b "),
            ("-б.", "-b."),
            ('-б"', '-b"'),
            ("-б<", "-b<"),
            ("-с ", "-c "),
            ("-с.", "-c."),
            ('-с"', '-c"'),
            ("-с<", "-c<"),
            ("-д ", "-d "),
            ("-д.", "-d."),
            ('-д"', '-d"'),
            ("-д<", "-d<"),
            ("-е ", "-e "),
            ("-е.", "-e."),
            ('-е"', '-e"'),
            ("-е<", "-e<"),
            ("-а", "-a"),
            ("-б", "-b"),
            ("-с", "-c"),
            ("-д", "-d"),
            ("-е", "-e"),
        ]
        for wrong, correct in planet_suffix_fixes:
            if wrong in text:
                text = text.replace(wrong, correct)
        return text

    # Compiled once at class level for performance
    _EN_WORD_RE = re.compile(r"\b([A-Za-z]{2,}(?:-[A-Za-z]{2,})*)\b")

    def _protect_english_text(self, text: str) -> Tuple[str, Dict[str, str]]:
        """
        Detect and protect English text segments from being translated.

        Called during RU→UK translation to shield English proper nouns, brand
        names, and game-specific terms.  Uses the English word dictionary
        (en_word_checker) when available so the decision is based on 370 k
        real English words instead of a tiny hardcoded exclusion list.

        Protection rules (in priority order):
          1. Skip already-tokenised segments (__PROT…, <PT…).
          2. Skip single-letter tokens.
          3. Protect if the segment is all-uppercase (game code / acronym).
          4. Protect if the segment starts with an uppercase letter (proper noun).
          5. If the English dictionary is loaded:
               - Skip if it IS a function word (EN_FUNCTION_WORDS).
               - Protect if it IS a real English content word (in dictionary).
               - Skip if it is NOT in the dictionary (noise / foreign loanword).
          6. Fallback (no dictionary): protect anything not in the function-word
             set (matches the previous behaviour).
        """
        from gui.en_word_checker import EN_FUNCTION_WORDS, word_is_english

        token_map: Dict[str, str] = {}
        protected_text = text
        offset = 0
        counter = 0

        for match in self._EN_WORD_RE.finditer(text):
            seg = match.group(1)

            # Rule 1 – skip internal tokens
            if seg.startswith("<PT") or seg.startswith("__PROT") or seg.startswith("__EN"):
                continue

            # Rule 2 – skip single characters
            if len(seg) < 2:
                continue

            word_lower = seg.lower()

            # Rule 3 – game codes / acronyms: always protect
            if seg.isupper() and len(seg) >= 2:
                pass  # fall through to protect

            # Rule 4 – proper nouns (Capitalised): always protect
            elif seg[0].isupper():
                pass  # fall through to protect

            # Rules 5 / 6 – lowercase word: use dictionary when available
            else:
                in_dict = word_is_english(word_lower)  # None = dict not loaded
                if in_dict is None:
                    # Fallback: skip function words, protect everything else
                    if word_lower in EN_FUNCTION_WORDS:
                        continue
                else:
                    if word_lower in EN_FUNCTION_WORDS:
                        continue
                    if not in_dict:
                        # Not a real English word — don't protect
                        continue
                    # Real English content word — fall through to protect

            counter += 1
            token = f"__EN{900000 + counter:06d}__"
            token_map[token] = seg
            start = match.start(1) + offset
            end = match.end(1) + offset
            protected_text = protected_text[:start] + token + protected_text[end:]
            offset += len(token) - len(seg)

        return protected_text, token_map

    @Slot()
    def stop(self):
        """Signal the worker to stop and release pending futures immediately.

        Sequence:
          1. Set _stop_flag so _translate_single returns on its next flag check.
          2. Snapshot the executor reference under the lock (prevents a race
             where translate_batch clears self._executor in its finally block
             while we're about to call shutdown on it).
          3. Call shutdown(wait=False, cancel_futures=True) on the snapshot so
             futures that haven't started yet are discarded right away.  Running
             futures continue until their current HTTP call returns; they will
             then see _stop_flag and exit without emitting translation_ready.
          4. Replace the HTTP adapter with max_retries=0 so any new connection
             attempt from a still-running thread fails immediately instead of
             retrying 3× (each retry waits seconds).
        """
        with QMutexLocker(self._mutex):
            self._stop_flag = True
            executor = self._executor  # snapshot before releasing the lock

        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass

        # Silence urllib3 retry warnings from threads still winding down.
        logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

        # Replace the adapter with max_retries=0 so new connections fail fast.
        # (translate_batch restores max_retries=3 when the next session starts.)
        try:
            pool_size = max(20, self.max_workers + 5)
            no_retry_adapter = requests.adapters.HTTPAdapter(  # pyright: ignore[reportAttributeAccessIssue]
                pool_connections=pool_size,
                pool_maxsize=pool_size,
                max_retries=0,
            )
            self._session.mount("http://", no_retry_adapter)
            self._session.mount("https://", no_retry_adapter)
        except Exception:
            pass

    def update_config(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        enable_term_protection: Optional[bool] = None,
        protect_named_entities: Optional[bool] = None,
        term_protector: Optional[TermProtector] = None,
        translation_cache: Optional[TranslationCache] = None,
        glossary_manager: Optional[GlossaryManager] = None,
        max_workers: Optional[int] = None,
        ollama_num_thread: Optional[int] = None,
        ollama_num_predict: Optional[int] = None,
        ollama_num_ctx: Optional[int] = None,
        long_string_threshold: Optional[int] = None,
        long_string_action: Optional[str] = None,
    ):
        """Update worker configuration."""
        if base_url is not None:
            self.base_url = base_url.rstrip("/")
        if model is not None:
            self.model = model
        if ollama_num_predict is not None:
            self.ollama_num_predict = ollama_num_predict
        if ollama_num_ctx is not None:
            self.ollama_num_ctx = ollama_num_ctx
        if long_string_threshold is not None:
            self.long_string_threshold = long_string_threshold
        if long_string_action is not None:
            self.long_string_action = long_string_action
        if enable_term_protection is not None:
            self.enable_term_protection = enable_term_protection
        if protect_named_entities is not None:
            self.protect_named_entities = protect_named_entities
        if term_protector is not None:
            self.term_protector = term_protector
        if translation_cache is not None:
            self.translation_cache = translation_cache
        if glossary_manager is not None:
            self.glossary_manager = glossary_manager
        if max_workers is not None:
            self.max_workers = max(1, max_workers)
            # Pool size changed — rebuild the session so the new pool_size takes effect.
            self._session = self._make_session()
        if ollama_num_thread is not None:
            self.ollama_num_thread = ollama_num_thread

    def load_protected_terms(self, file_path):
        """Load custom protected terms from file."""
        if self.term_protector:
            self.term_protector.load_custom_terms(file_path)

    def export_protected_terms(self, file_path):
        """Export protected terms to file."""
        if self.term_protector:
            self.term_protector.export_terms(file_path)

    def close(self) -> None:
        """Release all resources held by this worker.

        Safe to call more than once.  After this returns:
          - Any in-progress translate_batch run has been stopped.
          - The ThreadPoolExecutor (if one was active) has been shut down.
          - The requests.Session connection pool has been closed.
        """
        # Signal any running batch to stop and cancel pending futures.
        with QMutexLocker(self._mutex):
            self._stop_flag = True
            executor = self._executor
            self._executor = None

        if executor is not None:
            try:
                # wait=True so we don't return until threads have exited.
                executor.shutdown(wait=True, cancel_futures=True)
            except Exception as exc:
                logger.debug("executor shutdown during close(): %s", exc)

        try:
            self._session.close()
        except Exception as exc:
            logger.debug("session close during close(): %s", exc)

    # ── Context manager ───────────────────────────────────────────────────────

    def __enter__(self) -> "OllamaWorker":
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def __del__(self) -> None:
        # Last-resort safety net only — explicit close() / context manager is
        # the correct way to release resources.  __del__ is non-deterministic
        # and may be skipped entirely, so no important logic lives here.
        try:
            self.close()
        except Exception:
            pass
