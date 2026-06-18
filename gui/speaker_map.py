"""
Speaker (NPC) mapping for Starfield dialogue lines.

Starfield's dialogue is voiced, and the voice-type folder inside the
``*Voices*.ba2`` archives (see :mod:`bethesda_strings.wwise_voice`) is the most
reliable per-line "who is speaking" signal we can derive without a full
cross-record ESM index.  This module turns a raw voice-type folder name into a
human-friendly :class:`SpeakerInfo` (name, gender, faction, category) so a
translator gets immediate context — vital for keeping a character's voice
consistent across a branching dialogue tree.

Voice-type naming conventions observed in the shipped game:

* Named NPCs:   ``npcfsarahmorgan`` / ``npcmsamcoe`` / ``npcxsivan``
                (``npc`` + gender ``f``/``m``/``x`` + concatenated name).
                Some named entries omit the gender letter (``npccoopercarr``).
* Generic NPCs: ``<faction><gender><variant>`` —
                ``crimsonfleetfemale03``, ``genericmale05``, ``ucsecuritymale03``,
                ``genericfemaleaccent_french``, ``genericmalechild``.
* Announcers:   ``announcerf...`` / ``announcerm...`` (PA / ship / system voices).
* Creatures:    ``cr_...`` / ``crocopedea_...`` (alien fauna — not dialogue).
* Robots:       ``robotmodela...`` / ``robotminibota``.
* Crowd:        ``genericcrowd...`` (background chatter).
* Player:       ``playervoicemale01`` / ``npcxotherplayer``.
* Test / dev:   ``testvoicetype``, ``soundeffects_donotrecord``, ``_npc_nolines``.
* Content packs prefix the whole thing: ``sfbgs001_npcfgracekim`` (Shattered
  Space), ``sffl_...``, ``sfter_...`` etc.

The parser is intentionally forgiving: it recovers structure (gender, faction,
category) for the long tail and uses :data:`NAMED_NPCS` for confident, properly
spaced names of major characters.  No Qt dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# ── Categories ──────────────────────────────────────────────────────────────────

CAT_NAMED = "Named character"
CAT_GENERIC = "Generic NPC"
CAT_CROWD = "Crowd (background)"
CAT_ANNOUNCER = "Announcer / system"
CAT_CREATURE = "Creature"
CAT_ROBOT = "Robot"
CAT_PLAYER = "Player"
CAT_EXPRESSIONS = "Non-verbal (expressions)"
CAT_TEST = "Test / non-dialogue"
CAT_UNKNOWN = "Unknown"

_GENDER = {"f": "female", "m": "male", "x": "neutral"}

# Content-pack prefixes that wrap an otherwise normal voice type.
# Value is a friendly source label (only confident ones get a real name).
_CONTENT_PACKS: dict[str, str] = {
    "sfbgs001": "Shattered Space",
    "sfbgs003": "SFBGS003",
    "sfbgs004": "SFBGS004",
    "sfbgs00d": "SFBGS00D",
    "sfbgs022": "SFBGS022",
    "sfbgs050": "SFBGS050",
    "sffl": "SFFL",
    "sfter": "SFTER",
    "sfta00": "SFTA00",
    "sfta": "SFTA",
    "eaw": "EAW",
    "staryard": "Staryard",
}
_CONTENT_PACK_RE = re.compile(
    r"^(sfbgs\d+|sffl|sfter|sfta\d*|eaw|staryard)_", re.IGNORECASE
)

# Faction stem -> friendly faction label (generic voice types).
_FACTION_LABELS: dict[str, str] = {
    "crimsonfleet": "Crimson Fleet",
    "freestarsecurity": "Freestar Collective Security",
    "neonsecurity": "Neon Security",
    "ucsecurity": "UC Security",
    "marine": "UC Marine",
    "merc": "Mercenary",
    "pirate": "Pirate",
    "spacer": "Spacer",
    "ecliptic": "Ecliptic",
    "varuun": "House Va'ruun",
    "varuunzealot": "Va'ruun Zealot",
    "varuunguard": "Va'ruun Guard",
    "varuungeneric": "Va'ruun Citizen",
    "varuungenericchild": "Va'ruun Child",
    "seokguh": "Seokguh",
    "thefirst": "The First",
    "starborn": "Starborn",
    "vortexphantom": "Vortex Phantom",
    "terran": "Terran",
    "terrangeneric": "Terran",
    "redeemed": "Redeemed",
    "human": "Human",
    "generic": "Generic citizen",
    "genericcrowd": "Crowd",
}

# Curated proper names for major characters, keyed by the name part that follows
# the ``npc<gender>`` prefix.  Only confident, properly spaced spellings go here;
# everything else falls back to a title-cased rendering of the raw name part.
NAMED_NPCS: dict[str, str] = {
    # Constellation / companions
    "sarahmorgan": "Sarah Morgan",
    "barrett": "Barrett",
    "andreja": "Andreja",
    "samcoe": "Sam Coe",
    "coracoe": "Cora Coe",
    "walterstroud": "Walter Stroud",
    "vasco": "Vasco",
    # Major quest / faction characters
    "hadrian": "Hadrian",
    "claralyon": "Clara Lyon",
    "deliahstuart": "Delilah Stuart",
    "delilahstuart": "Delilah Stuart",
    "ameliaearhart": "Amelia Earhart",
    "adalovelace": "Ada Lovelace",
    "amanirenas": "Amanirenas",
    "amenirenas": "Amanirenas",
    "demeterduncan": "Demeter Duncan",
    "dianabrackenridge": "Diana Brackenridge",
    "gennadyayton": "Gennady Ayton",
    "owendexler": "Owen Dexler",
    "tomisarkadic": "Tomis Arkadic",
    "yannicklegrande": "Yannick Legrande",
    "alexshadid": "Alex Shadid",
    "frankrenick": "Frank Renick",
    "eliascartwright": "Elias Cartwright",
    "emilycartwright": "Emily Cartwright",
    "karsonendler": "Karson Endler",
    "autumnmacmillan": "Autumn MacMillan",
    "deputymacintyre": "Deputy MacIntyre",
    "jivantabedi": "Jivan Tabedi",
    "yuegarcia": "Yue Garcia",
    "ularuchen": "Ularu Chen",
    "walterstroud2": "Walter Stroud",
    "abigailmorgan": "Abigail Morgan",
    "heller": "Heller",
    "coopercarr": "Cooper Carr",
    "imogene": "Imogene Salzo",
    "imogenesalzo": "Imogene Salzo",
    "marcoschen": "Marcos Chen",
    "ularu": "Ularu Chen",
    "vladimirsall": "Vladimir Sall",
    "noelle": "Noel",
    "matteokhatri": "Matteo Khatri",
    "andromedakepler": "Andromeda Kepler",
    "moaragawa": "Moara Gawa",
    "keller": "Keller",
    "rosie": "Rosie Tannehill",
}


@dataclass(frozen=True)
class SpeakerInfo:
    """Human-friendly description of a dialogue speaker."""

    display_name: str          # best label, e.g. "Sarah Morgan" or "Crimson Fleet — Female 01"
    gender: str                # "female" | "male" | "neutral" | ""
    faction: str               # "" for named/unknown speakers
    category: str              # one of the CAT_* constants
    is_named: bool             # True for a specific named character
    is_cut: bool               # True for cut/unused content
    source: str                # content-pack label, "" for base game
    raw: str                   # the raw voice-type string


def _title_name(name: str) -> str:
    """Best-effort human rendering of a concatenated name part.

    Concatenated names (``sarahmorgan``) can't be reliably word-split, so this
    just capitalizes the first letter — callers always show :attr:`raw` too.
    """
    return name[:1].upper() + name[1:] if name else name


def _describe_generic(stem: str, gender_word: str, suffix: str, base) -> SpeakerInfo:
    """Build a SpeakerInfo for a generic ``<faction><gender><variant>`` type."""
    faction = _FACTION_LABELS.get(stem, _title_name(stem) or "Generic")
    gender_cap = gender_word.capitalize()
    category = CAT_GENERIC
    descriptor = gender_cap

    s = suffix.strip("_")
    if not s:
        descriptor = gender_cap
    elif re.fullmatch(r"\d+", s):
        descriptor = f"{gender_cap} {s}"
    elif s.startswith("accent_"):
        accent = s[len("accent_"):].replace("_", " ").strip()
        descriptor = f"{gender_cap}, {accent.title()} accent"
    elif s.startswith("childfiveyearold"):
        descriptor = f"{gender_cap} child (5 yo)"
    elif s.startswith("child"):
        tail = s[len("child"):]
        descriptor = f"{gender_cap} child" + (f" {tail}" if tail else "")
    elif s == "old":
        descriptor = f"Elderly {gender_word}"
    elif s == "youngadult":
        descriptor = f"Young adult {gender_word}"
    elif s == "rough":
        descriptor = f"{gender_cap}, rough"
    elif s == "eventoned":
        descriptor = f"{gender_cap}, even-toned"
    elif s.startswith("expressions"):
        descriptor = f"{gender_cap}"
        category = CAT_EXPRESSIONS
    else:
        descriptor = f"{gender_cap} ({s})"

    if stem == "crowd" or stem.startswith("genericcrowd"):
        category = CAT_CROWD
        accent = stem[len("genericcrowd"):] if stem.startswith("genericcrowd") else ""
        faction = "Crowd" + (f" ({accent.title()})" if accent else "")

    display = f"{faction} — {descriptor}"
    return SpeakerInfo(
        display_name=display,
        gender=gender_word,
        faction=faction,
        category=category,
        is_named=False,
        is_cut=base["is_cut"],
        source=base["source"],
        raw=base["raw"],
    )


def describe_voice_type(voice_type: str) -> SpeakerInfo:
    """Describe the speaker behind a raw voice-type folder name."""
    raw = voice_type or ""
    s = raw.strip().lower()

    # Strip a content-pack prefix, remembering the source.
    source = ""
    m = _CONTENT_PACK_RE.match(s)
    if m:
        tag = m.group(1)
        source = _CONTENT_PACKS.get(tag, tag.upper())
        s = s[m.end():]

    # Cut / unused content marker.
    is_cut = False
    if s.startswith("_cut_") or s.startswith("cut_"):
        is_cut = True
        s = re.sub(r"^_?cut_", "", s)

    base = {"raw": raw, "source": source, "is_cut": is_cut}

    def info(display, gender="", faction="", category=CAT_UNKNOWN, named=False):
        return SpeakerInfo(
            display_name=display, gender=gender, faction=faction,
            category=category, is_named=named, is_cut=is_cut,
            source=source, raw=raw,
        )

    if not s:
        return info(_title_name(raw) or "(unknown)")

    # ── Non-dialogue / test / dev assets ─────────────────────────────────────
    if (
        s.startswith("test")
        or s.startswith("soundeffects")
        or s.startswith("videoactor")
        or "donotrecord" in s
        or "nolines" in s
        or "novoice" in s
        or "sfxnovoice" in s
    ):
        return info(_title_name(s), category=CAT_TEST)

    # ── Creatures (alien fauna — no real speaker) ────────────────────────────
    if s.startswith("cr_") or re.match(
        r"^cr(ocopedea|octopedea|hexapoda|mantida|mantaa|floatera|hoppera|"
        r"terrormorph|quadrupeda|quadrupedb|bipeda|swimmera|larvaa)",
        s,
    ):
        return info("Creature", category=CAT_CREATURE)

    # ── Announcers / PA / ship systems ───────────────────────────────────────
    if s.startswith("announcer"):
        gm = re.match(r"^announcer([fm])", s)
        gender = _GENDER.get(gm.group(1), "") if gm else ""
        return info("Announcer / system voice", gender=gender, category=CAT_ANNOUNCER)

    # ── Robots ───────────────────────────────────────────────────────────────
    if s.startswith("robot"):
        # robotmodela<name> / robotvasco -> friendly tail when present.
        # ``model[a-z]?`` consumes only the one-letter model designation so a
        # trailing name (e.g. "vasco") survives instead of being eaten.
        tail = re.sub(r"^robot(model[a-z]?|mini[a-z]*|simulated[a-z]*)?", "", s)
        name = NAMED_NPCS.get(tail, _title_name(tail)) if tail and tail != "generic" else ""
        display = f"Robot{f' ({name})' if name else ''}"
        return info(display, category=CAT_ROBOT)

    # ── Player ───────────────────────────────────────────────────────────────
    if s.startswith("playervoice") or s.startswith("npcxotherplayer"):
        gm = re.search(r"(female|male|nonbinary)", s)
        gender = gm.group(1) if gm else ""
        gender = "neutral" if gender == "nonbinary" else gender
        return info("Player character", gender=gender, category=CAT_PLAYER)

    # ── Unity voices (special) ───────────────────────────────────────────────
    if s.startswith("unityvoice"):
        gm = re.search(r"(female|male|nonbinary)", s)
        g = gm.group(1) if gm else ""
        g = "neutral" if g == "nonbinary" else g
        return info("The Unity", gender=g, category=CAT_NAMED, named=True)

    # ── Named NPCs: npc<gender><name> or npc<name> ───────────────────────────
    # Handled before the generic crowd/faction matcher so a name that happens to
    # start with f/m/x (e.g. "npcfrankrenick") isn't mis-split into gender+name.
    nm = re.match(r"^npc(.+)$", s)
    if nm:
        rest = nm.group(1)
        # Prefer a full-name dictionary hit (covers names starting with f/m/x).
        if rest in NAMED_NPCS:
            return info(NAMED_NPCS[rest], category=CAT_NAMED, named=True)
        # Gender-prefixed form: npc + f/m/x + name.
        if rest[:1] in _GENDER and len(rest) > 1:
            gender = _GENDER[rest[:1]]
            name_key = rest[1:]
            display = NAMED_NPCS.get(name_key, _title_name(name_key))
            return info(display, gender=gender, category=CAT_NAMED, named=True)
        return info(NAMED_NPCS.get(rest, _title_name(rest)), category=CAT_NAMED, named=True)

    # ── Generic faction + gender + variant ───────────────────────────────────
    gm = re.match(r"^(.*?)(female|male|nonbinary)(.*)$", s)
    if gm:
        stem, gword, suffix = gm.group(1), gm.group(2), gm.group(3)
        gword = "neutral" if gword == "nonbinary" else gword
        # Strip stray leading scene codes like "lc08_", "ms01_", "r03_".
        stem = re.sub(r"^(?:[a-z]{1,4}\d*_)+", "", stem)
        if stem == "":
            stem = "generic"
        return _describe_generic(stem, gword, suffix, base)

    # ── Fallback ─────────────────────────────────────────────────────────────
    return info(_title_name(s), category=CAT_UNKNOWN)


def describe_voice_types(voice_types: Iterable[str]) -> list[SpeakerInfo]:
    """Describe several voice types, de-duplicating by display name (stable order)."""
    out: list[SpeakerInfo] = []
    seen: set[str] = set()
    for vt in voice_types:
        info = describe_voice_type(vt)
        if info.display_name not in seen:
            seen.add(info.display_name)
            out.append(info)
    return out
