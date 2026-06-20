"""Tests for the changelog helpers in gui.updater.

Pure functions only — parse_releases / markdown_to_html / changelog_to_html.
No network: a captured GitHub releases-list payload is fed directly in.
"""

from gui import updater


_SAMPLE = [
    {
        "tag_name": "v0.2.3",
        "name": "v0.2.3",
        "published_at": "2026-06-20T10:00:00Z",
        "html_url": "https://github.com/x/y/releases/tag/v0.2.3",
        "draft": False,
        "prerelease": False,
        "body": "## Added\n- A **bold** thing\n- Uses `code`\n\nSee [docs](https://e.x).",
    },
    {
        "tag_name": "v0.2.2",
        "name": "Release 0.2.2",
        "published_at": "2026-06-11T09:00:00Z",
        "html_url": "https://github.com/x/y/releases/tag/v0.2.2",
        "draft": False,
        "prerelease": False,
        "body": "Bugfixes.",
    },
    {
        "tag_name": "v0.3.0-rc1",
        "name": "RC",
        "published_at": "2026-06-25T09:00:00Z",
        "html_url": "https://github.com/x/y/releases/tag/v0.3.0-rc1",
        "draft": True,  # draft → skipped
        "prerelease": True,
        "body": "secret",
    },
]


def test_parse_releases_skips_drafts_and_keeps_order():
    rels = updater.parse_releases(_SAMPLE)
    assert [r["tag"] for r in rels] == ["v0.2.3", "v0.2.2"]
    assert rels[0]["date"] == "2026-06-20"
    assert rels[0]["name"] == "v0.2.3"


def test_parse_releases_respects_limit():
    assert len(updater.parse_releases(_SAMPLE, limit=1)) == 1


def test_parse_releases_handles_empty_and_none():
    assert updater.parse_releases([]) == []
    assert updater.parse_releases(None) == []


def test_parse_releases_falls_back_for_missing_fields():
    rels = updater.parse_releases([{"tag_name": "v1.0"}])
    assert rels[0]["name"] == "v1.0"
    assert rels[0]["body"] == ""
    assert rels[0]["url"] == updater.RELEASES_URL


def test_markdown_headings_and_bullets():
    html = updater.markdown_to_html("## Added\n- one\n- two")
    assert "<h3>Added</h3>" in html
    assert html.count("<li>") == 2
    assert "<ul>" in html and "</ul>" in html


def test_markdown_inline_bold_code_link():
    html = updater.markdown_to_html("A **b** and `c` and [d](https://e.x)")
    assert "<b>b</b>" in html
    assert "<code>c</code>" in html
    assert '<a href="https://e.x">d</a>' in html


def test_markdown_escapes_html():
    html = updater.markdown_to_html("a < b & c > d")
    assert "&lt;" in html and "&amp;" in html and "&gt;" in html
    # The raw, unescaped angle brackets must not leak through as tags.
    assert "a < b" not in html


def test_markdown_empty_body():
    assert updater.markdown_to_html("") == ""
    assert updater.markdown_to_html(None) == ""


def test_changelog_to_html_marks_installed_version():
    rels = updater.parse_releases(_SAMPLE)
    html = updater.changelog_to_html(rels, current_version="0.2.2")
    assert "(installed)" in html
    # Titles for both releases present.
    assert "v0.2.3" in html and "Release 0.2.2" in html


def test_changelog_to_html_no_current_version():
    rels = updater.parse_releases(_SAMPLE)
    html = updater.changelog_to_html(rels)
    assert "(installed)" not in html


def test_truncate_md_caps_long_bodies():
    body = "\n".join(f"- item {i}" for i in range(100))
    text, truncated = updater._truncate_md(body, max_lines=10)
    assert truncated is True
    assert text.count("\n") < 100


def test_truncate_md_short_body_not_truncated():
    text, truncated = updater._truncate_md("- a\n- b", max_lines=24)
    assert truncated is False
    assert text == "- a\n- b"


def test_changelog_to_html_adds_read_more_when_truncated():
    big = [
        {
            "tag_name": "v1.0",
            "name": "v1.0",
            "published_at": "2026-01-01T00:00:00Z",
            "html_url": "https://github.com/x/y/releases/tag/v1.0",
            "body": "\n".join(f"- line {i}" for i in range(200)),
        }
    ]
    html = updater.changelog_to_html(updater.parse_releases(big))
    assert "read the full notes" in html
    assert "releases/tag/v1.0" in html
