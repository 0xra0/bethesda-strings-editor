#!/usr/bin/env python3
"""
Download word frequency lists for all supported target languages.

Source: hermitdave/FrequencyWords (MIT licence)
Format: "word count" per line, sorted by descending frequency.
Each file has ~50 000 words — enough to reliably detect untranslated output
and low target-language vocabulary coverage.

Usage:
    python scripts/download_lang_dicts.py           # download all
    python scripts/download_lang_dicts.py de fr pl  # specific languages
"""

import argparse
import sys
from pathlib import Path

import requests

# Base URL pattern for hermitdave/FrequencyWords (2018 corpus)
_BASE = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018"

LANG_CONFIGS = {
    "de": {
        "url": f"{_BASE}/de/de_50k.txt",
        "out": "german_words.txt",
        "display": "German",
    },
    "es": {
        "url": f"{_BASE}/es/es_50k.txt",
        "out": "spanish_words.txt",
        "display": "Spanish",
    },
    "fr": {
        "url": f"{_BASE}/fr/fr_50k.txt",
        "out": "french_words.txt",
        "display": "French",
    },
    "it": {
        "url": f"{_BASE}/it/it_50k.txt",
        "out": "italian_words.txt",
        "display": "Italian",
    },
    "pl": {
        "url": f"{_BASE}/pl/pl_50k.txt",
        "out": "polish_words.txt",
        "display": "Polish",
    },
    "ptbr": {
        "url": f"{_BASE}/pt_br/pt_br_50k.txt",
        "out": "portuguese_words.txt",
        "display": "Portuguese (Brazilian)",
    },
}


def download(code: str, cfg: dict, out_dir: Path) -> bool:
    out_path = out_dir / cfg["out"]
    print(f"  {cfg['display']:25s} → {out_path.name} ...", end=" ", flush=True)
    try:
        r = requests.get(cfg["url"], timeout=60, stream=True)
        r.raise_for_status()
        total = int(r.headers.get("Content-Length", 0))
        received = 0
        with open(out_path, "wb") as fh:
            for chunk in r.iter_content(chunk_size=65536):
                fh.write(chunk)
                received += len(chunk)
        lines = sum(1 for _ in out_path.open(encoding="utf-8"))
        size_kb = received // 1024
        print(f"OK  ({lines:,} words, {size_kb} KB)")
        return True
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "langs",
        nargs="*",
        metavar="LANG",
        help=f"Language codes to download (default: all). Choices: {', '.join(LANG_CONFIGS)}",
    )
    parser.add_argument(
        "--output-dir", default="data",
        help="Directory to write word list files (default: data/)",
    )
    args = parser.parse_args()

    chosen = args.langs if args.langs else list(LANG_CONFIGS)
    unknown = [c for c in chosen if c not in LANG_CONFIGS]
    if unknown:
        print(f"Unknown language codes: {', '.join(unknown)}", file=sys.stderr)
        print(f"Available: {', '.join(LANG_CONFIGS)}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Downloading {len(chosen)} word list(s) to {out_dir}/\n")
    ok = 0
    for code in chosen:
        if download(code, LANG_CONFIGS[code], out_dir):
            ok += 1

    print(f"\n{'All' if ok == len(chosen) else ok}/{len(chosen)} downloads succeeded.")
    if ok < len(chosen):
        sys.exit(1)


if __name__ == "__main__":
    main()
