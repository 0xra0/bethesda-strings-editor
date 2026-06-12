"""
Ukrainian register (ти/ви) consistency checker.

Groups translated strings by inferred speaker (EDID prefix in ESP mode or
whole-file in strings mode) and flags speakers whose lines mix informal-ти
address with formal-ви address when speaking to the player.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List


# ── Marker patterns ───────────────────────────────────────────────────────────
# Possessives (твій/ваш family) are weighted highest — they unambiguously
# signal address direction. Bare pronouns (ти/ви) are included but note that
# "ви" alone can be genuinely plural, not formal singular.

_RE_TY = re.compile(
    r"\b(ти|тебе|тобі|тобою"
    r"|твій|твоя|твоє|твої|твого|твоєї|твоїй|твою|твоїм|твоїми|твоїх)\b",
    re.IGNORECASE | re.UNICODE,
)

_RE_VY = re.compile(
    r"\b(ви|вас|вам|вами"
    r"|ваш|ваша|ваше|ваші|вашого|вашої|вашій|вашу|вашим|вашими|ваших)\b",
    re.IGNORECASE | re.UNICODE,
)

# Record field-signatures that carry dialogue text worth checking.
# In ESP mode row["offset"] is "RECORD FIELD" e.g. "INFO NAM1".
_DIALOGUE_FIELDS = frozenset({"NAM1", "RNAM", "FULL", "DNAM", "DESC"})


@dataclass
class RegisterHit:
    row_index: int
    string_id: int
    text: str
    markers: List[str]


@dataclass
class RegisterGroup:
    """All hits for one inferred speaker."""

    speaker_key: str           # e.g. "barrett" / "_file_" / "_unknown_"
    ty_hits: List[RegisterHit] = field(default_factory=list)
    vy_hits: List[RegisterHit] = field(default_factory=list)

    @property
    def is_inconsistent(self) -> bool:
        return bool(self.ty_hits) and bool(self.vy_hits)

    @property
    def total(self) -> int:
        return len(self.ty_hits) + len(self.vy_hits)


# ── EDID → speaker key ────────────────────────────────────────────────────────

_STRIP_PREFIXES = (
    "Dialogue",
    "Scene",
    "SE_",
    "CF_",
    "SFT_",
    "SF_",
)


def _speaker_key(edid: str) -> str:
    """Extract a normalised NPC-name key from a Bethesda EDID string."""
    if not edid:
        return "_unknown_"
    s = edid
    for prefix in _STRIP_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    # Strip leading DLC block identifiers (DLC01, DLC02 …)
    if len(s) > 5 and s[:3].upper() == "DLC" and s[3:5].isdigit():
        s = s[5:]
    # The first underscore-separated component is typically the NPC name.
    key = s.split("_")[0].strip().lower()
    return key or "_unknown_"


# ── Public API ────────────────────────────────────────────────────────────────

def check_register(rows: List[dict]) -> List[RegisterGroup]:
    """
    Scan *rows* (StringTableModel._data) for ти/ви register inconsistency.

    Returns RegisterGroup objects where is_inconsistent is True, sorted by
    descending total marker count (most problematic speaker first).
    """
    groups: Dict[str, RegisterGroup] = {}
    is_esp = any(row.get("offset") for row in rows[:20])  # quick probe

    for i, row in enumerate(rows):
        translated = (row.get("translated") or "").strip()
        if not translated:
            continue
        if row.get("status", "pending") not in ("translated", "approved"):
            continue

        # In ESP mode, skip non-dialogue record fields.
        if is_esp:
            offset = row.get("offset", "")
            field_sig = offset.split()[-1] if offset else ""
            if field_sig not in _DIALOGUE_FIELDS:
                continue

        edid = row.get("length", "") if is_esp else ""
        speaker = _speaker_key(edid) if is_esp else "_file_"

        ty = _RE_TY.findall(translated)
        vy = _RE_VY.findall(translated)
        if not ty and not vy:
            continue

        string_id = row.get("id", 0)
        grp = groups.setdefault(speaker, RegisterGroup(speaker_key=speaker))

        if ty:
            grp.ty_hits.append(RegisterHit(i, string_id, translated, ty))
        if vy:
            grp.vy_hits.append(RegisterHit(i, string_id, translated, vy))

    result = [g for g in groups.values() if g.is_inconsistent]
    result.sort(key=lambda g: -g.total)
    return result
