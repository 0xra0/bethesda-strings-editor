"""
Extract English→Ukrainian Starfield string pairs and write them as ShareGPT JSONL
for fine-tuning TranslateGemma 12B.

Usage:
    python scripts/extract_sharegpt_dataset.py [output.jsonl]

Output defaults to: scripts/starfield_en_uk_sharegpt.jsonl
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from bethesda_strings.core import BethesdaStringFile

EN_DIR = Path("/home/home/Downloads/Starfield/Translate/Files/original/strings")
UK_DIR = Path("/home/home/Downloads/Starfield/Translate/Files/uk/nexus/Data/strings")

SYSTEM_PROMPT = (
    "You are a professional Ukrainian game localization translator for Starfield. "
    "Translate the given English text to Ukrainian. "
    "Preserve these tags exactly as-is (do not translate them): "
    "<Alias=...> and variants like <Alias.Name=...> <Alias.PluralName=...> <Alias.ShortName=...> "
    "<Alias.CurrentName=...>, "
    "<Token.ValueInt=...> <Token.Value=...> <Global=...>, "
    "%s {0} {1} {2}, "
    "<b> </b> <i> </i> <font ...> </font>, "
    "<mag> <dur> <repetitions>, "
    "[M] [F] [N] (gender/number agreement markers). "
    "Output only the translation, nothing else."
)

# Regex for strings that don't need translation (pure tags/numbers/punctuation/paths)
_NOTRANS_RE = re.compile(
    r"^[\W\d.]*$"
    r"|^<[\w.]+(?:=[\w.]+)?/?>$"
    r"|^[A-Za-z\d]{3,}_[A-Za-z\d_]+$"
    r"|^\w+[A-Z]+[_a-z\d]+[A-Z]+\w+$"
    r"|^.{1,2}$"
    r"|^<[^>]+$"
    r"|^\[.*\]$"           # pure bracketed labels like [TEMPLATE - ...]
    r"|^{.*}$"             # pure curly-brace tokens
    r"|^\[tk_[^\]]*\]$",   # bare xTranslator tokens
    re.UNICODE,
)

# Per-category patterns for tag-leak detection.
# Each preserved-tag category must have the same count in EN and UK.
_TAG_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'<Alias(?:\.[A-Za-z]+)?=[^>]*>', re.IGNORECASE), 'alias'),
    (re.compile(r'<font[^>]*>',                   re.IGNORECASE), 'font_open'),
    (re.compile(r'</font>',                        re.IGNORECASE), 'font_close'),
    (re.compile(r'<Token\.[A-Za-z]+=\w+>',        re.IGNORECASE), 'token'),
    (re.compile(r'<Global=[^>]+>',                re.IGNORECASE), 'global'),
    (re.compile(r'\{[012]\}|%s'),                               'placeholder'),
    (re.compile(r'\[M\]',  re.IGNORECASE),                      'gender_m'),
    (re.compile(r'\[F\]',  re.IGNORECASE),                      'gender_f'),
    (re.compile(r'\[N\]',  re.IGNORECASE),                      'gender_n'),
    (re.compile(r'\[tk_[^\]]*\]', re.IGNORECASE),               'tk'),
]


def _tag_counts(text: str) -> dict[str, int]:
    return {key: len(pat.findall(text)) for pat, key in _TAG_PATTERNS
            if pat.search(text)}


def _has_tag_mismatch(en: str, uk: str) -> bool:
    """Return True if any preserved-tag category count differs between EN and UK."""
    return _tag_counts(en) != _tag_counts(uk)



def _find_uk_file(en_path: Path) -> Path | None:
    """Map e.g. 'starfield_en.strings' → 'Starfield_uk.STRINGS' (case-insensitive)."""
    base = en_path.stem.replace("_en", "_uk")          # starfield_en → starfield_uk
    ext  = en_path.suffix                               # .strings / .dlstrings / .ilstrings
    for candidate in UK_DIR.iterdir():
        if candidate.stem.lower() == base and candidate.suffix.lower() == ext.lower():
            return candidate
    return None


def main(out_path: Path) -> None:
    pairs: list[dict] = []
    seen: set[tuple[str, str]] = set()

    en_files = sorted(EN_DIR.glob("*_en.*"))
    print(f"Found {len(en_files)} English source files")

    for en_path in en_files:
        uk_path = _find_uk_file(en_path)
        if uk_path is None:
            print(f"  SKIP (no UK match): {en_path.name}")
            continue

        try:
            ext = en_path.suffix.lstrip(".").lower()
            en_file = BethesdaStringFile(str(en_path), file_extension=ext)
            uk_file = BethesdaStringFile(str(uk_path), file_extension=ext)
        except Exception as e:
            print(f"  ERROR parsing {en_path.name}: {e}")
            continue

        # Build ID → text map for UK
        uk_map = {s.id: s.get_string() for s in uk_file.strings}

        matched = skipped = tag_bad = 0
        for s in en_file.strings:
            en_text = s.get_string()
            uk_text = uk_map.get(s.id, "")

            en_s = en_text.strip()
            uk_s = uk_text.strip()

            if not en_s or not uk_s or en_s == uk_s or _NOTRANS_RE.fullmatch(en_s) \
                    or len(en_s) < 3 or len(uk_s) < 3:
                skipped += 1
                continue

            if _has_tag_mismatch(en_s, uk_s):
                tag_bad += 1
                skipped += 1
                continue

            key = (en_s, uk_s)
            if key in seen:              # deduplicate identical pairs
                skipped += 1
                continue
            seen.add(key)

            pairs.append({
                "conversations": [
                    {"from": "system", "value": SYSTEM_PROMPT},
                    {"from": "human",  "value": f"Translate to Ukrainian:\n{en_s}"},
                    {"from": "gpt",    "value": uk_s},
                ]
            })
            matched += 1

        tag_info = f", {tag_bad} tag-mismatch" if tag_bad else ""
        print(f"  {en_path.name} + {uk_path.name}: {matched} pairs ({skipped} skipped{tag_info})")

    print(f"\nTotal pairs: {len(pairs)}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for item in pairs:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"Written to: {out_path}")


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "starfield_en_uk_sharegpt.jsonl"
    main(out)
