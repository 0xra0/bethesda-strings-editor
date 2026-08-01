#!/usr/bin/env python3
"""
Download word frequency lists for all supported target languages.

Source: hermitdave/FrequencyWords (MIT licence)
Format: "word count" per line, sorted by descending frequency.
Each file has ~50 000 words — enough to reliably detect untranslated output
and low target-language vocabulary coverage.

Covers every frequency-list language the app ships (de es fr it ko pl ptbr).
The other three word lists have their own sources: English via
scripts/download_en_dict.py, Ukrainian via scripts/build_uk_dict.py, and
Russian from Poliklot/russian-words with no script in this repo.

Running this reproduces the committed files byte for byte — mind the corpus
year pinned per language when editing (see _BASE_2016).

Usage:
    python scripts/download_lang_dicts.py           # download all
    python scripts/download_lang_dicts.py de fr pl  # specific languages
"""

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

import requests

# Base URL pattern for hermitdave/FrequencyWords (2018 corpus)
_BASE = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018"

# Korean is pinned to the older 2016 corpus, deliberately.  The six 2018 lists
# below are byte-identical to what is committed in data/, and Korean must stay
# reproducible the same way: the shipped data/korean_words.txt is the 2016
# ko_50k.txt, and it is the list ko_particle_checker's "stem is a real word"
# guard was measured against (0 false positives over 5,353 real Korean strings).
# Swapping in the 2018 corpus is a data change that invalidates that figure
# until it is re-measured, so it is not something a refresh run should do
# silently.
_BASE_2016 = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2016"

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
    "ko": {
        "url": f"{_BASE_2016}/ko/ko_50k.txt",   # 2016 — see _BASE_2016 above
        "out": "korean_words.txt",
        "display": "Korean",
    },
}


def _published_mode() -> int:
    """The mode a plain ``open(path, "wb")`` would have created.

    ``mkstemp`` deliberately makes its file private (0600) and ``os.replace``
    carries that mode across to the target, so a refresh would leave every word
    list readable only by the user who ran the script — where the committed
    files are 0644 and the previous straight-to-target write produced 0644 too.
    A word list the app cannot open is not a loud failure: the checker logs one
    warning and then answers "not a word" to everything.
    """
    umask = os.umask(0)
    os.umask(umask)
    return 0o666 & ~umask


def download(code: str, cfg: dict, out_dir: Path) -> bool:
    """Fetch one word list, replacing the committed file only on full success.

    The download goes to a temporary file beside the target and is moved into
    place with os.replace (atomic within a directory) once it has been received
    whole and decodes as UTF-8.  Streaming straight onto the target instead
    would leave a *truncated* list behind whenever the connection dropped
    mid-body — and a short word list does not fail loudly.  It loads fine and
    quietly answers "no, that is not a word": Ukrainian gender checking would
    stop recognising adjectives, and ko_particle_checker's "the stem is a real
    word" guard is what holds its measured zero false positives, so a clipped
    korean_words.txt turns it into an auto-fixer that rewrites correct text.
    Leaving the good file untouched is the only safe outcome of a failed run.

    Publishing through a temporary file means the two things a direct write got
    for free have to be restored explicitly: the size check must compare
    like-for-like byte counts (see the identity request below), and the mode
    must be the one open() would have used (see _published_mode).
    """
    out_path = out_dir / cfg["out"]
    print(f"  {cfg['display']:25s} → {out_path.name} ...", end=" ", flush=True)
    tmp_path: Optional[Path] = None
    try:
        # Ask for the body uncompressed.  Content-Length counts the bytes on the
        # wire, while iter_content hands back decoded ones, and these files are
        # served gzipped by default — so without this header the size check
        # below compares 676,499 decoded bytes against a declared 282,742 and
        # rejects a perfectly good download.  The extra ~370 KB per list is
        # nothing for a manual refresh; keeping the two numbers directly
        # comparable is what makes the check mean anything.
        r = requests.get(
            cfg["url"], timeout=60, stream=True,
            headers={"Accept-Encoding": "identity"},
        )
        r.raise_for_status()
        declared = int(r.headers.get("Content-Length", 0))

        received = 0
        fd, tmp_name = tempfile.mkstemp(dir=out_dir, prefix=f".{cfg['out']}.", suffix=".part")
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "wb") as fh:
            for chunk in r.iter_content(chunk_size=65536):
                fh.write(chunk)
                received += len(chunk)

        # A dropped connection can end iter_content early, so the advertised
        # length is what tells a short read apart from a complete one.  Should a
        # server compress anyway despite the identity request, Content-Length
        # describes the compressed body and only urllib3 knows how many bytes
        # came off the socket; if that count is unavailable the AttributeError
        # is caught below and the run fails without touching the good file.
        on_wire = received if not r.headers.get("Content-Encoding") else r.raw.tell()
        if declared and on_wire != declared:
            raise OSError(f"truncated download: got {on_wire:,} of {declared:,} bytes")

        # Decode before publishing: a corrupt body must not reach data/ either.
        lines = sum(1 for _ in tmp_path.open(encoding="utf-8"))
        if not lines:
            raise OSError("empty word list")

        os.chmod(tmp_path, _published_mode())
        os.replace(tmp_path, out_path)
        tmp_path = None
        print(f"OK  ({lines:,} words, {received // 1024} KB)")
        return True
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return False
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


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
