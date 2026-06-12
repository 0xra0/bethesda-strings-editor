"""
Ukrainian gendered noun-adjective agreement checker.

Scans translated strings for mismatches between adjective gender markers and
the grammatical gender of the following (or preceding) noun.  Works without
an external morphological library:

  • Adjective gender is inferred from nominative-case word endings.
  • Noun gender is read from a curated dictionary of ~250 game-relevant
    Ukrainian nouns keyed on nominative singular form.

Only tokens that exactly match the dictionary trigger a check, so inflected
forms (genitive, accusative, etc.) are silently skipped — this trades recall
for precision: no false positives from case-form ambiguity.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

# ── Gender codes ───────────────────────────────────────────────────────────────
M = "M"   # masculine (чоловічий)
F = "F"   # feminine  (жіночий)
N = "N"   # neuter    (середній)

GENDER_LABEL = {M: "чол. (M)", F: "жін. (F)", N: "сер. (N)"}

# ── Noun gender dictionary ─────────────────────────────────────────────────────
# Nominative singular → gender.  Only authoritative, known-gender entries here.
NOUN_GENDER: Dict[str, str] = {
    # ── People / roles (masculine) ─────────────────────────────────────────
    "герой":          M, "злодій":         M, "бранець":       M,
    "полонений":      M, "солдат":         M, "пілот":         M,
    "капітан":        M, "командир":       M, "офіцер":        M,
    "найманець":      M, "ворог":          M, "союзник":       M,
    "лікар":          M, "механік":        M, "інженер":       M,
    "дослідник":      M, "космонавт":      M, "астронавт":     M,
    "робот":          M, "андроїд":        M, "охоронець":     M,
    "торговець":      M, "злочинець":      M, "мешканець":     M,
    "навігатор":      M, "захисник":       M, "розвідник":     M,
    "шпигун":         M, "агент":          M, "оператор":      M,
    "пасажир":        M, "переселенець":   M, "біженець":      M,
    # ── Ship / space / tech (masculine) ───────────────────────────────────
    "корабель":       M, "флот":           M, "сектор":        M,
    "модуль":         M, "реактор":        M, "сканер":        M,
    "маяк":           M, "артефакт":       M, "портал":        M,
    "відсік":         M, "шлюз":           M, "ангар":         M,
    "маршрут":        M, "сигнал":         M, "протокол":      M,
    "документ":       M, "елемент":        M, "ресурс":        M,
    "рівень":         M, "контракт":       M, "наказ":         M,
    "результат":      M, "метод":          M, "звіт":          M,
    "пункт":          M, "борт":           M, "порт":          M,
    "транспорт":      M, "вантаж":         M, "маневр":        M,
    "доступ":         M, "код":            M, "пароль":        M,
    "сервер":         M, "термінал":       M, "канал":         M,
    "запит":          M, "потік":          M, "вибух":         M,
    "постріл":        M, "удар":           M, "бій":           M,
    "штурм":          M, "патруль":        M, "контроль":      M,
    "захист":         M, "порятунок":      M, "двигун":        M,
    "датчик":         M, "зразок":         M, "об'єкт":        M,
    "корпус":         M, "клас":           M, "тип":           M,
    "вид":            M, "розмір":         M, "план":          M,
    "розклад":        M, "список":         M, "відлік":        M,
    "рейс":           M, "маршрут":        M, "режим":         M,
    # ── Abstract (masculine) ──────────────────────────────────────────────
    "успіх":          M, "прогрес":        M, "ризик":         M,
    "вирок":          M, "провал":         M, "аналіз":        M,
    "огляд":          M, "рух":            M, "стан":          M,
    "ефект":          M, "вплив":          M, "баланс":        M,
    "відпочинок":     M, "зв'язок":        M, "прийом":        M,
    "захід":          M, "привід":         M, "прибуток":      M,
    "загін":          M, "злочин":         M, "конфлікт":      M,
    "наслідок":       M, "інцидент":       M, "підрозділ":     M,
    "акцент":         M, "компроміс":      M, "обмін":         M,

    # ── People / roles (feminine) ─────────────────────────────────────────
    "жінка":          F, "дівчина":        F, "людина":        F,
    "особа":          F, "постать":        F,
    # ── Places / things (feminine) ────────────────────────────────────────
    "місія":          F, "планета":        F, "зброя":         F,
    "броня":          F, "станція":        F, "база":          F,
    "система":        F, "галактика":      F, "команда":       F,
    "карта":          F, "атмосфера":      F, "гравітація":    F,
    "пропозиція":     F, "операція":       F, "ситуація":      F,
    "колонія":        F, "кімната":        F, "небезпека":     F,
    "загроза":        F, "битва":          F, "зустріч":       F,
    "допомога":       F, "нагорода":       F, "мета":          F,
    "відповідь":      F, "перемога":       F, "поразка":       F,
    "дорога":         F, "влада":          F, "таємниця":      F,
    "новина":         F, "інформація":     F, "технологія":    F,
    "можливість":     F, "безпека":        F, "проблема":      F,
    "відповідальність": F, "орбіта":       F, "зірка":         F,
    "стежка":         F, "аномалія":       F, "точка":         F,
    "мережа":         F, "структура":      F, "платформа":     F,
    "зона":           F, "ціль":           F, "тактика":       F,
    "стратегія":      F, "позиція":        F, "частота":       F,
    "швидкість":      F, "відстань":       F, "фракція":       F,
    "корпорація":     F, "організація":    F, "армія":         F,
    "розвідка":       F, "охорона":        F, "варта":         F,
    "втрата":         F, "перевага":       F, "слабкість":     F,
    "сила":           F, "воля":           F, "пам'ять":       F,
    "думка":          F, "ідея":           F, "теорія":        F,
    "ціна":           F, "вартість":       F, "якість":        F,
    "наука":          F, "помилка":        F, "загадка":       F,
    "мова":           F, "назва":          F, "форма":         F,
    "версія":         F, "вахта":          F, "черга":         F,
    "увага":          F, "тривога":        F, "небезпека":     F,
    "ракета":         F, "торпеда":        F, "граната":       F,
    "зброя":          F, "кров":           F, "рана":          F,
    "пустеля":        F, "природа":        F, "планета":       F,

    # ── Neuter ────────────────────────────────────────────────────────────
    "завдання":       N, "обладнання":     N, "місце":         N,
    "серце":          N, "небо":           N, "повідомлення":  N,
    "зображення":     N, "людство":        N, "рішення":       N,
    "питання":        N, "значення":       N, "число":         N,
    "вікно":          N, "тіло":           N, "слово":         N,
    "добро":          N, "зло":            N, "поле":          N,
    "море":           N, "сонце":          N, "спорядження":   N,
    "паливо":         N, "здоров'я":       N, "знання":        N,
    "покоління":      N, "існування":      N, "виживання":     N,
    "дослідження":    N, "попередження":   N, "середовище":    N,
    "забезпечення":   N, "управління":     N, "навчання":      N,
    "збереження":     N, "відновлення":    N, "покращення":    N,
    "вирішення":      N, "переміщення":    N, "підтвердження": N,
    "призначення":    N, "завершення":     N, "обличчя":       N,
    "ім'я":           N, "відлуння":       N, "оточення":      N,
    "відео":          N, "радіо":          N, "бюро":          N,
    "право":          N, "добро":          N, "зло":           N,
    "майбутнє":       N, "минуле":         N, "теперішнє":     N,
    "паливо":         N, "джерело":        N, "джерело":       N,
}

# ── Determiners / possessives with known gender ────────────────────────────────
# These are checked BEFORE suffix-based adjective detection.
_DETERMINER_GENDER: Dict[str, str] = {
    # Masculine
    "цей": M, "той": M, "який": M, "мій": M, "твій": M, "наш": M, "ваш": M,
    "чий": M, "кожний": M, "кожен": M, "увесь": M, "весь": M, "жодний": M,
    "інший": M, "один": M, "сам": M, "такий": M, "оцей": M, "отой": M,
    "перший": M, "другий": M, "третій": M, "четвертий": M, "п'ятий": M,
    "останній": M, "наступний": M, "головний": M, "новий": M, "старий": M,
    "великий": M, "малий": M, "хороший": M, "поганий": M, "важливий": M,
    # Feminine
    "ця": F, "та": F, "яка": F, "моя": F, "твоя": F, "наша": F, "ваша": F,
    "чия": F, "кожна": F, "уся": F, "вся": F, "жодна": F,
    "інша": F, "одна": F, "сама": F, "така": F, "оця": F, "ота": F,
    "перша": F, "друга": F, "третя": F, "четверта": F, "п'ята": F,
    "остання": F, "наступна": F, "головна": F, "нова": F, "стара": F,
    "велика": F, "мала": F, "хороша": F, "погана": F, "важлива": F,
    # Neuter
    "це": N, "те": N, "яке": N, "моє": N, "твоє": N, "наше": N, "ваше": N,
    "чиє": N, "кожне": N, "усе": N, "все": N, "жодне": N,
    "інше": N, "одне": N, "саме": N, "таке": N, "оце": N, "оте": N,
    "перше": N, "друге": N, "третє": N, "четверте": N, "п'яте": N,
    "останнє": N, "наступне": N, "головне": N, "нове": N, "старе": N,
    "велике": N, "мале": N, "хороше": N, "погане": N, "важливе": N,
}

# Known nouns / non-adjectives that end in -ий/-ій so we don't mis-tag them.
_NON_ADJ_IY: frozenset = frozenset({
    "настрій", "бій", "рій", "змій", "геній", "сценарій",
    "матеріал",  # doesn't end in -ій, just in case
})

# Known neuter nouns ending in -е/-є so we don't mis-tag them as neuter adj.
_NEUT_NOUN_E: frozenset = frozenset({
    "місце", "серце", "море", "поле", "сонце", "вікно", "тіло",
    "слово", "небо", "добро", "зло",
})

# ── Word-list loader (lazy singleton) ─────────────────────────────────────────
_uk_words: Optional[Set[str]] = None

def _get_uk_words() -> Set[str]:
    global _uk_words
    if _uk_words is None:
        p = Path(__file__).resolve().parent.parent / "data" / "ukrainian_words.txt"
        try:
            _uk_words = set(p.read_text("utf-8").splitlines())
        except OSError:
            _uk_words = set()
    return _uk_words


def _is_neut_adj(word_lower: str) -> bool:
    """
    Return True if *word_lower* (ending in -е or -є) is a neuter adjective.

    Strategy: strip the terminal vowel and check whether the resulting stem
    followed by -ий (hard) or -ій (soft) is a real Ukrainian word.
    This rejects verbs like 'переможе' (stem 'перемож' → 'переможий' ✗)
    while accepting adjectives like 'свіже' (stem 'свіж' → 'свіжий' ✓).
    """
    if word_lower in _NEUT_NOUN_E:
        return False
    stem = word_lower[:-1]   # strip -е or -є
    if not stem:
        return False
    words = _get_uk_words()
    return (stem + "ий") in words or (stem + "ій") in words


# ── Tokeniser ──────────────────────────────────────────────────────────────────
# Matches Ukrainian/Cyrillic words including apostrophe (ь → part of word).
_RE_WORD = re.compile(r"[а-яА-ЯіІїЇєЄґҐ'ʼ’]+", re.UNICODE)

# ── Adjective gender from ending ───────────────────────────────────────────────

def _adj_gender(token: str) -> Optional[str]:
    """
    Return the grammatical gender suggested by *token*'s nominative ending,
    or None if the token does not look like a nominative adjective/determiner.
    """
    w = token.lower()

    # 1. Exact determiner / possessive lookup (highest priority)
    g = _DETERMINER_GENDER.get(w)
    if g is not None:
        return g

    # 2. Masculine adjective: -ий or -ій (min 4 chars, not a known noun)
    if len(w) >= 4 and (w.endswith("ий") or w.endswith("ій")):
        if w not in _NON_ADJ_IY:
            return M

    # 3. Neuter adjective: -е or -є (min 4 chars)
    # Validate by stem check: "нове"→"нов"→"новий" ✓  "переможе"→"перемож"→"переможий" ✗
    if len(w) >= 4 and (w.endswith("е") or w.endswith("є")):
        if _is_neut_adj(w):
            return N

    # 4. Feminine adjective: -а or -я, but ONLY for clear adjective stems.
    # To avoid false-positives (nouns ending in -а), require the word to be
    # in the determiner table (already handled above) or end in -овa/-евa/-евa
    # (possessive-adjective suffixes), OR a short clear adj like "нова".
    # We skip heuristic F detection — it has too many false-positives — and
    # rely on the _DETERMINER_GENDER table covering the most-common F forms.

    return None


# ── Result types ───────────────────────────────────────────────────────────────

@dataclass
class GenderMismatch:
    row_index:     int
    string_id:     int
    text:          str          # full translated string
    adj_token:     str          # the adjective/determiner found
    noun_token:    str          # the noun found
    adj_gender:    str          # gender implied by adjective
    noun_gender:   str          # gender of noun from dictionary
    adj_pos:       int          # word position of adj in the token list
    pattern:       str          # "adj→noun" or "noun←adj"

    @property
    def context(self) -> str:
        """Short context snippet around the mismatch."""
        idx = self.text.lower().find(self.adj_token.lower())
        if idx < 0:
            return self.text[:120]
        start = max(0, idx - 20)
        end   = min(len(self.text), idx + len(self.adj_token) + 40)
        snippet = self.text[start:end]
        if start > 0:
            snippet = "…" + snippet
        if end < len(self.text):
            snippet += "…"
        return snippet


# ── Public API ─────────────────────────────────────────────────────────────────

def check_gender_agreement(rows: List[dict]) -> List[GenderMismatch]:
    """
    Scan *rows* (StringTableModel._data) for adjective/noun gender mismatches.

    Returns a list of GenderMismatch objects sorted by row index.
    Only translated/approved strings with non-empty translations are checked.
    Only exact nominative-singular dictionary noun matches are tested —
    inflected forms are silently skipped to keep false-positive rate low.
    """
    results: List[GenderMismatch] = []

    for i, row in enumerate(rows):
        translated = (row.get("translated") or "").strip()
        if not translated:
            continue
        if row.get("status", "pending") not in ("translated", "approved"):
            continue

        tokens = _RE_WORD.findall(translated)
        string_id = row.get("id", 0)
        seen: set = set()  # de-duplicate (adj_pos, pattern) within one string

        for j, tok in enumerate(tokens):
            tok_l = tok.lower()
            noun_g = NOUN_GENDER.get(tok_l)

            if noun_g is not None:
                # Pattern A: preceding token is an adjective
                if j > 0:
                    adj_g = _adj_gender(tokens[j - 1])
                    if adj_g is not None and adj_g != noun_g:
                        key = (j - 1, "A")
                        if key not in seen:
                            seen.add(key)
                            results.append(GenderMismatch(
                                row_index=i,
                                string_id=string_id,
                                text=translated,
                                adj_token=tokens[j - 1],
                                noun_token=tok,
                                adj_gender=adj_g,
                                noun_gender=noun_g,
                                adj_pos=j - 1,
                                pattern="adj→noun",
                            ))

                # Pattern B: following token is an adjective (predicative)
                if j < len(tokens) - 1:
                    adj_g = _adj_gender(tokens[j + 1])
                    if adj_g is not None and adj_g != noun_g:
                        key = (j + 1, "B")
                        if key not in seen:
                            seen.add(key)
                            results.append(GenderMismatch(
                                row_index=i,
                                string_id=string_id,
                                text=translated,
                                adj_token=tokens[j + 1],
                                noun_token=tok,
                                adj_gender=adj_g,
                                noun_gender=noun_g,
                                adj_pos=j + 1,
                                pattern="noun←adj",
                            ))

    return results
