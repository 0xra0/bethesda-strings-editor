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
