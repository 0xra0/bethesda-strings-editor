#!/usr/bin/env python3
"""
Create a quality-checker training dataset for Gemma fine-tuning.

Scans English source files paired with Ukrainian translated files,
runs QualityChecker on each string pair, and outputs ShareGPT JSONL
with structured quality assessment examples.

Synthetic bad examples are injected for every issue type to guarantee
full coverage of QC codes even when the real data has few examples of
a particular failure mode.

Usage:
    python scripts/create_qc_dataset.py [options]

Defaults use the same paths as extract_sharegpt_dataset.py.
"""

import argparse
import json
import logging
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from bethesda_strings.core import BethesdaStringFile
from gui.quality_checker import (
    RETRANSLATE_CODES,
    QualityChecker,
    QualityReport,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── System prompt ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a quality checker for Bethesda Starfield Ukrainian game localization. "
    "Given a source string and its Ukrainian translation, detect quality issues.\n"
    "\n"
    "Issue codes to detect:\n"
    "  MISSING_TAG        — game format tag present in source but absent in translation\n"
    "  EXTRA_TAG          — tag in translation not present in source\n"
    "  SOURCE_LANGUAGE_LEAK — Russian characters (ы/э/ё/ъ) or Russian words in Ukrainian output\n"
    "  ENGLISH_LEAK       — untranslated English words remaining in Ukrainian output\n"
    "  UNTRANSLATED       — translation is identical to the source\n"
    "  EMPTY_TRANSLATION  — translation is empty or whitespace-only\n"
    "  SUSPICIOUSLY_SHORT — translation is <20% the length of the source\n"
    "  SUSPICIOUSLY_LONG  — translation is >500% the length of the source\n"
    "  AI_ARTIFACT        — AI commentary prefix (Note:, Translation:, Переклад:, etc.)\n"
    "  REPETITIVE_CONTENT — same phrase repeated 3+ times (hallucination)\n"
    "  MISSING_NEWLINES   — source has \\n but translation has none\n"
    "  NEWLINE_COUNT_MISMATCH — newline count differs between source and translation\n"
    "  MISSING_NUMBER     — standalone number in source absent from translation\n"
    "  LOW_UKRAINIAN_COVERAGE — too few recognized Ukrainian words (<25%)\n"
    "  CASE_MISMATCH      — source starts uppercase, translation starts lowercase\n"
    "  TRANSLATION_TRUNCATED — translation is a cut-off prefix of the source\n"
    "\n"
    "Output for clean translation:\n"
    "VERDICT: GOOD\n"
    "\n"
    "Output for problematic translation:\n"
    "VERDICT: ISSUES_FOUND\n"
    "CODES: CODE1, CODE2\n"
    "SEVERITY: error|warning|info\n"
    "DETAILS:\n"
    "- [CODE] description\n"
    "ACTION: AUTOFIX|RETRANSLATE"
)

# Strings too short to be meaningful training examples
_MIN_SOURCE = 10
_MIN_TRANS = 5

# Skip pure technical IDs, template labels, very short strings
_SKIP_RE = re.compile(
    r"^[\W\d.]*$"
    r"|^<[\w.]+(?:=[\w.]+)?/?>$"
    r"|^[A-Za-z\d]{3,}_[A-Za-z\d_]+$"
    r"|^.{1,3}$"
    r"|^\[.*\]$"
    r"|^{.*}$",
    re.UNICODE,
)

# Game tag extractor (subset of quality_checker's full set, sufficient for injection)
_TAG_RE = re.compile(
    r"<Alias(?:[.=][^>]*)?>|<Token(?:[.=][^>]*)?>|<Global(?:=[^>]*)?>|"
    r"<[biuBIU]>|<br\s*/?>|</[A-Za-z]+>|"
    r"\[[A-Z][A-Za-z0-9_/]*\]|"
    r"%[sdfoxXceEgGpn%]|\{[^}]+\}|\\n|\\t",
    re.IGNORECASE,
)

# ── Response builder ───────────────────────────────────────────────────────────

def _severity_rank(sev: str) -> int:
    return {"error": 2, "warning": 1, "info": 0}.get(sev, 0)


def _action_for_codes(codes: set) -> str:
    if codes & RETRANSLATE_CODES:
        return "RETRANSLATE"
    return "AUTOFIX"


def _build_response(report: QualityReport) -> str:
    if not report.has_issues:
        return "VERDICT: GOOD"

    codes = sorted({i.code for i in report.issues})
    top_sev = max(report.issues, key=lambda i: _severity_rank(i.severity)).severity
    action = _action_for_codes(set(codes))

    lines = [
        "VERDICT: ISSUES_FOUND",
        f"CODES: {', '.join(codes)}",
        f"SEVERITY: {top_sev}",
        "DETAILS:",
    ]
    for issue in report.issues:
        detail = f" ({issue.detail})" if issue.detail else ""
        lines.append(f"- [{issue.code}] {issue.message}{detail}")
    lines.append(f"ACTION: {action}")
    return "\n".join(lines)


def _example(source: str, translation: str, response: str, src_lang: str = "English") -> dict:
    human = (
        f"Check this Ukrainian translation:\n\n"
        f"Source ({src_lang}):\n{source}\n\n"
        f"Translation (Ukrainian):\n{translation}"
    )
    return {
        "conversations": [
            {"from": "system", "value": SYSTEM_PROMPT},
            {"from": "human",  "value": human},
            {"from": "gpt",    "value": response},
        ]
    }


# ── Synthetic issue injectors ──────────────────────────────────────────────────
# All injectors share (source, translation) -> Optional[str] for a uniform INJECTORS table.
# Parameters unused by a specific injector are named with a leading _ (intentional non-use).

def _inject_russian_leak(_source: str, translation: str) -> Optional[str]:
    """Substitute Ukrainian-specific chars for Russian equivalents to simulate RU leakage."""
    mapping = str.maketrans("іїєІЇЄ", "иийИИЕ")
    result = translation.translate(mapping)
    # Force threshold by inserting explicit Russian-only chars
    words = result.split()
    if len(words) >= 3:
        mid = len(words) // 2
        w = words[mid]
        if len(w) >= 3 and any("а" <= c <= "я" for c in w.lower()):
            words[mid] = w[: len(w) // 2] + "ы" + w[len(w) // 2 + 1 :]
        result = " ".join(words)
    return result if result != translation else None


def _inject_missing_tag(source: str, translation: str) -> Optional[str]:
    """Remove one tag occurrence from translation that exists in source."""
    src_tags = _TAG_RE.findall(source)
    for tag in src_tags:
        if tag in translation:
            return translation.replace(tag, "", 1)
    return None


def _inject_extra_tag(source: str, translation: str) -> Optional[str]:
    """Add a tag to translation that is not in source."""
    src_tags = set(_TAG_RE.findall(source))
    candidates = ["<b>", "%s", "\\n", "[PLYR]"]
    for tag in candidates:
        if tag not in src_tags and tag not in translation:
            mid = len(translation) // 2
            return translation[:mid] + tag + translation[mid:]
    return None


def _inject_ai_artifact(_source: str, translation: str) -> str:
    prefixes = [
        "Переклад: ", "Примітка: ", "Translation: ",
        "Ukrainian: ", "Ось переклад: ", "Note: ", "Here's the translation: ",
    ]
    return random.choice(prefixes) + translation


def _inject_repetition(_source: str, translation: str) -> str:
    words = translation.split()
    if len(words) < 4:
        return (translation + " ") * 3
    tail = " ".join(words[-4:])
    return translation + " " + " ".join([tail] * 3)


def _inject_empty(_source: str, _translation: str) -> str:
    return ""


def _inject_untranslated(source: str, _translation: str) -> str:
    return source


def _inject_truncated(_source: str, translation: str) -> Optional[str]:
    words = translation.split()
    cut = max(1, len(words) * 3 // 10)
    if cut >= len(words):
        return None
    return " ".join(words[:cut])


def _inject_suspiciously_short(_source: str, translation: str) -> Optional[str]:
    words = translation.split()
    if len(words) < 4:
        return None
    return " ".join(words[:1]) + "."


def _inject_case_mismatch(_source: str, translation: str) -> Optional[str]:
    stripped = translation.lstrip()
    if stripped and stripped[0].isupper():
        return translation[: len(translation) - len(stripped)] + stripped[0].lower() + stripped[1:]
    return None


def _inject_missing_newlines(source: str, translation: str) -> Optional[str]:
    if "\\n" not in source and "\n" not in source:
        return None
    return translation.replace("\\n", " ").replace("\n", " ")


INJECTORS = [
    ("russian_leak",       _inject_russian_leak),
    ("missing_tag",        _inject_missing_tag),
    ("extra_tag",          _inject_extra_tag),
    ("ai_artifact",        _inject_ai_artifact),
    ("repetition",         _inject_repetition),
    ("empty",              _inject_empty),
    ("untranslated",       _inject_untranslated),
    ("truncated",          _inject_truncated),
    ("suspiciously_short", _inject_suspiciously_short),
    ("case_mismatch",      _inject_case_mismatch),
    ("missing_newlines",   _inject_missing_newlines),
]


# ── File loading ───────────────────────────────────────────────────────────────

def _find_uk_file(en_path: Path, uk_dir: Path) -> Optional[Path]:
    base = re.sub(r"_en$", "_uk", en_path.stem, flags=re.IGNORECASE)
    ext = en_path.suffix.lower()
    for candidate in uk_dir.iterdir():
        if candidate.stem.lower() == base.lower() and candidate.suffix.lower() == ext:
            return candidate
    return None


def _load_all_pairs(en_dir: Path, uk_dir: Path) -> list[tuple[str, str]]:
    """Return deduplicated (source, translation) pairs passing basic filters."""
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for en_path in sorted(en_dir.glob("*_en.*")):
        uk_path = _find_uk_file(en_path, uk_dir)
        if uk_path is None:
            logger.debug("No UK match for %s", en_path.name)
            continue
        ext = en_path.suffix.lstrip(".").lower()
        try:
            en_file = BethesdaStringFile(str(en_path), file_extension=ext)
            uk_file = BethesdaStringFile(str(uk_path), file_extension=ext)
        except Exception as exc:
            logger.warning("Parse error %s: %s", en_path.name, exc)
            continue

        uk_map = {s.id: s.get_string() for s in uk_file.strings}
        matched = 0
        for s in en_file.strings:
            en_text = (s.get_string() or "").strip()
            uk_text = (uk_map.get(s.id, "") or "").strip()
            if (
                not en_text
                or not uk_text
                or len(en_text) < _MIN_SOURCE
                or len(uk_text) < _MIN_TRANS
                or _SKIP_RE.fullmatch(en_text)
            ):
                continue
            key = (en_text, uk_text)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(key)
            matched += 1
        logger.info("  %s → %s: %d pairs", en_path.name, uk_path.name, matched)

    logger.info("Total unique pairs loaded: %d", len(pairs))
    return pairs


# ── Main ───────────────────────────────────────────────────────────────────────

def main(args: argparse.Namespace) -> None:
    random.seed(args.seed)

    en_dir = Path(args.en_dir)
    uk_dir = Path(args.uk_dir)
    out_path = Path(args.output)

    checker = QualityChecker(
        target_encoding="utf-8",
        target_language="Ukrainian",
        source_language="English",
    )

    all_pairs = _load_all_pairs(en_dir, uk_dir)
    random.shuffle(all_pairs)

    # ── Pass 1: classify real pairs ────────────────────────────────────────────
    good_pool: list[dict] = []          # clean translations
    issue_pool: defaultdict[str, list[dict]] = defaultdict(list)
    clean_pairs: list[tuple[str, str]] = []  # pairs that passed QC (for synthetic)

    for source, translation in all_pairs:
        report = checker.check(0, 0, source, translation)
        ex = _example(source, translation, _build_response(report))
        if not report.has_issues:
            good_pool.append(ex)
            clean_pairs.append((source, translation))
        else:
            for issue in report.issues:
                issue_pool[issue.code].append(ex)

    logger.info(
        "Pass 1 — good: %d | issues by code: %s",
        len(good_pool),
        dict(sorted(Counter({k: len(v) for k, v in issue_pool.items()}).items())),
    )

    # ── Pass 2: synthetic injection ────────────────────────────────────────────
    synth_pool: defaultdict[str, list[dict]] = defaultdict(list)
    random.shuffle(clean_pairs)
    n_synth = min(len(clean_pairs), args.max_synth_per_injector)

    for _, inject_fn in INJECTORS:
        for source, translation in clean_pairs[:n_synth]:
            try:
                injected = inject_fn(source, translation)
            except Exception:
                continue
            if injected is None or injected == translation:
                continue
            report = checker.check(0, 0, source, injected)
            if not report.has_issues:
                continue  # injection didn't trigger a detectable issue
            ex = _example(source, injected, _build_response(report))
            for issue in report.issues:
                synth_pool[issue.code].append(ex)

    logger.info(
        "Pass 2 — synthetic by code: %s",
        dict(sorted(Counter({k: len(v) for k, v in synth_pool.items()}).items())),
    )

    # ── Assemble final dataset ─────────────────────────────────────────────────
    random.shuffle(good_pool)
    final: list[dict] = list(good_pool[: args.max_good])

    all_codes = sorted(set(issue_pool) | set(synth_pool))
    for code in all_codes:
        pool = issue_pool.get(code, []) + synth_pool.get(code, [])
        random.shuffle(pool)
        final.extend(pool[: args.max_issues_per_code])

    random.shuffle(final)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for item in final:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    # ── Final stats ────────────────────────────────────────────────────────────
    verdict_counts: Counter = Counter()
    code_counts: Counter = Counter()
    for item in final:
        response = item["conversations"][2]["value"]
        if response == "VERDICT: GOOD":
            verdict_counts["GOOD"] += 1
        else:
            verdict_counts["ISSUES_FOUND"] += 1
            for line in response.splitlines():
                if line.startswith("CODES:"):
                    for code in line.split(":", 1)[1].split(","):
                        code_counts[code.strip()] += 1

    print(f"\n{'='*50}")
    print(f"Dataset: {len(final)} total examples → {out_path}")
    print(f"{'='*50}")
    print(f"  GOOD:         {verdict_counts['GOOD']:5d}")
    print(f"  ISSUES_FOUND: {verdict_counts['ISSUES_FOUND']:5d}")
    if code_counts:
        print("\nIssue code breakdown:")
        for code, count in sorted(code_counts.items(), key=lambda x: -x[1]):
            print(f"  {code:35s} {count:5d}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate QC training dataset for Gemma fine-tuning"
    )
    parser.add_argument(
        "--en-dir",
        default="/home/home/Downloads/Starfield/Translate/Files/original/strings",
        help="Directory with English source .strings/.dlstrings/.ilstrings files",
    )
    parser.add_argument(
        "--uk-dir",
        default="/home/home/Downloads/Starfield/Translate/Files/uk/nexus/Data/strings",
        help="Directory with Ukrainian translated files (matching _uk suffix)",
    )
    parser.add_argument(
        "--output",
        default="scripts/qc_dataset_sharegpt.jsonl",
        help="Output JSONL file path",
    )
    parser.add_argument(
        "--max-good",
        type=int,
        default=5000,
        help="Maximum clean (GOOD verdict) examples to include",
    )
    parser.add_argument(
        "--max-issues-per-code",
        type=int,
        default=1000,
        help="Maximum examples per issue code (real + synthetic combined)",
    )
    parser.add_argument(
        "--max-synth-per-injector",
        type=int,
        default=2000,
        help="Clean pairs to attempt per synthetic injector",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    args = parser.parse_args()

    main(args)
