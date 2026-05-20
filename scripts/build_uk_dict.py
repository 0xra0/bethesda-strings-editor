#!/usr/bin/env python3
"""
Scrape Ukrainian words from slovnyk.ua and save to data/ukrainian_words.txt.

Run once to build the dictionary (takes ~30-60 min for all 33 letters):

    python scripts/build_uk_dict.py

Safe to interrupt and resume — already-saved words are skipped automatically.
Use --start-letter N to resume from a specific letter (1=А, 20=П, etc.).
"""

import argparse
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

BASE_URL = "https://slovnyk.ua"
LETTERS = 33   # Ukrainian alphabet А..Я
DELAY = 0.5    # seconds between requests (≈2 req/s — polite rate)

# s2 sub-page links on the TOC page:  href="index.php?s1=N&s2=M"
_SUBPAGE_RE = re.compile(r'href="index\.php\?s1=(\d+)&(?:amp;)?s2=(\d+)"')

# Word links on a word-list page:  href="index.php?swrd=слово"
_WORD_RE = re.compile(r'href="index\.php\?swrd=([^"]+)"', re.IGNORECASE)

LETTER_NAMES = {
    1: "А", 2: "Б", 3: "В", 4: "Г", 5: "Ґ", 6: "Д", 7: "Е", 8: "Є",
    9: "Ж", 10: "З", 11: "И", 12: "І", 13: "Ї", 14: "Й", 15: "К",
    16: "Л", 17: "М", 18: "Н", 19: "О", 20: "П", 21: "Р", 22: "С",
    23: "Т", 24: "У", 25: "Ф", 26: "Х", 27: "Ц", 28: "Ч", 29: "Ш",
    30: "Щ", 31: "Ь", 32: "Ю", 33: "Я",
}


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    adapter = requests.adapters.HTTPAdapter(max_retries=3)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def fetch(session: requests.Session, url: str, retries: int = 3) -> str:
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=30)
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.text
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = (attempt + 1) * 2
            print(f"    Retry {attempt + 1}/{retries} after {wait}s: {e}", file=sys.stderr)
            time.sleep(wait)
    return ""


def get_subpages(session: requests.Session, letter_idx: int, delay: float = DELAY) -> list[int]:
    """Fetch the TOC page for a letter and return all s2 values (> 0)."""
    url = f"{BASE_URL}/index.php?s1={letter_idx}&s2=0"
    html = fetch(session, url)
    time.sleep(delay)
    pages: set[int] = set()
    for m in _SUBPAGE_RE.finditer(html):
        if int(m.group(1)) == letter_idx:
            s2 = int(m.group(2))
            if s2 > 0:
                pages.add(s2)
    return sorted(pages)


def get_words_on_page(session: requests.Session, letter_idx: int, s2: int, delay: float = DELAY) -> list[str]:
    """Return all words found on one word-list sub-page."""
    url = f"{BASE_URL}/index.php?s1={letter_idx}&s2={s2}"
    html = fetch(session, url)
    time.sleep(delay)

    words: list[str] = []
    seen: set[str] = set()
    for m in _WORD_RE.finditer(html):
        raw = urllib.parse.unquote(m.group(1)).strip()
        word = raw.lower()
        # Skip single-char entries and non-Cyrillic-containing tokens
        if len(word) < 3:
            continue
        if not any("Ѐ" <= c <= "ӿ" for c in word):
            continue
        if word in seen:
            continue
        seen.add(word)
        words.append(word)
    return words


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--output", default="data/ukrainian_words.txt",
        help="Output file path (default: data/ukrainian_words.txt)",
    )
    parser.add_argument(
        "--start-letter", type=int, default=1, metavar="N",
        help="Start from letter N (1=А … 33=Я). Use to resume after interruption.",
    )
    parser.add_argument(
        "--delay", type=float, default=DELAY, metavar="SEC",
        help=f"Seconds between requests (default: {DELAY})",
    )
    args = parser.parse_args()

    out_path = Path(args.output)
    delay = args.delay
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Load already-saved words to support resume
    existing: set[str] = set()
    if out_path.exists():
        with open(out_path, encoding="utf-8") as fh:
            existing = {ln.strip() for ln in fh if ln.strip()}
        print(f"Resuming: {len(existing):,} words already in {out_path}")

    session = make_session()
    total_added = 0

    with open(out_path, "a", encoding="utf-8") as out_f:
        for letter_idx in range(args.start_letter, LETTERS + 1):
            letter = LETTER_NAMES.get(letter_idx, str(letter_idx))
            print(f"\n[{letter_idx:02d}/33] {letter} — fetching TOC...", end=" ", flush=True)

            try:
                subpages = get_subpages(session, letter_idx, delay)
            except Exception as e:
                print(f"FAILED to get TOC: {e}", file=sys.stderr)
                continue

            print(f"{len(subpages)} sub-pages", flush=True)
            letter_added = 0

            for s2 in subpages:
                try:
                    words = get_words_on_page(session, letter_idx, s2, delay)
                except Exception as e:
                    print(f"  s2={s2:3d}: FAILED — {e}", file=sys.stderr)
                    continue

                new = [w for w in words if w not in existing]
                for w in new:
                    out_f.write(w + "\n")
                    existing.add(w)
                letter_added += len(new)
                total_added += len(new)
                print(f"  s2={s2:3d}: {len(words):3d} words, {len(new):3d} new", flush=True)
                out_f.flush()

            print(f"  → letter {letter}: {letter_added:,} new words added")

    print(f"\nFinished. Added {total_added:,} new words. Total in file: {len(existing):,}")


if __name__ == "__main__":
    main()
