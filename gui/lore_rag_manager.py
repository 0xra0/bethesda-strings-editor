"""
LoreRAGManager — retrieves lore context for a source string and formats it
for injection into the translation prompt (user turn).

Architecture:
  - Source text is cleaned of game tokens/tags, then proper-noun phrases are
    extracted as FTS5 query terms (longest first for precision).
  - Falls back to the first 60 chars of the cleaned text if no proper nouns
    are found.
  - Results from LoreDB.search() are joined as a compact inline snippet
    capped at *max_snippet_chars*.  Bold markers from FTS5 snippet() are
    stripped to keep the text clean.

The snippet is injected in the *user turn* (not the system prompt) so Claude's
system-prompt caching is not broken by per-string variation.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bethesda_strings.lore_db import LoreDB


# ── Text cleaning ─────────────────────────────────────────────────────────────

_STRIP_TOKENS_RE = re.compile(
    r"\[\[(?:STRUCT_BREAK_\w+|TK_\w+|BTAG_\w+)\]\]"  # protected tokens
    r"|<(?:Alias|Global|Token|Base|ActorValue|PlayerName)[^>]+>"  # game tags
    r"|%[-+ #0]*[\d.]*[sdfgexXoubcpn%]"  # printf format specs
    r"|\[[A-Z][a-zA-Z0-9_]*\]"            # binding tokens [Attack] etc.
)

# Capitalised multi-word phrase: "House Va'ruun", "Akila City", "Freestar Collective"
_PROPER_NOUN_RE = re.compile(
    r"\b([A-Z][a-zA-Z']+(?:\s+[A-Z][a-zA-Z']+){0,3})\b"
)


def _extract_query_terms(text: str) -> str:
    """Return an FTS query string from the most prominent proper nouns in *text*."""
    clean = _STRIP_TOKENS_RE.sub(" ", text).strip()
    nouns = _PROPER_NOUN_RE.findall(clean)
    if nouns:
        # Deduplicate, longest first (multi-word phrases are more specific)
        unique = sorted(set(nouns), key=len, reverse=True)[:6]
        return " ".join(unique)
    # Fallback: first 60 chars of cleaned text
    return clean[:60].strip()


# ── Manager ───────────────────────────────────────────────────────────────────

class LoreRAGManager:
    """Queries the lore database and formats context snippets for AI prompts.

    Typical usage (on the worker thread, same pattern as GlossaryManager)::

        snippet = lore_rag_manager.get_snippet(source_text)
        # snippet is "" when nothing relevant is found
    """

    def __init__(
        self,
        db: "LoreDB",
        max_snippet_chars: int = 480,
        max_results: int = 3,
    ) -> None:
        self.db = db
        self.max_snippet_chars = max_snippet_chars
        self.max_results = max_results
        self.enabled = True

    def get_snippet(self, source_text: str) -> str:
        """Return a formatted lore context string, or '' if nothing relevant.

        The snippet is safe to embed directly in the user-turn prompt prefix::

            "Lore context: [House Va'ruun] The religious faction … | [Akila City] …"
        """
        if not self.enabled or not source_text or not source_text.strip():
            return ""
        query = _extract_query_terms(source_text)
        if not query:
            return ""
        results = self.db.search(query, max_results=self.max_results)
        if not results:
            return ""

        parts: list[str] = []
        chars_used = 0
        for hit in results:
            # Strip FTS bold markers; keep the title as an anchor
            excerpt = re.sub(r"</?b>", "", hit.get("excerpt", "")).strip()
            snippet = f"[{hit['title']}] {excerpt}"
            if chars_used + len(snippet) > self.max_snippet_chars:
                remaining = self.max_snippet_chars - chars_used
                if remaining < 40:
                    break
                snippet = snippet[:remaining].rsplit(" ", 1)[0] + "…"
            parts.append(snippet)
            chars_used += len(snippet) + 3  # separator " | "
        return " | ".join(parts)
