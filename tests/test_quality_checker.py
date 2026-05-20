"""
Tests for QualityChecker — both existing and new checks.
No Qt dependency; pure Python.
"""

import pytest
from gui.quality_checker import (
    AUTOFIX_CODES,
    RETRANSLATE_CODES,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    QualityChecker,
    QualityIssue,
    QualityReport,
    _extract_tags,
    _find_repeated_ngram,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_checker(**kw) -> QualityChecker:
    return QualityChecker(
        target_encoding=kw.get("target_encoding", "utf-8"),
        target_language=kw.get("target_language", "Ukrainian"),
        source_language=kw.get("source_language", "Russian"),
    )


def check(original: str, translated: str, **kw) -> QualityReport:
    return make_checker(**kw).check(0, 1, original, translated)


def codes(report: QualityReport):
    return {i.code for i in report.issues}


# ── Empty / untranslated ───────────────────────────────────────────────────────

def test_empty_translation_flagged():
    r = check("Hello world", "")
    assert "EMPTY_TRANSLATION" in codes(r)
    assert r.severity == SEVERITY_ERROR


def test_empty_original_no_flag():
    r = check("", "")
    assert not r.has_issues


def test_untranslated_identical():
    # UNTRANSLATED only fires when the original contains Cyrillic (Russian source),
    # because English game terms are intentionally left untranslated.
    r = check("Привіт як справи", "Привіт як справи")
    assert "UNTRANSLATED" in codes(r)
    assert r.severity == SEVERITY_ERROR


def test_untranslated_skip_english_source():
    # Non-Cyrillic originals (English game terms, NPC names, etc.) are
    # intentionally left unchanged — must not be flagged as UNTRANSLATED.
    r = check("Hello world here", "Hello world here")
    assert "UNTRANSLATED" not in codes(r)


def test_untranslated_skip_pure_numbers():
    # Pure numeric content — no alphabetic text to translate
    r = check("12345", "12345")
    assert "UNTRANSLATED" not in codes(r)


def test_untranslated_skip_very_short():
    # Text too short to flag
    r = check("OK", "OK")
    assert "UNTRANSLATED" not in codes(r)


def test_translated_not_flagged():
    r = check("Hello world", "Привіт світ")
    assert "UNTRANSLATED" not in codes(r)


# ── Tags ──────────────────────────────────────────────────────────────────────

def test_missing_alias_tag():
    r = check("Talk to <Alias=Companion>", "Поговори з")
    assert "MISSING_TAG" in codes(r)
    assert r.severity == SEVERITY_ERROR


def test_extra_tag_detected():
    r = check("Attack", "Атака <Alias=Enemy>")
    assert "EXTRA_TAG" in codes(r)


def test_matching_tags_no_issue():
    r = check("Go to <Alias=Base>!", "Йди до <Alias=Base>!")
    assert "MISSING_TAG" not in codes(r)
    assert "EXTRA_TAG" not in codes(r)


def test_bracket_tag_preserved():
    r = check("[PLYR] Thanks", "[PLYR] Дякую")
    assert not r.has_issues


def test_missing_bracket_tag():
    r = check("[PLYR] Thanks", "Дякую")
    assert "MISSING_TAG" in codes(r)


def test_printf_preserved():
    r = check("You have %s credits", "У вас є %s кредитів")
    assert not r.has_issues


def test_missing_printf():
    r = check("You have %s credits", "У вас є кредити")
    assert "MISSING_TAG" in codes(r)


# ── Numbers ───────────────────────────────────────────────────────────────────

def test_number_preserved():
    r = check("You need 100 credits", "Вам потрібно 100 кредитів")
    assert "MISSING_NUMBER" not in codes(r)


def test_number_missing():
    r = check("You need 500 credits", "Вам потрібні кредити")
    assert "MISSING_NUMBER" in codes(r)
    assert r.severity == SEVERITY_WARNING


def test_short_numbers_ignored():
    # Single-digit numbers not flagged (too common to be meaningful)
    r = check("Take 5 steps", "Зроби кроки")
    assert "MISSING_NUMBER" not in codes(r)


def test_number_in_tag_not_double_counted():
    # Numbers embedded in tag paths should not trigger separate number check
    r = check("<Alias=NPC01>", "<Alias=NPC01>")
    assert "MISSING_NUMBER" not in codes(r)


# ── URL preservation ──────────────────────────────────────────────────────────

def test_url_preserved():
    r = check("Visit https://example.com", "Відвідай https://example.com")
    assert "MISSING_URL" not in codes(r)


def test_url_missing():
    r = check("Visit https://example.com for info", "Відвідай для інформації")
    assert "MISSING_URL" in codes(r)
    assert r.severity == SEVERITY_ERROR


def test_email_missing():
    r = check("Email user@example.com", "Надішліть лист")
    assert "MISSING_URL" in codes(r)


def test_no_url_no_flag():
    r = check("Open the door", "Відчини двері")
    assert "MISSING_URL" not in codes(r)


# ── AI artifact detection ─────────────────────────────────────────────────────

def test_ai_artifact_translation_prefix():
    r = check("Fire!", "Translation: Вогонь!")
    assert "AI_ARTIFACT" in codes(r)
    assert r.severity == SEVERITY_WARNING


def test_ai_artifact_note_prefix():
    r = check("Fire!", "Note: this is a command")
    assert "AI_ARTIFACT" in codes(r)


def test_clean_translation_no_artifact():
    r = check("Fire!", "Вогонь!")
    assert "AI_ARTIFACT" not in codes(r)


def test_artifact_ukrainian_prefix():
    r = check("Fire!", "Ukrainian: Вогонь!")
    assert "AI_ARTIFACT" in codes(r)


# ── Sentence structure ────────────────────────────────────────────────────────

def test_sentence_count_ok():
    orig = "First. Second. Third. Fourth."
    trans = "Перший. Другий. Третій. Четвертий."
    r = check(orig, trans)
    assert "SENTENCE_COUNT_MISMATCH" not in codes(r)


def test_sentence_count_mismatch_too_few():
    orig = "One. Two. Three. Four. Five."
    trans = "Одне"
    r = check(orig, trans)
    assert "SENTENCE_COUNT_MISMATCH" in codes(r)


def test_sentence_count_mismatch_too_many():
    orig = "One. Two. Three."
    trans = "Один. Два. Три. Чотири. П'ять. Шість. Сім. Вісім."
    r = check(orig, trans)
    assert "SENTENCE_COUNT_MISMATCH" in codes(r)


def test_sentence_count_short_string_skipped():
    # Only 2 sentences in original — threshold is 3
    r = check("Hello. World.", "Привіт.")
    assert "SENTENCE_COUNT_MISMATCH" not in codes(r)


# ── Length ratio ──────────────────────────────────────────────────────────────

def test_suspiciously_short():
    r = check("This is a very long sentence with many words.", "О")
    assert "SUSPICIOUSLY_SHORT" in codes(r)


def test_suspiciously_long():
    r = check("Hi", "Це дуже довгий і деталізований текст, що не відповідає короткому оригіналу в жодному сенсі взагалі.")
    assert "SUSPICIOUSLY_LONG" in codes(r)


# ── Newlines ──────────────────────────────────────────────────────────────────

def test_missing_newlines():
    r = check("Line one\\nLine two", "Рядок один рядок два")
    assert "MISSING_NEWLINES" in codes(r)


def test_newline_preserved():
    r = check("Line one\\nLine two", "Рядок один\\nРядок два")
    assert "MISSING_NEWLINES" not in codes(r)


# ── Russian leakage ───────────────────────────────────────────────────────────

def test_russian_leakage_flagged():
    r = check("Weapons fire", "Оружіыы стрільбы")
    assert "SOURCE_LANGUAGE_LEAK" in codes(r)


def test_clean_ukrainian_no_leakage():
    r = check("Weapons", "Зброя")
    assert "SOURCE_LANGUAGE_LEAK" not in codes(r)


# ── Repetition detection ──────────────────────────────────────────────────────

def test_repetitive_content():
    repeated = "слово одне два три " * 5
    r = check("something long here that is not the same", repeated)
    assert "REPETITIVE_CONTENT" in codes(r)


def test_no_repetition_normal():
    r = check("Hello there friend", "Привіт там друже")
    assert "REPETITIVE_CONTENT" not in codes(r)


# ── Encoding ──────────────────────────────────────────────────────────────────

def test_encoding_ok_utf8():
    r = check("Text", "Текст", target_encoding="utf-8")
    assert "ENCODING_ERROR" not in codes(r)


def test_encoding_error_cp1252():
    r = check("Text", "Текст", target_encoding="cp1252")
    assert "ENCODING_ERROR" in codes(r)
    assert r.severity == SEVERITY_ERROR


# ── Auto-fix ──────────────────────────────────────────────────────────────────

def test_autofix_leading_whitespace():
    checker = make_checker()
    report = checker.check(0, 1, "  Hello", "Hello")
    fixed, applied = checker.auto_fix("  Hello", "Hello", report)
    assert fixed.startswith(" ")
    assert applied


def test_autofix_russian_chars():
    checker = make_checker()
    orig = "Weapons fire"
    # Use enough ы chars (>=3 with >5% ratio) to trigger SOURCE_LANGUAGE_LEAK
    trans = "Оружыы стрільбыы"
    report = checker.check(0, 1, orig, trans)
    assert "SOURCE_LANGUAGE_LEAK" in {i.code for i in report.issues}
    fixed, applied = checker.auto_fix(orig, trans, report)
    assert "ы" not in fixed
    assert applied


def test_autofix_missing_tag_appends():
    checker = make_checker()
    orig = "Talk to <Alias=Companion>"
    trans = "Поговори"
    report = checker.check(0, 1, orig, trans)
    fixed, applied = checker.auto_fix(orig, trans, report)
    assert "<alias=companion>" in fixed.lower()
    assert applied


def test_autofix_no_changes_needed():
    checker = make_checker()
    orig = "Hello"
    trans = "Привіт"
    report = checker.check(0, 1, orig, trans)
    fixed, applied = checker.auto_fix(orig, trans, report)
    assert fixed == trans
    assert not applied


# ── build_retry_hint ──────────────────────────────────────────────────────────

def test_retry_hint_empty_for_clean():
    hint = QualityChecker.build_retry_hint([])
    assert hint == ""


def test_retry_hint_missing_tag():
    issue = QualityIssue(SEVERITY_ERROR, "MISSING_TAG", "tag missing", "<alias=npc>")
    hint = QualityChecker.build_retry_hint([issue])
    assert "tag" in hint.lower()
    assert "<alias=npc>" in hint


def test_retry_hint_repetitive():
    issue = QualityIssue(SEVERITY_WARNING, "REPETITIVE_CONTENT", "repeated", "")
    hint = QualityChecker.build_retry_hint([issue])
    assert "repetiti" in hint.lower() or "repeated" in hint.lower()


def test_retry_hint_untranslated():
    issue = QualityIssue(SEVERITY_ERROR, "UNTRANSLATED", "identical", "")
    hint = QualityChecker.build_retry_hint([issue])
    assert "translat" in hint.lower()


def test_retry_hint_multiple_issues():
    issues = [
        QualityIssue(SEVERITY_ERROR, "MISSING_TAG", "tag", "<alias=x>"),
        QualityIssue(SEVERITY_WARNING, "SOURCE_LANGUAGE_LEAK", "russian", "ы э"),
    ]
    hint = QualityChecker.build_retry_hint(issues)
    assert hint.count("•") == 2


def test_retry_hint_missing_url():
    issue = QualityIssue(SEVERITY_ERROR, "MISSING_URL", "url lost", "https://foo.com")
    hint = QualityChecker.build_retry_hint([issue])
    assert "https://foo.com" in hint


# ── issue_can_autofix / issue_needs_retranslation ─────────────────────────────

def test_autofix_codes_set():
    assert QualityChecker.issue_can_autofix("MISSING_NEWLINES")
    assert QualityChecker.issue_can_autofix("SOURCE_LANGUAGE_LEAK")
    assert QualityChecker.issue_can_autofix("REPETITIVE_CONTENT")  # repetition can be stripped
    assert not QualityChecker.issue_can_autofix("UNTRANSLATED")


def test_retranslate_codes_set():
    assert QualityChecker.issue_needs_retranslation("UNTRANSLATED")
    assert QualityChecker.issue_needs_retranslation("REPETITIVE_CONTENT")
    assert QualityChecker.issue_needs_retranslation("EMPTY_TRANSLATION")
    assert not QualityChecker.issue_needs_retranslation("LEADING_WHITESPACE_REMOVED")


# ── Code-set completeness ─────────────────────────────────────────────────────

def test_autofix_and_retranslate_codes_are_frozensets():
    assert isinstance(AUTOFIX_CODES, frozenset)
    assert isinstance(RETRANSLATE_CODES, frozenset)


def test_missing_tag_in_autofix():
    assert "MISSING_TAG" in AUTOFIX_CODES


def test_glossary_mismatch_in_retranslate():
    assert "GLOSSARY_MISMATCH" in RETRANSLATE_CODES


# ── check_all integration ─────────────────────────────────────────────────────

def test_check_all_returns_only_issues():
    rows = [
        {"id": 1, "original": "Hello", "translated": "Привіт"},
        {"id": 2, "original": "Fire!", "translated": ""},
        {"id": 3, "original": "Бум бахання тут", "translated": "Бум бахання тут"},  # untranslated Cyrillic
    ]
    checker = make_checker()
    reports = checker.check_all(rows)
    # Row 0 is clean — should not appear
    assert all(r.row_index != 0 for r in reports)
    row_indices = {r.row_index for r in reports}
    assert 1 in row_indices  # empty
    assert 2 in row_indices  # untranslated


def test_check_all_with_encoding():
    rows = [{"id": 1, "original": "Hi", "translated": "Привіт"}]
    checker = make_checker(target_encoding="cp1252")
    reports = checker.check_all(rows)
    assert any(r.row_index == 0 for r in reports)
    assert any("ENCODING_ERROR" in {i.code for i in r.issues} for r in reports)


# ── _extract_tags helper ──────────────────────────────────────────────────────

def test_extract_tags_alias():
    tags = _extract_tags("<Alias=NPC> and <Alias=Player>")
    assert tags["<alias=npc>"] == 1
    assert tags["<alias=player>"] == 1


def test_extract_tags_br():
    tags = _extract_tags("Line one<br/>Line two")
    assert tags["<br/>"] == 1


def test_extract_tags_escaped_newline():
    tags = _extract_tags("First\\nSecond")
    assert tags["\\n"] == 1


# ── _find_repeated_ngram helper ───────────────────────────────────────────────

def test_find_repeated_ngram_detects():
    text = "the cat sat on the mat " * 4
    gram = _find_repeated_ngram(text)
    assert gram is not None


def test_find_repeated_ngram_clean():
    gram = _find_repeated_ngram("The quick brown fox jumps over the lazy dog")
    assert gram is None
