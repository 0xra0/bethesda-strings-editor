"""
Tests for last_code_block — the "Use as Translation" extraction.

The panel used to scan the rendered chat widget for ``` fences, but its reply
handlers rewrite every fence into a <pre> element before display, so the
rendered text has no backticks in it at all.  The button's enable check ran
against the raw reply and lit up, then applying it always reported "no code
block found": the feature could never work once.  Reading the stored history
instead is what fixes it, so these tests read history.

Pure Python; no Qt widgets are constructed.
"""

from gui.claude_chat_panel import last_code_block


def assistant(content):
    return {"role": "assistant", "content": content}


def user(content):
    return {"role": "user", "content": content}


def test_extracts_a_fenced_block():
    history = [
        user("Translate this"),
        assistant("Here is my suggestion:\n```\nПерезарядити зброю\n```"),
    ]

    assert last_code_block(history) == "Перезарядити зброю"


def test_language_tag_is_not_part_of_the_suggestion():
    history = [assistant("Suggestion:\n```korean\n무기 재장전\n```")]

    assert last_code_block(history) == "무기 재장전"


def test_takes_the_last_block_of_the_last_reply():
    history = [
        assistant("First idea:\n```\nOld one\n```"),
        assistant("Better:\n```\nFirst try\n```\nOr rather:\n```\nFinal answer\n```"),
    ]

    assert last_code_block(history) == "Final answer"


def test_falls_back_to_an_earlier_reply():
    """A follow-up reply with no block must not hide the suggestion above it."""
    history = [
        assistant("Suggestion:\n```\nПерезарядити зброю\n```"),
        assistant("Yes, that reads naturally in context."),
    ]

    assert last_code_block(history) == "Перезарядити зброю"


def test_ignores_blocks_the_user_typed():
    history = [
        assistant("Suggestion:\n```\nFrom Claude\n```"),
        user("What about ```From the user```?"),
    ]

    assert last_code_block(history) == "From Claude"


def test_multiline_block_keeps_its_line_breaks():
    history = [assistant("```\nLine one\nLine two\n```")]

    assert last_code_block(history) == "Line one\nLine two"


def test_no_block_returns_empty():
    history = [assistant("I would leave this string untranslated.")]

    assert last_code_block(history) == ""


def test_empty_history_returns_empty():
    assert last_code_block([]) == ""


def test_malformed_history_entries_do_not_raise():
    assert last_code_block([{"role": "assistant"}, {}, {"content": None}]) == ""
