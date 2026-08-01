# `data/`

Runtime data the application reads at startup or on first use: word lists for
source-language leak detection, the game fonts the width-fit simulator measures
with, and the reference images the visual-context preview draws from.

Everything here is tracked in git (no LFS) and bundled into the frozen build by
`bethesda_strings_editor.spec`, which globs `data/*_words.txt`, `data/*.png` and
`data/fonts/*.ttf` into a `data/` tree that mirrors this one — so
`Path(__file__).parent.parent / "data"` resolves identically frozen and from
source. Total ≈ 49 MB, of which the Russian word list alone is 33 MB.

Numbers below are **measured**, not estimated: entry counts come from the
project's own loaders (CPython 3.10 in the `bethesda-strings-editor` conda env),
and the resident cost is the steady-state RSS delta after `gc.collect()`.

## Word lists — `*_words.txt`

Ten languages, one per checker module in `gui/`. They answer "is this token a
real word in language X", which is how the quality checker spots untranslated
English left in the output, Russian leaking into Ukrainian, and how
`_protect_english_text` decides a Latin-script token is worth protecting.

| File | Lines | Loaded | On disk | Resident | Read by |
|---|---:|---:|---:|---:|---|
| `english_words.txt` | 370,105 | 370,078 | 4.0 MB | +39 MB | `gui/en_word_checker.py` |
| `russian_words.txt` | 1,532,879 | 1,526,255 | 33.4 MB | +215 MB | `gui/ru_word_checker.py` |
| `ukrainian_words.txt` | 271,468 | 271,468 | 5.7 MB | +35 MB | `gui/uk_word_checker.py`, `gui/gender_checker.py` |
| `german_words.txt` | 50,000 | 49,180 | 647 KB | +5.6 MB | `gui/de_word_checker.py` |
| `spanish_words.txt` | 50,000 | 49,271 | 643 KB | +5.6 MB | `gui/es_word_checker.py` |
| `french_words.txt` | 50,000 | 46,403 | 638 KB | +5.6 MB | `gui/fr_word_checker.py` |
| `italian_words.txt` | 50,000 | 48,713 | 638 KB | +5.3 MB | `gui/it_word_checker.py` |
| `polish_words.txt` | 50,000 | 49,406 | 661 KB | +6.6 MB | `gui/pl_word_checker.py` |
| `portuguese_words.txt` | 50,000 | 49,565 | 635 KB | +5.6 MB | `gui/ptbr_word_checker.py` |
| `korean_words.txt` | 50,000 | 50,000 | 630 KB | +6.3 MB | `gui/ko_word_checker.py`, `gui/ko_particle_checker.py` |

**Two formats.** The seven 50 k lists are frequency lists — `word count` per
line, sorted by descending frequency. English, Russian and Ukrainian are plain
one-word-per-line lists. `gui/_word_checker_base.py` serves the seven by taking
`line.strip().split()[0].lower()`, so the count column is simply ignored, and
keeps a word when `len(word) >= min_word_len and word.isalpha()` — that filter
is the whole gap between *Lines* and *Loaded*. Korean loses nothing to it
because it uses `min_word_len=1` (one Hangul block is a whole syllable) where
Latin scripts use 3.

English, Russian and Ukrainian predate that base class and keep their own
loaders, each with a different filter over the lowercased whole line: English
`2 ≤ len ≤ 30 and isalpha()`, Russian `len ≥ 4` and alphabetic once hyphens are
removed, Ukrainian `len ≥ 3` and nothing else — so the Ukrainian set keeps its
148 multi-word entries (`аб ініціо`) while the Russian one drops its 13. The
script-based exclusions those two modules are named for (`і ї є ґ` cannot be
Russian; `ы э ё ъ` cannot be Ukrainian) are applied per lookup, not at load, so
they do not affect the counts above.

**Loading is per language pair, never wholesale.** All ten resident at once cost
**329 MB**; `ollama_worker.preload_language_dictionaries(src, tgt)` warms only
the pair, plus English (the English-leak check and English-text protection run
for every pair) plus Russian when the target is Ukrainian. An EN→KO session
therefore pays ~45 MB and never touches the 215 MB Russian set.
`tests/test_dict_preload.py` pins that behaviour.

**Two known quirks, both harmless:**

- `english_words.txt` uses CRLF line endings (as upstream ships it). `.strip()`
  absorbs them.
- `korean_words.txt` contains **4,123 pure-ASCII entries** (`you`, `the`, `i`, …)
  — English that survived in the OpenSubtitles corpus the list is built from.
  They are loaded like any other word, which is why `text_has_korean_words()`
  counts Hangul characters directly as its primary signal and only falls back to
  the list.

`ukrainian_words.txt` is the one file read twice, by two independent consumers
that do not share a cache: `uk_word_checker` keeps a lowercased `frozenset`, and
`gender_checker` builds its own `set` of the raw lines. A session that runs the
Ukrainian gender check as well as leak detection therefore holds ~78 MB of
Ukrainian words, not 35. Both are lazy, so a session that runs neither pays
nothing.

## Fonts — `fonts/*.ttf`

Starfield's own UI faces, exported from the game's Scaleform SWFs with JPEXS
Free Flash Decompiler (every file carries `FFDec v.0.0.0` in name record 8).
They make the width-fit simulator work with zero configuration: real advance
widths, so the tool measures what the game will draw rather than approximating
it with a substitute face.

| File | Family (name ID 1) | Glyphs | Cyrillic | Width vs `RF_35_M` | Role |
|---|---|---:|---|---|---|
| `RF_35_M.ttf` | `RF_35_M` | 573 | full | — | body |
| `RF_55_M.ttf` | `RF_55_M` | 573 | full | +10 % | bold (2nd choice) |
| `RF_55_SB.ttf` | `RF_55_SB` | 573 | full | +16 % | bold (1st choice) |
| `NB_Architekt_Light.ttf` | `NB Architekt Light` | 273 | none | — | Latin display |
| `NB_Architekt.ttf` | `NB Architekt` | 277 | none | +3 % vs Light | Latin bold |

- **"Full Cyrillic"** means all 74 letters Ukrainian and Russian need (`А`–`я`
  plus `Є є І і Ї ї Ґ ґ Ё ё`); the `RF_*` faces cover 134 codepoints of
  U+0400–U+04FF in total, and 127 of 128 in Latin Extended-A. **`NB Architekt`
  has zero Cyrillic glyphs** — that is why `width_fit` keeps a separate `latin`
  role instead of treating the faces as interchangeable, and why a Ukrainian
  string measured with it would be measured entirely from the fallback advance.
- All five are **1024 units/em** (the DefineFont2 grid they came off) and all
  five carry a complete `hmtx` table, so `FontSource.has_metrics` is true for
  every one of them — nothing here is ever measured with invented widths.
- All five also declare `usWeightClass 400` and subfamily `Regular`, **including
  the semibold**: the weight lives in the outlines, not the metadata. Selecting
  a face by OS/2 weight would pick the wrong one every time, which is why
  `width_fit._FAMILY_FILES` / `_ROLE_FILES` resolve by family name and filename.
  The width column is what actually differs, and it is why role matters: the
  +16 % is the mean over the 557 shared glyphs, and it rises to **+19 % on
  ALL-CAPS text** — precisely what button labels are.
- Read by `bethesda_strings/width_fit.py` (advance widths, via
  `font_checker.parse_ttf_glyphs`) and `gui/visual_context_preview.py` (which
  registers `RF_35_M`, `RF_55_M`, `NB_Architekt_Light` and `NB_Architekt` with
  Qt for on-screen rendering).

## Reference images — `*.png`

| File | Size | Loaded at runtime? |
|---|---|---|
| `dialogue_bg_tile.png` | 50 × 50 RGBA | **Yes** — `visual_context_preview._ensure_bg_tile()` |
| `dialogue_panel_ref.png` | 597 × 147 RGBA | **No** — calibration reference only |

`dialogue_bg_tile.png` is `DefineBitsLossless2` id 78 from `dialoguemenu.swf`:
white pixels at alpha 0–92, tiled over the subtitle panel fill to reproduce the
grain of the real dialogue box.

`dialogue_panel_ref.png` is the JPEXS export of the panel sprite itself, at the
597 × 147 SWF native scale the renderer's measurements in
`visual_context_preview.py` were verified against pixel by pixel. No code opens
it — it is kept so those measurements can be re-checked rather than re-derived.
Note that the spec's `data/*.png` glob still bundles it into releases (3.4 KB).

## Regenerating

| Files | How |
|---|---|
| `german` `spanish` `french` `italian` `polish` `portuguese` | `python scripts/download_lang_dicts.py` — hermitdave/FrequencyWords, 2018 corpus, MIT |
| `korean` | Same upstream (`ko/ko_50k.txt`), but **not** in that script's `LANG_CONFIGS` — it was fetched by hand |
| `english` | `python scripts/download_en_dict.py` — dwyl/english-words `words_alpha.txt` |
| `ukrainian` | `python scripts/build_uk_dict.py` — scrapes slovnyk.ua at ~2 req/s, 30–60 min, resumable |
| `russian` | No script in the repo; upstream is Poliklot/russian-words (1.5 M inflected forms) |
| `fonts/`, `*.png` | Extracted from the game with JPEXS Free Flash Decompiler; no script |

## Licensing

The repository's MIT licence covers the project's code. The word lists carry
their upstreams' terms (FrequencyWords is MIT), and `fonts/` and the reference
images are **extracted Bethesda game assets** — they are not the project's to
relicense, and are here because measuring a translation against the game's real
metrics is not possible without them.
