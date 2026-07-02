"""
Tests for OllamaWorker's source-deterministic post-translation fixups:

  * _strip_spurious_br        — drop <br> tags the model invented
  * _unwrap_spurious_brackets — unwrap [LIST] the model put around bare LIST
  * _match_trailing_newlines  — make the trailing newline run match the source

All three are staticmethods, so they can be exercised directly off the class
without constructing an OllamaWorker (which needs a QThread).  The cases below
are taken verbatim from a real mamaylm batch (du_outlaws_01.xml) whose quality
report flagged EXTRA_TAG (<br>) and NEWLINE_COUNT_MISMATCH.

Run with:
    python -m pytest tests/test_ollama_artifact_fixups.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui.ollama_worker import OllamaWorker, TranslationRequest  # noqa: E402

strip_br = OllamaWorker._strip_spurious_br
unwrap = OllamaWorker._unwrap_spurious_brackets
match_nl = OllamaWorker._match_trailing_newlines
heal = OllamaWorker._heal_known_artifacts


# ── _strip_spurious_br ─────────────────────────────────────────────────────────

def test_br_removed_when_source_has_none():
    src = "Sentence one. Sentence two. Sentence three."
    tgt = "Речення одне.<br>\nРечення два.<br>\nРечення три."
    out = strip_br(tgt, src)
    assert "<br>" not in out
    # <br>\n collapses to a single space (sentence boundary preserved)
    assert out == "Речення одне. Речення два. Речення три."


def test_br_count_drops_to_zero_internal_newlines():
    src = "A single paragraph with no breaks at all."
    tgt = "Абзац.<br>\nДругий.<br>\nТретій.<br>\nЧетвертий."
    out = strip_br(tgt, src)
    assert out.count("\n") == 0
    assert out.count("<br>") == 0


def test_br_variants_and_case_insensitive():
    src = "x"
    for tag in ("<br>", "<br/>", "<br />", "<BR>", "<Br/>"):
        assert "br" not in strip_br(f"текст{tag}кінець", src).lower()


def test_br_preserved_up_to_source_count():
    # If the source legitimately carries a <br>, keep exactly that many.
    src = "Line<br>break"
    tgt = "Рядок<br>розрив<br>зайве"
    out = strip_br(tgt, src)
    assert out.count("<br>") == 1


def test_br_at_end_drops_cleanly():
    assert strip_br("текст<br>\n", "текст") == "текст"


def test_br_noop_when_absent():
    assert strip_br("звичайний текст", "plain text") == "звичайний текст"


# ── _unwrap_spurious_brackets ──────────────────────────────────────────────────

def test_list_unwrapped():
    src = "The frontier settlers who flirt with LIST want to believe."
    tgt = "Поселенці кордону, які фліртують із [LIST], хочуть вірити."
    assert unwrap(tgt, src) == "Поселенці кордону, які фліртують із LIST, хочуть вірити."


def test_unwrap_only_when_bare_in_source():
    # Source already brackets it → leave the translation's brackets alone.
    src = "Press [LIST] to continue."
    tgt = "Натисніть [LIST], щоб продовжити."
    assert unwrap(tgt, src) == tgt


def test_unwrap_requires_token_in_source():
    # Token not in source at all → don't touch translation brackets.
    src = "Nothing relevant here."
    tgt = "Тут є [LIST] звідкись."
    assert unwrap(tgt, src) == tgt


def test_unwrap_ignores_short_tokens():
    # 2-letter acronyms (UC) are left alone to avoid false positives.
    src = "Reported to UC command."
    tgt = "Повідомлено [UC] командуванню."
    assert unwrap(tgt, src) == tgt


def test_unwrap_multiple_distinct_tokens():
    src = "A LIST transport and a MAST relay."
    tgt = "Транспорт [LIST] і ретранслятор [MAST]."
    out = unwrap(tgt, src)
    assert "[LIST]" not in out and "[MAST]" not in out
    assert "LIST" in out and "MAST" in out


# ── _match_trailing_newlines ───────────────────────────────────────────────────

def test_trailing_newline_count_bumped_up():
    # Real case: source "…Grav\n\n", model produced "…\n" → must become "\n\n".
    src = "Whispers In The Grav\n\n"
    tgt = "Шепіт у гравітації\n"
    assert match_nl(tgt, src) == "Шепіт у гравітації\n\n"


def test_trailing_newline_count_trimmed_down():
    src = "Title\n"
    tgt = "Заголовок\n\n\n"
    assert match_nl(tgt, src) == "Заголовок\n"


def test_trailing_newline_stripped_when_source_has_none():
    src = "No trailing newline"
    tgt = "Без кінцевого переносу\n\n"
    assert match_nl(tgt, src) == "Без кінцевого переносу"


def test_trailing_newline_added_when_missing():
    src = "Ends with newline\n"
    tgt = "Закінчується переносом"
    assert match_nl(tgt, src) == "Закінчується переносом\n"


def test_trailing_literal_escape_form():
    # Literal two-character \n (backslash + n), as in some UI strings.
    src = "Label\\n\\n"
    tgt = "Мітка\\n"
    assert match_nl(tgt, src) == "Мітка\\n\\n"


def test_trailing_noop_when_equal():
    src = "x\n\n"
    tgt = "у\n\n"
    assert match_nl(tgt, src) == "у\n\n"


# ── _match_trailing_newlines: CRLF sources (real mamaylm RU→UK batch) ───────────
# The Russian source XML is CRLF, but mamaylm emits LF-only output.  A naive
# \n+$ capture under-counts a CRLF trailing run (it stops at the \r between the
# two breaks), so the source's "\r\n\r\n" (2 breaks) became "\n" (1) and tripped
# NEWLINE_COUNT_MISMATCH.  The count must be matched as plain LF.

def test_trailing_crlf_double_break_becomes_two_lf():
    # ID 46444: src "…<0.Name>.\r\n\r\n", model gave "…<0.Name>.\n" → need "\n\n".
    src = "Загружены данные для <0.Name>.\r\n\r\n"
    tgt = "Завантажено дані для <0.Name>.\n"
    out = match_nl(tgt, src)
    assert out == "Завантажено дані для <0.Name>.\n\n"
    assert "\r" not in out  # output stays LF-only to match the model's body
    assert out.count("\n") == src.count("\n")  # QC newline counts now agree


def test_trailing_crlf_single_break():
    src = "Одна строка\r\n"
    tgt = "Один рядок"
    assert match_nl(tgt, src) == "Один рядок\n"


def test_trailing_crlf_trims_excess_to_source_count():
    # Source has one CRLF break; model over-produced three LF — trim to one.
    src = "Заголовок\r\n"
    tgt = "Заголовок\n\n\n"
    assert match_nl(tgt, src) == "Заголовок\n"


def test_trailing_bare_cr_counts_as_one_break():
    src = "Текст\r"
    tgt = "Текст"
    assert match_nl(tgt, src) == "Текст\n"


# ── retry-hint feedback leak (regression) ──────────────────────────────────────
# A QC retry hint is English feedback.  It must NOT sit in the user turn after the
# "To {tgt}:" anchor — a translation-tuned model translates everything there, so
# the hint leaked into the output (e.g. "Переклад зворотного зв'язку — попередня
# спроба…").  The hint belongs in the system prompt only.

def _req(retry_hint: str = "") -> TranslationRequest:
    return TranslationRequest(
        index=0,
        original_text="Hello world.",
        string_id=1,
        source_lang="en",
        target_lang="uk",
        retry_hint=retry_hint,
    )


_HINT = "\n\nRetranslation feedback — previous attempt had issues:\n• Preserve all numbers."


def test_retry_hint_absent_from_user_turn():
    user_turn = _req(retry_hint=_HINT).to_prompt()
    assert "Retranslation feedback" not in user_turn
    assert "Preserve all numbers" not in user_turn
    # The source text itself is still present and the anchor is intact.
    assert user_turn.startswith("To Ukrainian:")
    assert "Hello world." in user_turn


def test_retry_hint_present_in_system_prompt():
    assert "Retranslation feedback" in _req(retry_hint=_HINT).to_system_prompt()


def test_no_retry_hint_user_turn_is_plain_anchor():
    assert _req().to_prompt() == "To Ukrainian:\nHello world."


# ── AI-fix mode: same leak vector via the "Issues to fix" block ─────────────────
# fix_translation mode passes the source, the flawed translation, and the QC
# issues.  The issues are English instructions, so they must sit in the system
# prompt — not the user turn — or a translation-tuned model echoes them as output.

def _fix_req(retry_hint: str = "") -> TranslationRequest:
    return TranslationRequest(
        index=0,
        original_text="Hello world.",
        string_id=1,
        source_lang="en",
        target_lang="uk",
        fix_translation="Привіт світ.",
        retry_hint=retry_hint,
    )


def test_fix_mode_issues_absent_from_user_turn():
    req = _fix_req(retry_hint=_HINT)
    user_turn = req.to_prompt()
    assert "Retranslation feedback" not in user_turn
    assert "Preserve all numbers" not in user_turn
    assert "Issues to fix" not in user_turn
    # Reference material and the output anchor are still there.
    assert "Hello world." in user_turn          # source
    assert "Привіт світ." in user_turn           # flawed translation to correct
    assert user_turn.rstrip().endswith("Corrected Ukrainian translation:")


def test_fix_mode_issues_present_in_system_prompt():
    sys_prompt = _fix_req(retry_hint=_HINT).to_system_prompt()
    assert "Issues to fix:" in sys_prompt
    assert "Preserve all numbers" in sys_prompt
    # Proofreader persona, not the plain-translator one.
    assert "proofreader" in sys_prompt.lower()


def test_fix_mode_without_hint_has_generic_issues_block():
    sys_prompt = _fix_req().to_system_prompt()
    assert "General quality issues." in sys_prompt


# ── _heal_known_artifacts (cache-hit healing path) ─────────────────────────────

def test_heal_applies_all_fixups():
    src = "A LIST transport jumped.\n\n"
    tgt = "Транспорт [LIST] стрибнув.<br>\n"
    out = heal(tgt, src)
    assert "<br>" not in out
    assert "[LIST]" not in out and "LIST" in out
    assert out.endswith("\n\n")


def test_heal_noop_on_clean_text():
    src = "Clean source."
    tgt = "Чисте джерело."
    assert heal(tgt, src) == "Чисте джерело."


# ── \н escape healing (Cyrillic н bled into the \n escape) ─────────────────────
# Real mamaylm RU→UK interface-TXT run: "…account.\n\нПомилка" tripped MISSING_URL
# because the broken escape glued "нПомилка" onto the preceding URL.  The source
# uses literal two-character \n escapes, so the restored form must be literal too.

heal_esc = OllamaWorker._heal_cyrillic_escapes


def test_heal_cyrillic_escape_literal_form():
    # Source carries literal \n escapes → restore a literal \n (not a real newline).
    src = "Go to http://help.bethesda.net.\\n\\nError"
    tgt = "Перейдіть на http://help.bethesda.net.\\n\\нПомилка"
    out = heal_esc(tgt, src)
    assert out == "Перейдіть на http://help.bethesda.net.\\n\\nПомилка"
    assert "\\н" not in out and "\n" not in out  # no real newline introduced


def test_heal_cyrillic_escape_real_newline_form():
    # Source uses real newlines (.strings) → restore a real newline.
    src = "Line one\nLine two"
    tgt = "Рядок один\\нРядок два"
    assert heal_esc(tgt, src) == "Рядок один\nРядок два"


def test_heal_cyrillic_escape_uppercase():
    src = "A\\nB"
    assert heal_esc("А\\НБ", src) == "А\\nБ"


def test_heal_cyrillic_escape_noop_without_backslash():
    # A bare Cyrillic н with no preceding backslash must never be touched.
    assert heal_esc("Небо", "Sky") == "Небо"


def test_heal_runs_cyrillic_escape_in_pipeline():
    src = "See http://x.io.\\n\\nDone"
    tgt = "Дивись http://x.io.\\n\\нГотово"
    assert heal(tgt, src) == "Дивись http://x.io.\\n\\nГотово"


def test_clean_translation_heals_escape_survives_mixed_script_fix():
    # Full _clean_translation path: the \н heal must survive _fix_mixed_script,
    # which used to convert the restored Latin "n" in "\nПомилка" back to "н".
    src = r"Посетите http://help.bethesda.net.\n\nОшибка: X"
    tgt = r"Відвідайте http://help.bethesda.net.\n\нПомилка: X"
    out = _W._clean_translation(tgt, "uk", src, 1, source_lang="ru")
    assert "\\н" not in out
    assert "http://help.bethesda.net." in out and "\\nПомилка" in out


def test_fix_mixed_script_protects_literal_escape():
    from gui.ollama_worker import _fix_mixed_script
    # Genuine stray Latin still fixed …
    assert _fix_mixed_script("dослідницький") == "дослідницький"
    # … but a literal \n escape stuck to a Cyrillic word is left intact.
    assert _fix_mixed_script("текст\\nПродовження") == "текст\\nПродовження"


# ── hallucinated [TK:…] markers (invented on short titles) ─────────────────────
# Real run: "ЛУНА"→"МІСЯЦЬ\n\n[TK:0001256_00000004]\n\n<made-up lore>" and
# "ПОСАДКА"→"ПРИЗИЩЕННЯ\n\n[TK:00002986] …".  [TK:…] never appears in Bethesda
# strings — strip the marker and the fabricated tail it introduces.

strip_tk = OllamaWorker._strip_hallucinated_tk


def test_strip_tk_short_title_cuts_fabricated_tail():
    src = "ЛУНА"
    tgt = "МІСЯЦЬ\n\n[TK:0001256_00000004]\n\nМісяць – це супутник.\n\n[TK:0001]"
    assert strip_tk(tgt, src) == "МІСЯЦЬ"


def test_strip_tk_short_title_inline_marker():
    src = "ПОСАДКА"
    tgt = "ПРИЗИЩЕННЯ\n\n[TK:00002986] Завершено"
    assert strip_tk(tgt, src) == "ПРИЗИЩЕННЯ"


def test_strip_tk_long_source_removes_bare_marker_only():
    src = "A reasonably long source sentence that the model translated fully."
    tgt = "Достатньо довге джерельне речення [TK:42], яке модель переклала повністю."
    out = strip_tk(tgt, src)
    assert "[TK:" not in out
    assert "Достатньо довге" in out and "переклала повністю" in out


def test_strip_tk_preserved_when_in_source():
    src = "Key [TK:99] reference"
    tgt = "Посилання [TK:99]"
    assert strip_tk(tgt, src) == "Посилання [TK:99]"


def test_strip_tk_noop_without_marker():
    assert strip_tk("Звичайний текст", "Plain text") == "Звичайний текст"


# ── RU→UK cleaning: don't blank valid short translations ───────────────────────
#
# mamaylm translates these correctly, but _clean_translation (written for EN→UK)
# used to blank them because UK is a prefix/substring of RU or much shorter, or
# because the source legitimately repeats.  Regression guard for those 156 empties
# seen in a real full-game RU→UK run.

from gui.ollama_worker import (  # noqa: E402
    _are_closely_related,
    _source_has_repetition,
)

_W = OllamaWorker(model="test-model")


def _clean_ruuk(src, model_out):
    return _W._clean_translation(model_out, "uk", src, 1, source_lang="ru").strip()


def test_ruuk_prefix_word_not_blanked():
    # UK = RU + suffix; echo-prefix strip used to leave "ь" then blank it.
    assert _clean_ruuk("Торговец", "Торговець") == "Торговець"


def test_ruuk_substring_word_not_blanked():
    # UK word is a substring of the RU word.
    assert _clean_ruuk("Небесная", "Небесна") == "Небесна"


def test_ruuk_shorter_word_not_blanked():
    # Legitimately much shorter UK rendering of a ≤6-char RU source.
    assert _clean_ruuk("Есть!", "Є!") == "Є!"


def test_ruuk_repeated_source_preserved():
    # Source legitimately repeats — must NOT be de-duplicated.
    assert _clean_ruuk("Давай! Давай!", "Давай! Давай!") == "Давай! Давай!"
    assert _clean_ruuk("Думай, думай, думай!", "Думай, думай, думай!") == "Думай, думай, думай!"


def test_enuk_one_char_garbage_still_blanked():
    # Unrelated pair: the garbage heuristics must still fire (no regression).
    assert _W._clean_translation("H", "uk", "Hello world", 1, source_lang="en").strip() == ""


def test_closely_related_detection():
    assert _are_closely_related("ru", "uk")
    assert _are_closely_related("Russian", "Ukrainian")   # full names
    assert not _are_closely_related("en", "uk")
    assert not _are_closely_related("uk", "uk")


def test_source_repetition_detection():
    assert _source_has_repetition("Давай! Давай!")
    assert _source_has_repetition("go go go")
    assert not _source_has_repetition("Небесная")
    assert not _source_has_repetition("Майкл Гаррет")


# ── EN-number placeholder hallucinations (mamaylm) ──────────────────────────────
# Real cases from quality_report_20260624_152755 ($LegalScreen): the model emits
# "EN900016" / "EN900031EN900032…" (and the Cyrillic look-alike "ЕН900001") in
# place of segments it failed to translate, dropping a URL and a number.

_has_ph = OllamaWorker._has_placeholder_artifacts
_strip_ph = OllamaWorker._strip_placeholder_artifacts


def test_placeholder_detected_latin_and_cyrillic():
    assert _has_ph("Havok є EN900016 Microsoft", "Havok is Microsoft")
    assert _has_ph("EN900031EN900032EN900033EN900034 [PC]", "Warning text [PC]")
    # Cyrillic look-alike ЕН (Cyrillic Е+Н)
    assert _has_ph("політику конфіденційностіЕН900001", "privacy policy")


def test_placeholder_not_flagged_when_clean():
    assert not _has_ph("Чистий переклад без токенів", "Clean source")
    assert not _has_ph("", "src")


def test_placeholder_left_alone_when_in_source():
    # If the source genuinely carries such a token, don't touch it.
    assert not _has_ph("код EN900016 тут", "code EN900016 here")
    assert _strip_ph("код EN900016 тут", "code EN900016 here") == "код EN900016 тут"


def test_placeholder_stripped_and_residue_tidied():
    tgt = "Програмне забезпечення __ и/или EN900016__ є ©2016 Microsoft."
    out = _strip_ph(tgt, "Программное обеспечение Havok является ©2016 Microsoft.")
    assert "EN900016" not in out
    assert "__" not in out
    assert "  " not in out          # doubled spaces collapsed
    assert "©2016 Microsoft." in out


def test_placeholder_run_stripped():
    out = _strip_ph("EN900031EN900032EN900033EN900034 [PC]", "Warning [PC]")
    assert "EN9000" not in out
    assert "[PC]" in out


def test_needs_ru_uk_retry_fires_on_placeholder():
    # The signal that drives a full retranslation from source.
    assert _W._needs_ru_to_uk_retry("Программное обеспечение Havok", "ПЗ EN900016")


def test_clean_translation_strips_placeholder_as_safety_net():
    out = _W._clean_translation(
        "EN900035EN900036 [PC]", "uk", "Перевод [PC]", 1, source_lang="ru"
    )
    assert "EN9000" not in out
    assert "[PC]" in out


# ── Fabricated heading appended to a short UI label ─────────────────────────────
# Real cases: $AUTO BUILD "АВТО" -> "АВТО\n\nКОМПЛЕКТНІСТЬ…"; $MAP "КАРТА" -> …

_strip_app = OllamaWorker._strip_appended_after_short_label


def test_appended_heading_cut_from_short_label():
    assert _strip_app("АВТО\n\nКОМПЛЕКТНІСТЬ ТА ХАРАКТЕРИСТИКИ", "АВТО") == "АВТО"
    assert _strip_app("КАРТА\n\nКОМПЛЕ́ТНИЙ УКРАЇНСЬКИЙ ПЕРЕКЛАД:", "КАРТА") == "КАРТА"


def test_appended_label_leaves_multiline_source_alone():
    # Source genuinely multi-line — must not be truncated.
    src = "Рядок один\nРядок два"
    tgt = "Line one\nLine two"
    assert _strip_app(tgt, src) == tgt


def test_appended_label_leaves_long_source_alone():
    # Longer source could legitimately wrap to multiple lines.
    src = "Це досить довге джерело з кількома словами"
    tgt = "Translated line one\nTranslated line two"
    assert _strip_app(tgt, src) == tgt


def test_appended_label_noop_without_extra_newlines():
    assert _strip_app("АВТО", "АВТО") == "АВТО"


def test_heal_known_artifacts_covers_both_new_fixups():
    assert heal("АВТО\n\nКОМПЛЕКТНІСТЬ", "АВТО") == "АВТО"
    out = heal("текст EN900016 кінець", "source text end")
    assert "EN900016" not in out


# ── Ukrainian stress/accent marks (mamaylm наголос artifacts) ───────────────────
# MamayLM glues grave/acute accents onto Ukrainian vowels to mark stress
# (наголос). Bethesda strings never carry them, so they must be stripped from
# fresh output and from cached hits alike — unless the source used the character.

_strip_acc = OllamaWorker._strip_stress_accents


def test_stress_grave_backtick_removed():
    assert _strip_acc("робо`та почалася", "work has begun") == "робота почалася"


def test_stress_acute_and_combining_forms_removed():
    assert _strip_acc("робо´та", "work") == "робота"   # standalone acute ´ U+00B4
    assert _strip_acc("робо́та", "work") == "робота"   # combining acute  U+0301
    assert _strip_acc("робо̀та", "work") == "робота"   # combining grave  U+0300


def test_stress_accent_left_alone_when_in_source():
    # A backtick genuinely in the source (code/terminal text) is preserved.
    assert _strip_acc("`код` тут", "`code` here") == "`код` тут"


def test_stress_accent_noop_on_clean_text():
    assert _strip_acc("Чистий переклад", "Clean source") == "Чистий переклад"
    assert _strip_acc("", "src") == ""


def test_stress_accent_tidies_orphan_space():
    # A standalone mark left as its own token collapses the doubled space.
    assert _strip_acc("слово ` тут", "word here") == "слово тут"


def test_clean_translation_strips_stress_accent():
    out = _W._clean_translation("робо`та", "uk", "work", 1, source_lang="en")
    assert "`" not in out and "робота" in out


def test_heal_known_artifacts_strips_stress_accent():
    assert heal("робо́та", "work") == "робота"
