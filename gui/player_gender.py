"""
Player-reference detector.

English "you" and player-describing adjectives/verbs don't encode grammatical
gender, but many target languages do — so a source string that addresses or
describes the player is *gender-sensitive*: its translation depends on the
player character's gender (see ``AppSettings.player_gender`` and
``ollama_worker._player_gender_directive``).

This module flags which source strings are player-referring, so the translator
can see where the player-gender setting actually matters. Pure Python (no Qt),
heuristic and source-language-agnostic in spirit but tuned for English source:
whole-word second-person pronouns plus Bethesda player-reference placeholders.
"""

from __future__ import annotations

import re
from typing import List, Sequence

# Whole-word English second-person pronouns — the player is the "you" being
# addressed. Longer forms are listed first so the alternation prefers them, and
# \b boundaries keep "young"/"contour" from matching "you"/"your".
_SECOND_PERSON = re.compile(
    r"\b(?:yourselves|yourself|yours|your|you['’](?:re|ve|ll|d)|you)\b",
    re.IGNORECASE,
)

# Bethesda player-reference placeholders: [PLYR], [Player], <Alias=Player…>,
# <Alias.ShortName=Player…>.
_PLAYER_TOKEN = re.compile(
    r"\[PLYR\]|\[Player\]|<Alias(?:\.[A-Za-z]+)?=Player",
    re.IGNORECASE,
)


def is_player_referring(source_text: str) -> bool:
    """True if *source_text* addresses or names the player (gender-sensitive)."""
    if not source_text:
        return False
    return bool(_SECOND_PERSON.search(source_text) or _PLAYER_TOKEN.search(source_text))


def find_player_referring_rows(rows: Sequence[dict], *, key: str = "original") -> List[int]:
    """Indices of *rows* whose source text (``row[key]``) is player-referring.

    ``rows`` are ``StringTableModel`` row dicts, whose source text lives under
    ``"original"`` in both display modes.
    """
    out: List[int] = []
    for i, row in enumerate(rows):
        text = row.get(key, "") if isinstance(row, dict) else ""
        if is_player_referring(text or ""):
            out.append(i)
    return out
