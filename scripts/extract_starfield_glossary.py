"""
Extract game terminology from Starfield translated .txt export files.

Reads Ukrainian translation files and their English originals (where available),
matches strings by hex ID, and produces a glossary JSON compatible with the
app's glossary system (gui/glossary.py format).

Usage:
    python scripts/extract_starfield_glossary.py \
        --uk-dir /path/to/uk/nexus/txt \
        --en-dir /path/to/original/txt \
        --output starfield_glossary.json

    # Or pass individual files:
    python scripts/extract_starfield_glossary.py \
        --uk-files file1.txt file2.txt ... \
        --en-dir /path/to/original/txt \
        --output starfield_glossary.json
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ── Patterns for filtering ─────────────────────────────────────────────────────

# Strings that are clearly editorial labels / templates / developer notes
_SKIP_PATTERNS = re.compile(
    r"^\["                                          # Starts with [ (template/label)
    r"|\[TEMPLATE\b"                                # Template markers
    r"|\bSettlement\s+Dialogue"                     # Settlement dialogue labels
    r"|\bDialogue\b"                                # Generic dialogue category labels
    r"|\bVoicetype"                                 # Voice type labels
    r"|\bShared\s+lines\b"                          # Shared voice lines
    r"|\bAssembly\b.*?\bDialogue",                  # Assembly-Dialogue category
    re.IGNORECASE,
)

# Quest objective lines (action verbs at the start)
_QUEST_VERB_PATTERNS = re.compile(
    r"^(Speak\s+to|Talk\s+to|Go\s+to|Proceed\s+to|Return\s+to|Travel\s+to"
    r"|Retrieve|Find\s+the|Find\s+a|Meet|Follow|Enter|Locate|Access|Use\s+the"
    r"|Wait\s+for|Report\s+to|Investigate|Defeat|Kill|Destroy|Board|Scan"
    r"|Complete|Read\s+the|Open\s+the|Collect|Deliver|Escape|Search|Reach)\b",
    re.IGNORECASE,
)

# Generic game-internal debug / internal names
_INTERNAL_PATTERNS = re.compile(
    r"\bDummy\b|\bDEBUG\b|\bTEST\b|\bPLACEHOLDER\b|^N/A$|__",
    re.IGNORECASE,
)


# ── Category detection ─────────────────────────────────────────────────────────

_FACTION_KEYWORDS = re.compile(
    r"\b(Freestar|UC\b|United\s+Colonies|Crimson\s+Fleet|Ryujin|Ecliptic"
    r"|Spacer|Starborn|Constellation|Va'ruun|Zealot|House\s+Va'ruun"
    r"|Ranger|Vanguard|Neon\s+Security|Xenofresh|HopeTech|Deimos"
    r"|Stroud.Eklund|Taiyo|Ecliptic|SysDef)\b",
    re.IGNORECASE,
)

_LOCATION_KEYWORDS = re.compile(
    r"\b(City|Station|System|Outpost|Colony|Gate|Port|Hub|Yard|Base|Vault"
    r"|District|Plaza|Landing|Spaceport|Staryard|Lab|Facility|Settlement"
    r"|Paradiso|Neon|Atlantis|Akila|Jemison|Volii|Alpha|Cheyenne|Narion"
    r"|Porrima|Serpentis|Olympus|Lunara|Bessel|Toliman|Wolf|Cassiopeia)\b",
    re.IGNORECASE,
)

# Celestial body names: "Name I-a", "Name IV-b", "Zeta Ophiuchi III"
_CELESTIAL_RE = re.compile(
    r"\b[IVXLCDM]{1,5}(-[a-zA-Z])?(\s|$)"   # Roman numeral + optional letter suffix
    r"|\b(Alpha|Beta|Gamma|Delta|Zeta|Epsilon|Eta|Theta|Kappa|Tau|Sigma|Omega)\b",
    re.IGNORECASE,
)

_SHIP_FILE_PATTERN = re.compile(r"blueprint", re.IGNORECASE)

_SKILL_KEYWORDS = re.compile(
    r"\b(Skill|Rank|Tier|Perk|Ability|Proficiency|Mastery|Novice|Advanced"
    r"|Expert|Master|Ballistic|Persuasion|Piloting|Stealth|Medicine"
    r"|Leadership|Commerce|Intimidation|Targeting|Manipulation)\b",
    re.IGNORECASE,
)

_WEAPON_KEYWORDS = re.compile(
    r"\b(Pistol|Rifle|Shotgun|Sniper|Launcher|SMG|LMG|Knife|Blade|Sword"
    r"|Equinox|Rattler|Grendel|Kraken|Beowulf|Coachman|Eon|Magshear"
    r"|Pacifier|Razorback|Lawgiver|Hard\s+Target|Bridger|Regulator"
    r"|Calibrated|Compensated|Refined|Experimental|Modified)\b",
    re.IGNORECASE,
)

_ITEM_KEYWORDS = re.compile(
    r"\b(Digipick|Pack|Suit|Helmet|Boost|Spacesuit|Jetpack|Backpack"
    r"|Grenade|Mine|Aid\b|Med\b|Stim|Injector|Amp|Compound|Extract"
    r"|Module|Component|Resource|Ore|Fiber|Fiber|Adhesive|Iron|Lead"
    r"|Copper|Tungsten|Nickel|Aluminum|Titanium|Beryllium|Dysprosium)\b",
    re.IGNORECASE,
)


def _categorize(en_text: str, filename: str) -> str:
    fn = filename.lower()
    words = en_text.split()
    wc = len(words)

    if _SHIP_FILE_PATTERN.search(fn):
        return "Ship Names"
    if _CELESTIAL_RE.search(en_text) and wc <= 4:
        return "Locations"
    if _FACTION_KEYWORDS.search(en_text) and wc <= 5:
        return "Factions & Organizations"
    if _LOCATION_KEYWORDS.search(en_text) and wc <= 5:
        return "Locations"
    if _SKILL_KEYWORDS.search(en_text) and wc <= 5:
        return "Skills & Perks"
    if _WEAPON_KEYWORDS.search(en_text) and wc <= 5:
        return "Weapons"
    if _ITEM_KEYWORDS.search(en_text) and wc <= 5:
        return "Items & Equipment"
    # Proper noun heuristic: title-case names, at most 3 words
    significant = [w.strip("\"',.") for w in words if len(w) > 2 and not w[0].isdigit()]
    all_title = significant and all(w[0].isupper() for w in significant if w)
    if all_title and wc <= 3:
        return "Proper Nouns"
    return "Game Terms"


# ── File parsing ───────────────────────────────────────────────────────────────

_LINE_RE_FULL = re.compile(r'^\d+ (0x[0-9A-Fa-f]+) "(.*?)" "(.*?)"$')


def parse_txt_file(path: Path) -> Dict[str, Tuple[str, str]]:
    """
    Parse an exported .txt file.
    Returns dict: hex_id_upper -> (original, translated).
    Lines with empty original are skipped.
    """
    entries: Dict[str, Tuple[str, str]] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning(f"Cannot read {path}: {e}")
        return entries

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE_FULL.match(line)
        if m:
            hex_id, original, translated = m.groups()
            if original:
                entries[hex_id.upper()] = (original, translated)
    return entries


# ── Term filtering ─────────────────────────────────────────────────────────────

# Internal code patterns
_CODE_PATTERNS = re.compile(
    r"^[a-z]+_[a-z]+_[a-z0-9]"          # voice type codes: female_as_yo1
    r"|^[A-Z]{2,}_[A-Z0-9_]+"            # ALL_CAPS_CODES
    r"|[A-Z]{2,}\d{3,}"                  # LC122SpaceCellLocation style codes
    r"|\bPEO\b"                           # PEO internal prefix
    r"|\bSWF\b|\bSFTA\b|\bSFBGS\b"       # plugin-internal prefixes
    r"|^\$",                              # $Use, $Action internal strings
    re.IGNORECASE,
)

# Sentence-like strings (not terms)
_SENTENCE_PATTERNS = re.compile(
    r"[.!?]\s*$"                          # ends with sentence punctuation
    r"|\bwe\b|\byou\b|\byour\b|\bit's\b|\bwe're\b"  # personal pronouns
    r"|\boh\b\s|\buh\b\s|\bhmm\b"         # interjections
    r"|\bthe\s+ship\b|\bthe\s+crew\b",    # quest-action phrases
    re.IGNORECASE,
)

# Gameplay stat / template strings
_STAT_PATTERNS = re.compile(
    r"<[a-z_]+>"                          # <mag>, <dur>, <repetitions> placeholders
    r"|\bDamage\b.*%|\b%.*Damage\b"       # % damage formulas
    r"|\bSpeed\s+Upgrade\b|\bChance\b|\bRate\b.*Upgrade",
    re.IGNORECASE,
)

# Extra noise patterns (NO IGNORECASE — the CamelCase check must be case-sensitive)
_NOISE_PATTERNS = re.compile(
    r"\([A-Za-z]{4,}\)"                   # (InternalQualifier) parentheticals
    r"|\bRe:\s"                            # email subject Re:
    r"|\b[A-Z][a-z]+[A-Z][a-z]+"         # CamelCase internal IDs: RangedAttack
    r"|\b\d+[A-Z][a-z]"                   # mixed alphanumeric: 1Way, 2x2
    r"|\s+and\s+[A-Z][a-z]",              # "X and Y" list items
)

# Quest action verbs not caught by _QUEST_VERB_PATTERNS (continuation)
_QUEST_CONTINUATION = re.compile(
    r"^(Approach|Clear|Buy|Confront|Defeat|Unlock|Fix|Repair|Upgrade|Check"
    r"|Board|Dock|Launch|Deploy|Activate|Disable|Hack|Override|Steal|Take"
    r"|Place|Install|Remove|Pick\s+up|Drop|Give|Show|Ask|Tell|Warn|Convince"
    r"|Win|Lose|Survive|Escape|Flee|Fight|Attack|Defend)\b",
    re.IGNORECASE,
)


def _is_useful_term(en_text: str, uk_text: str) -> bool:
    """Return True if this EN→UK pair is a useful glossary term."""
    if not en_text or not uk_text:
        return False
    if en_text == uk_text:
        return False
    if "\n" in en_text or "\r" in en_text:
        return False
    if "\n" in uk_text or "\r" in uk_text:
        return False

    en_stripped = en_text.strip()

    # Multi-line strings: catch both actual and escaped newlines
    if "\n" in en_stripped or "\r" in en_stripped or "\\n" in en_stripped or "\\r" in en_stripped:
        return False

    en_words = en_stripped.split()

    if len(en_words) > 6:
        return False
    if len(en_stripped) > 55:
        return False
    if len(en_stripped) < 3:
        return False

    # Parenthetical prefix like "(Optional)..."
    if en_stripped.startswith("("):
        return False

    # Internal category labels: "Storage - Gas - Medium", "Reactor - Advanced"
    if en_stripped.count(" - ") >= 2:
        return False

    if _SKIP_PATTERNS.search(en_stripped):
        return False
    if _QUEST_VERB_PATTERNS.search(en_stripped):
        return False
    if _INTERNAL_PATTERNS.search(en_stripped):
        return False
    if _CODE_PATTERNS.search(en_stripped):
        return False
    if _SENTENCE_PATTERNS.search(en_stripped):
        return False
    if _STAT_PATTERNS.search(en_stripped):
        return False
    if _QUEST_CONTINUATION.search(en_stripped):
        return False
    if _NOISE_PATTERNS.search(en_stripped):
        return False

    # Single-word terms: require title-case + min 4 chars (catches "Neon", "Wolf", etc.)
    if len(en_words) == 1:
        w = en_words[0].strip("\"'-.,")
        if not w or not w[0].isupper():
            return False
        if len(w) < 4:
            return False

    return True


def _is_useful_ship_name(name: str) -> bool:
    """Ship names can be English-only (untranslated) — include them."""
    if not name or "\n" in name or "\r" in name:
        return False
    if len(name) > 50 or len(name.strip()) < 2:
        return False
    if _INTERNAL_PATTERNS.search(name):
        return False
    # Exclude generic "Ship" etc.
    if name.strip().lower() in ("ship", "корабель", "vessel"):
        return False
    return True


# ── Plugin name matching ───────────────────────────────────────────────────────

def _plugin_key(filename: str) -> str:
    """Normalise a filename to a plugin+extension key for matching EN↔UK files."""
    name = Path(filename).name.lower()
    # Strip language suffix: _uk_translated, _en_translated
    name = re.sub(r"_(uk|en)_translated", "", name)
    return name


def build_en_index(en_dir: Optional[Path]) -> Dict[str, Dict[str, Tuple[str, str]]]:
    """
    Returns: plugin_key -> {hex_id: (en_original, "")}
    """
    index: Dict[str, Dict[str, Tuple[str, str]]] = {}
    if en_dir is None or not en_dir.exists():
        return index
    for f in sorted(en_dir.glob("*.txt")):
        key = _plugin_key(f.name)
        if key not in index:
            index[key] = parse_txt_file(f)
        else:
            index[key].update(parse_txt_file(f))
    return index


# ── Glossary entry dataclass ───────────────────────────────────────────────────

@dataclass
class GlossaryEntry:
    source_term: str
    target_term: str
    category: str = ""
    definition: str = ""
    examples: List[str] = field(default_factory=list)
    notes: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


# ── Main extraction ────────────────────────────────────────────────────────────

def extract_glossary(
    uk_files: List[Path],
    en_dir: Optional[Path],
    max_entries: int = 5000,
) -> List[GlossaryEntry]:
    en_index = build_en_index(en_dir)
    logger.info(f"Loaded EN index: {len(en_index)} plugin keys")

    entries: List[GlossaryEntry] = []
    seen: Dict[str, str] = {}  # en_lower -> target_term (dedup)
    stats = {"matched": 0, "uk_only": 0, "ships": 0, "skipped": 0}

    for uk_path in uk_files:
        uk_data = parse_txt_file(uk_path)
        if not uk_data:
            continue

        plugin_key = _plugin_key(uk_path.name)
        en_data = en_index.get(plugin_key, {})
        is_blueprint = _SHIP_FILE_PATTERN.search(plugin_key) is not None
        filename = uk_path.name

        for hex_id, (uk_orig, uk_trans) in uk_data.items():
            # Prefer user correction if available
            uk_text = uk_trans if uk_trans.strip() else uk_orig

            if hex_id in en_data:
                en_text = en_data[hex_id][0]
                stats["matched"] += 1

                if is_blueprint:
                    # Ship names: keep both the EN name itself and EN→UK pairs
                    if en_text.strip() and en_text != uk_text and _is_useful_ship_name(en_text):
                        en_key = en_text.lower().strip()
                        if en_key not in seen:
                            seen[en_key] = uk_text
                            entries.append(GlossaryEntry(
                                source_term=en_text.strip(),
                                target_term=uk_text.strip(),
                                category="Ship Names",
                                notes=f"src:{filename}",
                            ))
                            stats["ships"] += 1
                    continue

                if not _is_useful_term(en_text, uk_text):
                    stats["skipped"] += 1
                    continue

                en_key = en_text.lower().strip()
                if en_key in seen:
                    continue
                seen[en_key] = uk_text

                category = _categorize(en_text, filename)
                entries.append(GlossaryEntry(
                    source_term=en_text.strip(),
                    target_term=uk_text.strip(),
                    category=category,
                    notes=f"src:{filename}",
                ))
                if len(entries) >= max_entries:
                    logger.warning(f"Reached max_entries={max_entries}, stopping early")
                    return entries

            else:
                # No EN match — extract Ukrainian strings as source terms
                # Useful for: ship names in English within UK files, callsigns, etc.
                stats["uk_only"] += 1

                if is_blueprint and _is_useful_ship_name(uk_text):
                    # Keep English ship names that appear as-is in UK files
                    if re.search(r"[a-zA-Z]{3,}", uk_text):
                        en_key = uk_text.lower().strip()
                        if en_key not in seen:
                            seen[en_key] = uk_text
                            entries.append(GlossaryEntry(
                                source_term=uk_text.strip(),
                                target_term=uk_text.strip(),
                                category="Ship Names",
                                notes=f"src:{filename} (uk-only)",
                            ))
                            stats["ships"] += 1


    logger.info(
        f"Stats: matched={stats['matched']}, uk_only={stats['uk_only']}, "
        f"ships={stats['ships']}, skipped={stats['skipped']}"
    )
    logger.info(f"Total glossary entries: {len(entries)}")
    return entries


# ── Output ─────────────────────────────────────────────────────────────────────

def write_glossary_json(entries: List[GlossaryEntry], output: Path) -> None:
    data = {"entries": [asdict(e) for e in entries]}
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Written {len(entries)} entries to {output}")

    # Print category summary
    from collections import Counter
    cats = Counter(e.category for e in entries)
    for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {count:5d}  {cat}")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--uk-dir", type=Path, help="Directory containing UK .txt files")
    group.add_argument("--uk-files", nargs="+", type=Path, metavar="FILE", help="Individual UK .txt files")
    parser.add_argument("--en-dir", type=Path, default=None, help="Directory containing EN original .txt files")
    parser.add_argument("--output", type=Path, default=Path("starfield_glossary.json"), help="Output JSON file")
    parser.add_argument("--max", type=int, default=5000, help="Maximum glossary entries")
    args = parser.parse_args()

    if args.uk_dir:
        uk_files = sorted(args.uk_dir.glob("*.txt"))
    else:
        uk_files = args.uk_files

    if not uk_files:
        logger.error("No UK files found")
        sys.exit(1)

    logger.info(f"Processing {len(uk_files)} UK files")
    entries = extract_glossary(uk_files, args.en_dir, max_entries=args.max)

    if not entries:
        logger.error("No glossary entries extracted")
        sys.exit(1)

    write_glossary_json(entries, args.output)


if __name__ == "__main__":
    main()
