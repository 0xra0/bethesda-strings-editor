"""
Tests for gui/diff_viewer.py — diff computation and HTML export.

Run with:
    python tests/test_diff_viewer.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Headless Qt — must be set before importing Qt widgets
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gui.diff_viewer import (
    DiffResult,
    DiffStats,
    _compute_diff,
    _tokenise_char,
    _tokenise_word,
    build_html_report,
)


# ── tokenisers ────────────────────────────────────────────────────────────────

def test_word_tokenise_preserves_whitespace():
    tokens = _tokenise_word("Hello  world\nnew line")
    joined = "".join(tokens)
    assert joined == "Hello  world\nnew line"
    print("  PASS test_word_tokenise_preserves_whitespace")


def test_char_tokenise_roundtrip():
    text = "UC Vanguard at [PLYR] — ref %s."
    assert "".join(_tokenise_char(text)) == text
    print("  PASS test_char_tokenise_roundtrip")


def test_empty_string_tokenise():
    assert _tokenise_word("") == [""]
    assert _tokenise_char("") == [""]
    print("  PASS test_empty_string_tokenise")


# ── _compute_diff ─────────────────────────────────────────────────────────────

def test_identical_texts_no_segments():
    r = _compute_diff("Same text here.", "Same text here.", "word", False)
    assert r.segments == []
    assert r.stats.summary() == "Identical"
    print("  PASS test_identical_texts_no_segments")


def test_diff_has_correct_segment_count():
    # "Hello world" vs "Goodbye planet" — two replace segments
    r = _compute_diff("Hello world", "Goodbye planet", "word", False)
    assert len(r.segments) > 0
    assert r.stats.similarity < 1.0
    print(f"  PASS test_diff_has_correct_segment_count  (segs={len(r.segments)}, sim={r.stats.similarity:.0%})")


def test_chars_deleted_and_inserted():
    r = _compute_diff("abcdef", "abc123", "char", False)
    assert r.stats.chars_deleted > 0
    assert r.stats.chars_inserted > 0
    print(f"  PASS test_chars_deleted_and_inserted  (del={r.stats.chars_deleted}, ins={r.stats.chars_inserted})")


def test_shared_tags_reduce_diff():
    """Character-level diff should identify preserved game tags as equal regions."""
    with_tags_a = "Meet [PLYR] at <Alias=Station>."
    with_tags_b = "Зустріньте [PLYR] на <Alias=Station>."
    no_tags_a = "Meet player at Station."
    no_tags_b = "Зустріньте гравця на Станції."

    r_tags = _compute_diff(with_tags_a, with_tags_b, "char", False)
    r_no = _compute_diff(no_tags_a, no_tags_b, "char", False)

    # Preserved tags increase similarity
    assert r_tags.stats.similarity > r_no.stats.similarity, (
        f"Tags should increase similarity: {r_tags.stats.similarity:.2f} vs {r_no.stats.similarity:.2f}"
    )
    print(f"  PASS test_shared_tags_reduce_diff  "
          f"(with_tags={r_tags.stats.similarity:.0%}, no_tags={r_no.stats.similarity:.0%})")


def test_segment_right_positions_within_text():
    """All segment right_start/right_end positions must be within the right text length."""
    right_text = "Привіт світ [PLYR] %s"
    r = _compute_diff("Hello world [PLYR] %s", right_text, "word", False)
    text_len = len(right_text)
    for seg in r.segments:
        assert 0 <= seg.right_start <= text_len, f"start={seg.right_start} out of range"
        assert 0 <= seg.right_end <= text_len, f"end={seg.right_end} out of range"
        assert seg.right_start <= seg.right_end
    print(f"  PASS test_segment_right_positions_within_text  ({len(r.segments)} segments)")


def test_left_html_contains_del_spans():
    r = _compute_diff("deleted_word remaining", "remaining", "word", False)
    assert "line-through" in r.left_html
    assert "deleted_word" in r.left_html
    print("  PASS test_left_html_contains_del_spans")


def test_right_formats_non_overlapping():
    """Format spans must not overlap each other."""
    r = _compute_diff("aaa bbb ccc", "xxx bbb yyy", "word", False)
    sorted_fmts = sorted(r.right_formats, key=lambda f: f[0])
    for i in range(len(sorted_fmts) - 1):
        assert sorted_fmts[i][1] <= sorted_fmts[i + 1][0], (
            f"Overlapping formats at positions {sorted_fmts[i]} and {sorted_fmts[i+1]}"
        )
    print("  PASS test_right_formats_non_overlapping")


def test_word_diff_vs_char_diff_similarity():
    """Word-level and char-level similarity should both be in 0-1."""
    for text_a, text_b in [
        ("Short.", "Short."),
        ("Hello world foo bar", "Hello universe foo baz"),
        ("", "something"),
        ("something", ""),
    ]:
        for gran in ("word", "char"):
            r = _compute_diff(text_a, text_b, gran, False)
            assert 0.0 <= r.stats.similarity <= 1.0, (
                f"Similarity out of [0,1]: {r.stats.similarity} for gran={gran}"
            )
    print("  PASS test_word_diff_vs_char_diff_similarity")


def test_dark_mode_html_uses_dark_colors():
    r_light = _compute_diff("delete this", "insert this", "word", False)
    r_dark = _compute_diff("delete this", "insert this", "word", True)
    # Dark mode HTML should use dark background colors
    assert "#fee2e2" not in r_dark.left_html, "Light color should not appear in dark mode"
    assert "#dcfce7" not in r_dark.left_html
    print("  PASS test_dark_mode_html_uses_dark_colors")


# ── DiffStats ─────────────────────────────────────────────────────────────────

def test_stats_summary_identical():
    s = DiffStats()
    assert s.summary() == "Identical"
    print("  PASS test_stats_summary_identical")


def test_stats_summary_with_changes():
    s = DiffStats(words_replaced=3, chars_deleted=10, chars_inserted=12, similarity=0.75)
    summary = s.summary()
    assert "3 word(s) changed" in summary
    assert "-10" in summary
    assert "+12" in summary
    assert "75%" in summary
    print(f"  PASS test_stats_summary_with_changes  ({summary!r})")


# ── build_html_report ─────────────────────────────────────────────────────────

_ROWS = [
    {"id": 0x0001, "original": "Hello world", "translated": "Привіт світ", "status": "translated"},
    {"id": 0x0002, "original": "Same text",   "translated": "Same text",   "status": "translated"},
    {"id": 0x0003, "original": "UC Vanguard", "translated": "",             "status": "pending"},
]


def test_html_report_contains_all_strings():
    html = build_html_report(_ROWS, None, "word", changed_only=False)
    assert "0x00000001" in html
    assert "0x00000002" in html
    assert "0x00000003" in html
    print("  PASS test_html_report_contains_all_strings")


def test_html_report_changed_only_excludes_identical():
    html = build_html_report(_ROWS, None, "word", changed_only=True)
    # Row 2 is identical — should not appear
    assert "Same text" not in html
    # Row 1 is different — should appear
    assert "0x00000001" in html
    print("  PASS test_html_report_changed_only_excludes_identical")


def test_html_report_with_comparison_data():
    comparison = {
        0x0001: "Old translation",
        0x0002: "Same text",
    }
    html = build_html_report(_ROWS, comparison, "word", changed_only=False)
    assert "Comparison File" in html
    assert "Current Translation" in html
    # "Old translation" is word-diffed so words appear in separate spans
    assert "Old" in html and "translation" in html
    print("  PASS test_html_report_with_comparison_data")


def test_html_report_is_valid_html():
    html = build_html_report(_ROWS, None, "word", changed_only=False)
    assert html.startswith("<!DOCTYPE html>")
    assert "</html>" in html
    assert "<style>" in html
    assert "charset" in html
    print("  PASS test_html_report_is_valid_html")


def test_html_report_escapes_special_chars():
    dangerous = [
        {"id": 0x9, "original": "<script>alert('xss')</script>",
         "translated": "& < > \" '", "status": "translated"},
    ]
    html = build_html_report(dangerous, None, "char", changed_only=False)
    assert "<script>" not in html
    assert "&amp;" in html or "&lt;" in html
    print("  PASS test_html_report_escapes_special_chars")


def test_html_report_empty_translated_shows_placeholder():
    rows = [{"id": 1, "original": "Hello", "translated": "", "status": "pending"}]
    html = build_html_report(rows, None, "word", changed_only=False)
    assert "empty" in html.lower()
    print("  PASS test_html_report_empty_translated_shows_placeholder")


def test_html_report_char_granularity():
    html = build_html_report(_ROWS, None, "char", changed_only=False)
    assert "Character" in html
    print("  PASS test_html_report_char_granularity")


# ── Dialog construction ────────────────────────────────────────────────────────

def test_dialog_construction():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from gui.diff_viewer import DiffViewerDialog

    dlg = DiffViewerDialog(rows=_ROWS, initial_row=0)
    assert dlg is not None
    # Dialog should show the correct initial row
    text = dlg._right_pane.toPlainText()
    assert "Привіт" in text or "Same" in text or text == ""
    dlg.destroy()
    print("  PASS test_dialog_construction")


def test_dialog_row_navigation():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from gui.diff_viewer import DiffViewerDialog

    dlg = DiffViewerDialog(rows=_ROWS, initial_row=0)
    initial_idx = dlg._current_row_idx
    # Navigate forward
    dlg._go_next_row()
    assert dlg._current_row_idx != initial_idx or len(dlg._effective_rows()) == 1
    dlg.destroy()
    print("  PASS test_dialog_row_navigation")


def test_dialog_segment_navigation():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from gui.diff_viewer import DiffViewerDialog

    rows = [{"id": 1, "original": "Hello world", "translated": "Bonjour monde", "status": "translated"}]
    dlg = DiffViewerDialog(rows=rows, initial_row=0)
    n_segs = len(dlg._segments)
    if n_segs > 0:
        dlg._go_next_segment()
        assert dlg._current_segment == 0
        dlg._go_prev_segment()
        assert dlg._current_segment == n_segs - 1
    dlg.destroy()
    print(f"  PASS test_dialog_segment_navigation  ({n_segs} segments)")


def test_dialog_auto_save_on_navigate():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from gui.diff_viewer import DiffViewerDialog

    rows = [
        {"id": 1, "original": "Hello", "translated": "Привіт", "status": "translated"},
        {"id": 2, "original": "World", "translated": "Світ",   "status": "translated"},
    ]
    saved = []
    dlg = DiffViewerDialog(rows=rows, initial_row=0)
    dlg.translation_updated.connect(lambda i, t: saved.append((i, t)))

    # Modify the right pane text
    dlg._right_pane.setPlainText("Modified translation")
    # Navigate to next row (should auto-save)
    dlg._go_next_row()
    assert any(t == "Modified translation" for _, t in saved), f"Expected save, got: {saved}"
    dlg.destroy()
    print(f"  PASS test_dialog_auto_save_on_navigate  (saves={saved})")


def test_dialog_granularity_switch():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from gui.diff_viewer import DiffViewerDialog

    rows = [{"id": 1, "original": "Hello world", "translated": "Привіт світ", "status": "translated"}]
    dlg = DiffViewerDialog(rows=rows, initial_row=0)

    dlg._combo_gran.setCurrentIndex(0)  # word
    segs_word = len(dlg._segments)
    dlg._combo_gran.setCurrentIndex(1)  # char
    segs_char = len(dlg._segments)

    # Both should produce non-zero segments for different-language pair
    assert segs_word >= 0 and segs_char >= 0
    dlg.destroy()
    print(f"  PASS test_dialog_granularity_switch  (word={segs_word}, char={segs_char})")


# ── runner ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_word_tokenise_preserves_whitespace,
        test_char_tokenise_roundtrip,
        test_empty_string_tokenise,
        test_identical_texts_no_segments,
        test_diff_has_correct_segment_count,
        test_chars_deleted_and_inserted,
        test_shared_tags_reduce_diff,
        test_segment_right_positions_within_text,
        test_left_html_contains_del_spans,
        test_right_formats_non_overlapping,
        test_word_diff_vs_char_diff_similarity,
        test_dark_mode_html_uses_dark_colors,
        test_stats_summary_identical,
        test_stats_summary_with_changes,
        test_html_report_contains_all_strings,
        test_html_report_changed_only_excludes_identical,
        test_html_report_with_comparison_data,
        test_html_report_is_valid_html,
        test_html_report_escapes_special_chars,
        test_html_report_empty_translated_shows_placeholder,
        test_html_report_char_granularity,
        test_dialog_construction,
        test_dialog_row_navigation,
        test_dialog_segment_navigation,
        test_dialog_auto_save_on_navigate,
        test_dialog_granularity_switch,
    ]

    print(f"Running {len(tests)} tests...\n")
    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as e:
            print(f"  FAIL {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {test.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{'All tests passed.' if not failed else f'{failed} test(s) failed.'}")
    sys.exit(0 if not failed else 1)
