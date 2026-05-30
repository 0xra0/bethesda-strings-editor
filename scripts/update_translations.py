"""
Batch-translate all unfinished strings in the Qt .ts files using Claude Haiku.

Usage:
    python scripts/update_translations.py [--dry-run]

Requires ANTHROPIC_API_KEY in environment or app's SecretStore.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Config ─────────────────────────────────────────────────────────────────────

TS_DIR = Path(__file__).parent.parent / "gui" / "translations"

LANGUAGES: dict[str, str] = {
    "de_DE": "German",
    "fr_FR": "French",
    "es_ES": "Spanish",
    "pl_PL": "Polish",
    "cs_CZ": "Czech",
    "uk_UA": "Ukrainian",
}

BATCH_SIZE = 60          # strings per API call
MODEL      = "claude-haiku-4-5"
MAX_TOKENS = 4096

# ── Anthropic client ────────────────────────────────────────────────────────────

def get_api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key
    try:
        from gui.secret_store import get_store
        stored = get_store().get("anthropic-api-key")
        if stored:
            return stored
    except Exception:
        pass
    sys.exit("ERROR: ANTHROPIC_API_KEY not set and not found in SecretStore.")


def translate_batch(client, sources: list[str], lang_name: str) -> list[str]:
    """Translate a list of source strings into lang_name. Returns same-length list."""
    numbered = "\n".join(f"{i+1}. {s}" for i, s in enumerate(sources))

    system = (
        f"You are a professional software UI translator. "
        f"Translate the following numbered English UI strings into {lang_name}. "
        f"Rules:\n"
        f"- Output ONLY the numbered translations, one per line, e.g. '1. translation'\n"
        f"- Keep the same number, the period, and a space before the translation\n"
        f"- Do NOT translate technical placeholders: {{count}}, {{n}}, %s, %d, {{0}}, {{1}}, etc.\n"
        f"- Preserve keyboard shortcut markers (& before a letter) verbatim\n"
        f"- Preserve ellipsis (…) and punctuation style\n"
        f"- Keep file extensions (.strings, .dlstrings, .esp, .esm, .ba2, .xml, .csv) as-is\n"
        f"- Keep brand names (NexusMods, Ollama, Claude, Starfield, Bethesda, xTranslator, "
        f"Weblate, TMX, SST, JSONL, LoRA, etc.) untranslated\n"
        f"- Do NOT add explanations or notes\n"
        f"- If a string should not be translated (it is a format code, file path, or "
        f"identifier), output it unchanged"
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": numbered}],
    )

    raw = response.content[0].text.strip()
    lines = raw.split("\n")

    results: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Extract "N. translation"
        if ". " in line:
            _, _, translation = line.partition(". ")
            results.append(translation.strip())
        else:
            results.append(line)

    # Pad/trim to match input length
    if len(results) < len(sources):
        results.extend(sources[len(results):])   # fallback: keep source
    return results[:len(sources)]


# ── .ts file handling ──────────────────────────────────────────────────────────

def load_ts(path: Path) -> ET.ElementTree:
    ET.register_namespace("", "")
    parser = ET.XMLParser(encoding="utf-8")
    return ET.parse(str(path), parser=parser)


def save_ts(tree: ET.ElementTree, path: Path) -> None:
    # Write with XML declaration and proper indentation
    root = tree.getroot()
    _indent(root)
    tree.write(str(path), encoding="unicode", xml_declaration=True)
    # Fix the declaration line to match Qt's format
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "<?xml version='1.0' encoding='us-ascii'?>",
        '<?xml version="1.0" encoding="utf-8"?>',
    )
    if not text.startswith('<?xml'):
        text = '<?xml version="1.0" encoding="utf-8"?>\n' + text
    path.write_text(text, encoding="utf-8")


def _indent(elem: ET.Element, level: int = 0) -> None:
    """Add pretty-print indentation to an ElementTree in-place."""
    indent = "\n" + "    " * level
    child_indent = "\n" + "    " * (level + 1)
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = child_indent
        if not elem.tail or not elem.tail.strip():
            elem.tail = indent
        for child in elem:
            _indent(child, level + 1)
        if not child.tail or not child.tail.strip():
            child.tail = indent
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = indent


def get_unfinished_messages(root: ET.Element) -> list[tuple[ET.Element, ET.Element]]:
    """Return list of (context_elem, message_elem) for all unfinished translations."""
    unfinished = []
    for ctx in root.findall("context"):
        for msg in ctx.findall("message"):
            trans = msg.find("translation")
            if trans is not None and trans.get("type") == "unfinished":
                unfinished.append((ctx, msg))
    return unfinished


# ── Main ───────────────────────────────────────────────────────────────────────

def process_language(client, locale: str, lang_name: str, dry_run: bool) -> int:
    ts_path = TS_DIR / f"{locale}.ts"
    if not ts_path.exists():
        print(f"  SKIP — {ts_path} not found")
        return 0

    tree = load_ts(ts_path)
    root = tree.getroot()

    unfinished = get_unfinished_messages(root)
    if not unfinished:
        print(f"  {locale}: nothing to translate")
        return 0

    print(f"  {locale}: {len(unfinished)} strings to translate into {lang_name}")

    # Extract sources
    sources = []
    for _ctx, msg in unfinished:
        src = msg.find("source")
        sources.append(src.text or "" if src is not None else "")

    if dry_run:
        print(f"  [dry-run] would translate {len(sources)} strings")
        return len(sources)

    # Translate in batches
    translations: list[str] = []
    for i in range(0, len(sources), BATCH_SIZE):
        batch = sources[i : i + BATCH_SIZE]
        print(f"    batch {i//BATCH_SIZE + 1}/{(len(sources)+BATCH_SIZE-1)//BATCH_SIZE} "
              f"({len(batch)} strings)…", end=" ", flush=True)
        try:
            result = translate_batch(client, batch, lang_name)
            translations.extend(result)
            print("ok")
        except Exception as e:
            print(f"ERROR: {e}")
            translations.extend(batch)   # fallback: keep source text
        time.sleep(0.3)   # stay well under rate limits

    # Write translations back into the XML tree
    for (_, msg), translation in zip(unfinished, translations):
        trans_elem = msg.find("translation")
        if trans_elem is not None:
            trans_elem.text = translation
            del trans_elem.attrib["type"]   # remove type="unfinished"

    save_ts(tree, ts_path)
    print(f"  {locale}: saved {len(translations)} translations → {ts_path.name}")
    return len(translations)


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch-translate .ts files with Claude")
    parser.add_argument("--dry-run", action="store_true", help="Show counts, do not translate")
    parser.add_argument("--locale", help="Only process this locale (e.g. de_DE)")
    args = parser.parse_args()

    import anthropic
    client = anthropic.Anthropic(api_key=get_api_key())

    total = 0
    for locale, lang_name in LANGUAGES.items():
        if args.locale and locale != args.locale:
            continue
        print(f"\n{locale} ({lang_name})")
        total += process_language(client, locale, lang_name, args.dry_run)

    print(f"\nDone — {total} strings translated total.")

    if not args.dry_run:
        print("\nCompile .qm files:")
        print("  ./scripts/compile_translations.sh")


if __name__ == "__main__":
    main()
