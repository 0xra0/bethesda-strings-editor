"""
Tests for review_translation's output budget.

A flat max_tokens=1024 truncated the review of any long string — books, notes,
terminal entries — mid-sentence.  Worse than a clipped review: the reply is
appended to the chat history verbatim, so the half-finished text then rode into
the next Suggest request as if Claude had meant to stop there.

Hermetic: the client is built via __new__ so the anthropic SDK is never
imported, and _create_message is replaced with a recorder.
"""

from gui.claude_client import ClaudeClient


def make_client():
    """A ClaudeClient that records the kwargs of every request it would send."""
    client = ClaudeClient.__new__(ClaudeClient)
    client.model = "claude-haiku-4-5"
    client.calls = []

    def _record(**kwargs):
        client.calls.append(kwargs)
        return type("R", (), {"content": [type("B", (), {"text": "review"})()]})()

    client._create_message = _record
    return client


def review_budget(original, translation):
    client = make_client()
    client.review_translation(original, translation, "en", "uk")
    return client.calls[0]["max_tokens"]


def test_short_string_keeps_a_workable_floor():
    budget = review_budget("Reload", "Перезарядити")

    assert budget >= 1024


def test_budget_grows_with_the_text_under_review():
    short = review_budget("Reload", "Перезарядити")
    long = review_budget("A" * 900, "Б" * 900)

    assert long > short


def test_long_book_entry_gets_room_for_a_full_review():
    """The old flat 1024 could not restate this, let alone improve it."""
    original = "The Settled Systems remember the Colony War. " * 40
    translation = "Заселені системи пам'ятають Колоніальну війну. " * 40

    budget = review_budget(original, translation)

    assert budget > 1024


def test_budget_is_capped():
    """An enormous string must not ask for an unbounded completion."""
    budget = review_budget("A" * 50_000, "Б" * 50_000)

    assert budget <= 4096


def test_budget_accounts_for_both_sides():
    """A review restates the translation, so its length matters too."""
    source_only = review_budget("A" * 700, "Б")
    both = review_budget("A" * 700, "Б" * 700)

    assert both > source_only
