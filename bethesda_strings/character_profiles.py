"""Character persona profiles for AI translation.

Each profile encodes how a specific NPC or faction speaks so the AI adapts
its register, vocabulary, and style per string.  Two persistence layers:

- **Profiles**  → ``~/.config/bse/character_profiles.json``
  (shared across all projects; built-in profiles are embedded and cannot be
  deleted, but their ``system_addendum`` can be freely edited).

- **Assignments** → ``~/.config/bse/profile_assignments/{file_hash}.json``
  (per-file mapping of string_id → profile_id, loaded/saved with the file).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class CharacterProfile:
    """One persona configuration that customises the AI system prompt."""

    profile_id: str            # stable UUID slug
    name: str                  # display name (e.g. "Freestar Ranger")
    description: str           # one-line UI hint
    color: str                 # hex "#RRGGBB" — badge + table tint

    # AI settings
    temperature: Optional[float]    # None = inherit worker default
    system_addendum: str            # appended verbatim to the system prompt

    # Structured voice options (used by the addendum generator)
    formality: str             # "casual" | "neutral" | "formal"
    allow_contractions: bool
    custom_instructions: List[str] = field(default_factory=list)

    is_builtin: bool = False   # built-in profiles survive user resets

    def generate_addendum(self) -> str:
        """Build a system_addendum from the structured fields."""
        formality_map = {
            "casual":  "casual, informal",
            "neutral": "neutral, standard",
            "formal":  "formal, professional",
        }
        register = formality_map.get(self.formality, self.formality)
        contractions = (
            "Contractions are natural and expected."
            if self.allow_contractions
            else "Avoid contractions — use full forms."
        )
        lines = [
            f"Character: {self.name} ({register} register)",
            contractions,
        ]
        lines.extend(self.custom_instructions)
        return "\n".join(lines)


# ── Built-in profiles ─────────────────────────────────────────────────────────

def _builtin(
    pid: str,
    name: str,
    description: str,
    color: str,
    temperature: Optional[float],
    formality: str,
    allow_contractions: bool,
    addendum: str,
    custom: Optional[List[str]] = None,
) -> CharacterProfile:
    return CharacterProfile(
        profile_id=pid,
        name=name,
        description=description,
        color=color,
        temperature=temperature,
        system_addendum=addendum,
        formality=formality,
        allow_contractions=allow_contractions,
        custom_instructions=custom or [],
        is_builtin=True,
    )


BUILTIN_PROFILES: List[CharacterProfile] = [
    _builtin(
        "builtin-freestar-ranger",
        "Freestar Ranger",
        "Casual frontier/western-sci-fi tone",
        "#E07820",
        temperature=0.25,
        formality="casual",
        allow_contractions=True,
        addendum=(
            "Character: Freestar Ranger (casual frontier, western-sci-fi register)\n"
            "Use informal, down-to-earth language. Contractions are natural (I'm, we'll, can't). "
            "Short, punchy sentences. Avoid bureaucratic or overly formal vocabulary. "
            "Dry wit and plain-spoken directness fit this character."
        ),
    ),
    _builtin(
        "builtin-sysdef-officer",
        "SysDef Officer",
        "Formal military / law enforcement register",
        "#2060C0",
        temperature=0.07,
        formality="formal",
        allow_contractions=False,
        addendum=(
            "Character: SysDef Officer (formal military register)\n"
            "Use precise, professional military-style language. No contractions. "
            "Complete sentences. Maintain rank-appropriate formality. "
            "Prefer official terminology over colloquialisms. "
            "Project authority and discipline in every line."
        ),
    ),
    _builtin(
        "builtin-crimson-fleet",
        "Crimson Fleet Pirate",
        "Rough, threatening, crude register",
        "#C02020",
        temperature=0.30,
        formality="casual",
        allow_contractions=True,
        addendum=(
            "Character: Crimson Fleet pirate (rough, threatening register)\n"
            "Use crude, direct, intimidating language. Short aggressive sentences. "
            "Threats and menace are appropriate to the character. "
            "Contractions and rough grammar are fine. "
            "Avoid anything polished or overly formal."
        ),
    ),
    _builtin(
        "builtin-house-varuun",
        "House Va'ruun Zealot",
        "Formal religious / fanatical register",
        "#7030A0",
        temperature=0.15,
        formality="formal",
        allow_contractions=False,
        addendum=(
            "Character: House Va'ruun zealot (formal religious register)\n"
            "Use reverent, poetic, and formal language. "
            "References to the Great Serpent and faith are tone-appropriate. "
            "Elaborate sentence structures are welcome. No colloquialisms. "
            "Convey fanatical devotion and cosmic awe through word choice."
        ),
    ),
    _builtin(
        "builtin-uc-civilian",
        "UC Civilian",
        "Neutral contemporary speech",
        "#208060",
        temperature=0.15,
        formality="neutral",
        allow_contractions=True,
        addendum=(
            "Character: United Colonies civilian (neutral, contemporary register)\n"
            "Standard modern speech — neither stiff nor overly casual. "
            "Natural contractions are fine. Everyday vocabulary. "
            "City-dweller, urban tone rather than frontier."
        ),
    ),
    _builtin(
        "builtin-robot",
        "Robot / Automaton",
        "Technical, systematic, no contractions",
        "#606060",
        temperature=0.05,
        formality="formal",
        allow_contractions=False,
        addendum=(
            "Character: Robot or automated system (technical, precise register)\n"
            "No contractions. Precise, systematic language. "
            "Avoid metaphor and ambiguity. Technical vocabulary is appropriate. "
            "Machine-like clarity and logical sentence structure."
        ),
    ),
    _builtin(
        "builtin-narrator",
        "Narrator / Item Text",
        "Neutral descriptive text for items, logs, and narration",
        "#505050",
        temperature=None,
        formality="neutral",
        allow_contractions=False,
        addendum=(
            "Register: Narration or item description (neutral, literary)\n"
            "Clear, professional descriptive language. "
            "Third person preferred where appropriate. "
            "Well-constructed sentences. Balanced formality."
        ),
    ),
]


# ── Profile manager ───────────────────────────────────────────────────────────

class ProfileManager:
    """Loads, persists, and provides CRUD access to CharacterProfiles."""

    def __init__(self, config_dir: Path) -> None:
        self._dir = config_dir
        self._profiles: Dict[str, CharacterProfile] = {}
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _profiles_path(self) -> Path:
        return self._dir / "character_profiles.json"

    def _load(self) -> None:
        """Merge built-in defaults with any user-saved customisations."""
        # Start with built-ins (copies, so user edits don't mutate the templates)
        for p in BUILTIN_PROFILES:
            self._profiles[p.profile_id] = _copy_profile(p)

        path = self._profiles_path()
        if not path.is_file():
            return
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        for raw in saved.get("profiles", []):
            pid = raw.get("profile_id", "")
            if not pid:
                continue
            try:
                p = CharacterProfile(
                    profile_id=pid,
                    name=raw.get("name", "Unnamed"),
                    description=raw.get("description", ""),
                    color=raw.get("color", "#808080"),
                    temperature=raw.get("temperature"),
                    system_addendum=raw.get("system_addendum", ""),
                    formality=raw.get("formality", "neutral"),
                    allow_contractions=raw.get("allow_contractions", True),
                    custom_instructions=raw.get("custom_instructions", []),
                    is_builtin=raw.get("is_builtin", False),
                )
                self._profiles[pid] = p
            except Exception:
                continue

    def save(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        data = {"profiles": [_profile_to_dict(p) for p in self._profiles.values()]}
        self._profiles_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def all(self) -> List[CharacterProfile]:
        # Built-ins first (in definition order), then user-created alphabetically
        builtin_ordered = [
            self._profiles[p.profile_id]
            for p in BUILTIN_PROFILES
            if p.profile_id in self._profiles
        ]
        user_created = sorted(
            [p for p in self._profiles.values() if not p.is_builtin],
            key=lambda p: p.name.lower(),
        )
        return builtin_ordered + user_created

    def get(self, profile_id: str) -> Optional[CharacterProfile]:
        return self._profiles.get(profile_id)

    def upsert(self, profile: CharacterProfile) -> None:
        self._profiles[profile.profile_id] = profile
        self.save()

    def delete(self, profile_id: str) -> bool:
        p = self._profiles.get(profile_id)
        if p is None or p.is_builtin:
            return False
        del self._profiles[profile_id]
        self.save()
        return True

    def new_profile(self) -> CharacterProfile:
        """Create a blank user profile with a unique ID."""
        return CharacterProfile(
            profile_id=f"user-{uuid.uuid4().hex[:12]}",
            name="New Profile",
            description="",
            color="#808080",
            temperature=None,
            system_addendum="",
            formality="neutral",
            allow_contractions=True,
            custom_instructions=[],
            is_builtin=False,
        )

    def duplicate(self, source_id: str) -> Optional[CharacterProfile]:
        src = self._profiles.get(source_id)
        if src is None:
            return None
        copy = _copy_profile(src)
        copy.profile_id = f"user-{uuid.uuid4().hex[:12]}"
        copy.name = f"{src.name} (copy)"
        copy.is_builtin = False
        self._profiles[copy.profile_id] = copy
        self.save()
        return copy


# ── Assignment persistence ────────────────────────────────────────────────────

class ProfileAssignments:
    """Per-file mapping of string_id → profile_id.

    Stored in ``<config_dir>/profile_assignments/<hash>.json`` so game files
    are never modified.
    """

    def __init__(self, config_dir: Path) -> None:
        self._dir = config_dir / "profile_assignments"
        self._file_path: Optional[Path] = None
        self._data: Dict[int, str] = {}     # string_id → profile_id

    def load(self, file_path: Path) -> None:
        """Load assignments for *file_path*, discarding any previous state."""
        self._data = {}
        h = hashlib.sha1(str(file_path).encode()).hexdigest()[:16]
        self._file_path = self._dir / f"{h}.json"
        if self._file_path.is_file():
            try:
                raw = json.loads(self._file_path.read_text(encoding="utf-8"))
                self._data = {int(k): v for k, v in raw.get("assignments", {}).items()}
            except (OSError, json.JSONDecodeError, ValueError):
                pass

    def save(self) -> None:
        if self._file_path is None:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file_path.write_text(
            json.dumps({"assignments": {str(k): v for k, v in self._data.items()}},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get(self, string_id: int) -> Optional[str]:
        return self._data.get(string_id)

    def set(self, string_id: int, profile_id: Optional[str]) -> None:
        if profile_id is None:
            self._data.pop(string_id, None)
        else:
            self._data[string_id] = profile_id

    def set_many(self, string_ids: List[int], profile_id: Optional[str]) -> None:
        for sid in string_ids:
            self.set(sid, profile_id)
        self.save()

    def all(self) -> Dict[int, str]:
        return dict(self._data)

    def clear(self) -> None:
        self._data.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _copy_profile(p: CharacterProfile) -> CharacterProfile:
    return CharacterProfile(
        profile_id=p.profile_id,
        name=p.name,
        description=p.description,
        color=p.color,
        temperature=p.temperature,
        system_addendum=p.system_addendum,
        formality=p.formality,
        allow_contractions=p.allow_contractions,
        custom_instructions=list(p.custom_instructions),
        is_builtin=p.is_builtin,
    )


def _profile_to_dict(p: CharacterProfile) -> dict:
    return {
        "profile_id": p.profile_id,
        "name": p.name,
        "description": p.description,
        "color": p.color,
        "temperature": p.temperature,
        "system_addendum": p.system_addendum,
        "formality": p.formality,
        "allow_contractions": p.allow_contractions,
        "custom_instructions": p.custom_instructions,
        "is_builtin": p.is_builtin,
    }
