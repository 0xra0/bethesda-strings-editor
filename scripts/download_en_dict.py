#!/usr/bin/env python3
"""
Download the English word list from dwyl/english-words and save to
data/english_words.txt (~4 MB, 370 k words).

Usage:
    python scripts/download_en_dict.py

Source: https://github.com/dwyl/english-words
"""

import sys
from pathlib import Path

import requests

URL = (
    "https://raw.githubusercontent.com/dwyl/english-words/master/words_alpha.txt"
)
OUT = Path("data/english_words.txt")


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    print(f"Downloading {URL} ...")
    r = requests.get(URL, timeout=60, stream=True)
    r.raise_for_status()

    total = int(r.headers.get("Content-Length", 0))
    received = 0
    with open(OUT, "wb") as fh:
        for chunk in r.iter_content(chunk_size=65536):
            fh.write(chunk)
            received += len(chunk)
            if total:
                pct = received * 100 // total
                print(f"\r  {pct:3d}%  {received // 1024} KB", end="", flush=True)

    print(f"\nSaved {received // 1024} KB → {OUT}")
    lines = OUT.read_text(encoding="utf-8").count("\n")
    print(f"Word count: {lines:,}")


if __name__ == "__main__":
    main()
