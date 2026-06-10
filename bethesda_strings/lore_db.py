"""
Local SQLite FTS5 lore database for Bethesda game RAG.

Articles (lore pages from UESP, Starfield Wiki, or manual entries) are indexed
for BM25 full-text search.  The trigram tokeniser is preferred because it
handles apostrophes in names like Va'ruun without splitting; unicode61 is the
fallback for older SQLite builds (< 3.34).

Usage::

    db = LoreDB(Path("~/.config/bse/lore.sqlite"))
    db.upsert(LoreArticle("House Va'ruun", "…body…", "uesp", "faction,lore"))
    hits = db.search("Va'ruun faith", max_results=3)
    db.close()
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


# ── Markup stripping ─────────────────────────────────────────────────────────

_MARKUP_RE = re.compile(
    r"\{\{[^{}]*\}\}"          # {{template}}
    r"|\[\[(?:[^|\]]+\|)?([^\]]+)\]\]"  # [[link|text]] → keep text
    r"|<[^>]+>"                # HTML tags
    r"|={2,}[^=]+=+"           # == headings ==
    r"|\[\w+ [^\]]+\]"         # [external links]
)
_WS_RE = re.compile(r"\s{2,}")


def _strip_markup(text: str) -> str:
    """Very light MediaWiki/HTML markup stripper for snippet quality."""
    text = _MARKUP_RE.sub(lambda m: m.group(1) or " ", text)
    return _WS_RE.sub(" ", text).strip()


# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class LoreArticle:
    title: str
    content: str    # raw text (markup stripped on insert)
    source: str     # "uesp", "starfield-wiki", "manual", …
    tags: str = "" # comma-separated category labels


# ── Database ─────────────────────────────────────────────────────────────────

_MAX_CONTENT_CHARS = 8_000   # keep per-article storage bounded


class LoreDB:
    """SQLite FTS5 lore index with BM25-ranked search."""

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._tokenizer = self._init_schema()

    def _init_schema(self) -> str:
        """Create tables if absent.  Returns the tokeniser name in use."""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lore_fts'"
        )
        if cur.fetchone():
            row = self._conn.execute(
                "SELECT value FROM lore_meta WHERE key='tokenizer'"
            ).fetchone()
            return row[0] if row else "unicode61"

        for tokenizer in ("trigram", "unicode61"):
            try:
                with self._conn:
                    self._conn.execute("""
                        CREATE TABLE IF NOT EXISTS lore_articles (
                            id    INTEGER PRIMARY KEY AUTOINCREMENT,
                            title TEXT    NOT NULL UNIQUE,
                            source TEXT   NOT NULL,
                            tags  TEXT    DEFAULT '',
                            added_at REAL DEFAULT (unixepoch('now'))
                        )
                    """)
                    self._conn.execute(f"""
                        CREATE VIRTUAL TABLE IF NOT EXISTS lore_fts USING fts5(
                            title, content,
                            tokenize="{tokenizer}"
                        )
                    """)
                    self._conn.execute("""
                        CREATE TABLE IF NOT EXISTS lore_meta (
                            key TEXT PRIMARY KEY, value TEXT
                        )
                    """)
                    self._conn.execute(
                        "INSERT OR REPLACE INTO lore_meta VALUES ('tokenizer', ?)",
                        (tokenizer,),
                    )
                    self._conn.execute(
                        "INSERT OR REPLACE INTO lore_meta VALUES ('schema_version', '1')"
                    )
                return tokenizer
            except sqlite3.OperationalError:
                # Drop partial state before retrying with fallback tokeniser
                for tbl in ("lore_fts", "lore_articles", "lore_meta"):
                    self._conn.execute(f"DROP TABLE IF EXISTS {tbl}")
                self._conn.commit()

        raise RuntimeError("SQLite FTS5 is not available in this build")

    # ── Write ─────────────────────────────────────────────────────────────────

    def upsert(self, article: LoreArticle) -> None:
        """Insert or replace an article (matched by title)."""
        clean = _strip_markup(article.content)[:_MAX_CONTENT_CHARS]
        with self._conn:
            row = self._conn.execute(
                "SELECT id FROM lore_articles WHERE title = ?", (article.title,)
            ).fetchone()
            if row:
                art_id: int = row["id"]
                self._conn.execute(
                    "DELETE FROM lore_fts WHERE rowid = ?", (art_id,)
                )
                self._conn.execute(
                    "UPDATE lore_articles SET source=?, tags=?, added_at=unixepoch('now') WHERE id=?",
                    (article.source, article.tags, art_id),
                )
            else:
                cur = self._conn.execute(
                    "INSERT INTO lore_articles (title, source, tags) VALUES (?, ?, ?)",
                    (article.title, article.source, article.tags),
                )
                art_id = cur.lastrowid  # type: ignore[assignment]
            self._conn.execute(
                "INSERT INTO lore_fts (rowid, title, content) VALUES (?, ?, ?)",
                (art_id, article.title, clean),
            )

    def delete_by_source(self, source: str) -> int:
        """Remove all articles from *source*. Returns the number deleted."""
        ids = [
            r[0]
            for r in self._conn.execute(
                "SELECT id FROM lore_articles WHERE source = ?", (source,)
            ).fetchall()
        ]
        if not ids:
            return 0
        ph = ",".join("?" * len(ids))
        with self._conn:
            self._conn.execute(f"DELETE FROM lore_fts WHERE rowid IN ({ph})", ids)
            self._conn.execute(f"DELETE FROM lore_articles WHERE id IN ({ph})", ids)
        return len(ids)

    def delete_all(self) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM lore_fts")
            self._conn.execute("DELETE FROM lore_articles")

    # ── Read ──────────────────────────────────────────────────────────────────

    def search(self, query: str, max_results: int = 4) -> List[Dict]:
        """BM25-ranked FTS5 search.

        Returns a list of ``{"title", "excerpt", "score"}`` dicts where
        *excerpt* has bold markers (``<b>…</b>``) around matched terms.
        Returns ``[]`` on empty query or FTS5 error.
        """
        if not query.strip():
            return []
        # FTS5 treats apostrophes as string-literal delimiters in MATCH queries,
        # which causes "Va'ruun" to be mis-parsed as a quoted phrase.  Strip
        # them so "Va'ruun" → "Va ruun" — trigrams still find the article.
        safe = re.sub(r"[^\w\s\-]", " ", query).strip()
        if not safe:
            return []
        # Wrap in double-quotes to treat the phrase as a phrase query when it
        # contains multiple tokens, falling back to individual tokens if no hit.
        results = self._fts_query(safe, max_results)
        if not results and " " in safe:
            # Phrase query returned nothing — retry with individual tokens
            results = self._fts_query(" OR ".join(safe.split()), max_results)
        return results

    def _fts_query(self, query: str, max_results: int) -> List[Dict]:
        try:
            rows = self._conn.execute(
                """
                SELECT title,
                       snippet(lore_fts, 1, '<b>', '</b>', '…', 28) AS excerpt,
                       rank AS score
                FROM lore_fts
                WHERE lore_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (query, max_results),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def get_article_content(self, title: str) -> Optional[str]:
        """Return the stored (markup-stripped) content for *title*, or None."""
        row = self._conn.execute(
            "SELECT content FROM lore_fts WHERE title = ?", (title,)
        ).fetchone()
        return row[0] if row else None

    def article_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM lore_articles").fetchone()
        return row[0] if row else 0

    def sources(self) -> List[Dict]:
        """Return ``[{"source": …, "count": …}, …]`` sorted by count."""
        rows = self._conn.execute(
            "SELECT source, COUNT(*) AS count FROM lore_articles "
            "GROUP BY source ORDER BY count DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_articles(self, source: Optional[str] = None, limit: int = 200) -> List[Dict]:
        """Return ``[{"id", "title", "source", "tags"}, …]``."""
        if source:
            rows = self._conn.execute(
                "SELECT id, title, source, tags FROM lore_articles WHERE source=? "
                "ORDER BY title LIMIT ?",
                (source, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, title, source, tags FROM lore_articles ORDER BY title LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
