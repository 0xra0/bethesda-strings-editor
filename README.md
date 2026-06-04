# Bethesda Strings Editor

AI-assisted localization tool for Bethesda game files (Starfield). Translates `.strings`, `.dlstrings`, `.ilstrings`, BA2 archives, and ESP/ESM plugin files between all 11 Starfield-supported languages using a locally-running Ollama model or the Claude API, with a full quality-checking and review workflow.

![NexusMods Header](resources/nexusmods_header.png)

![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![PySide6](https://img.shields.io/badge/UI-PySide6-green) ![Ollama](https://img.shields.io/badge/AI-Ollama-orange) [![NexusMods](https://img.shields.io/badge/NexusMods-Starfield-D98F40?logo=nexusmods&logoColor=white)](https://www.nexusmods.com/starfield/mods/17158) [![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/0xra0/bethesda-strings-editor)

---

## Ollama models

| Model | Purpose | Hub |
|-------|---------|-----|
| `translategemma3-st` | Game string translation (Gemma 3 12B fine-tune) | [0xra/bethesda-translate](https://ollama.com/0xra/bethesda-translate) |
| `qcgemma4-st` | Translation quality checking (Gemma 4 E4B fine-tune) | [0xra/bethesda-qc](https://ollama.com/0xra/bethesda-qc) |

Pull both models:

```bash
ollama pull 0xra/bethesda-translate
ollama pull 0xra/bethesda-qc
```

Then register them locally under the names the app expects:

```bash
ollama cp 0xra/bethesda-translate translategemma3-st
ollama cp 0xra/bethesda-qc qcgemma4-st
```

---

## Supported languages

All 9 official Starfield languages plus Russian and Ukrainian for xTranslator-style workflows:

| Code | Language |
|------|----------|
| `en` | English |
| `de` | German |
| `es` | Spanish |
| `fr` | French |
| `it` | Italian |
| `ja` | Japanese |
| `pl` | Polish |
| `ptbr` | Portuguese (Brazil) |
| `zhhans` | Chinese (Simplified) |
| `ru` | Russian |
| `uk` | Ukrainian |

Each language pair gets its own system prompt with language-specific style rules, register guidance, and translation examples.

---

## Features

### Translation
- **Parallel AI translation** via [Ollama](https://ollama.com) with configurable concurrency (default 10 workers)
- **Claude API backend** — drop-in alternative to Ollama; select Haiku 4.5, Sonnet 4.6, or Opus 4.8 in Settings
- **Language-pair prompts** — dedicated system prompts for every source→target combination: register rules, script conventions (Japanese polite forms, Chinese simplified terminology), and native examples
- **Translation memory** — known strings are looked up before calling the model, so they are never retranslated
- **Translation cache** — SHA-256-keyed JSON cache (up to 50,000 entries) persisted across sessions
- **Term protector** — 8,000+ Starfield-specific terms (names, places, UI labels) are replaced with placeholder tokens before the AI sees the text and restored afterward, preventing mistranslation of proper nouns
- **Newline / spacing restoration** — when the model drops structural `[[STRUCT_BREAK]]` tokens, output is re-split proportionally and per-line indentation is restored from the original
- **Mixed-script repair** — stray Latin letters inside Cyrillic words (e.g. `dослідницький` → `дослідницький`) are corrected automatically
- **Glossary system** — CSV/TBX/JSON glossary with in-app editor, term suggestions dock, and automatic injection into AI prompts

### File support
- **Binary string files**: `.strings` (null-terminated), `.dlstrings` / `.ilstrings` (length-prefixed)
- **BA2 archives**: read and write Starfield v2 BA2 files (GNRL type, zlib-compressed); picker dialog for multi-entry archives
- **ESP/ESM plugins**: non-localized plugins where text is stored directly in field buffers
- **xTranslator SST XML**: import/export in xTranslator format (match by `sID`, fallback to source text)
- **Drag-and-drop** file loading with format validation

### Quality assurance
- **Quality checker** with 20+ checks: missing/extra game tags, empty or untranslated strings, source-language leakage, English leak, suspicious length ratios, newline mismatches, truncated AI output, AI artifact prefixes, encoding failures, script coverage (CJK), and more — for all 11 supported languages
- **Hunspell spell-check** — optional per-language spell checking using system dictionaries; fires `SPELL_ERROR` warnings on misspelled lowercase words (install dictionaries with `pacman -S hunspell-uk` / `apt install hunspell-uk` etc.)
- **AI quality model** (`qcgemma4-st`) — fine-tuned Gemma 4 E4B that detects 16 issue codes with structured `VERDICT` output and `AUTOFIX`/`RETRANSLATE` recommendations
- **Auto-fix** for mechanically correctable issues (whitespace, capitalization, character substitution, missing newlines, truncated translations)
- **Retranslation queue** — strings that need AI to fix are queued and retranslated with a per-string hint describing what went wrong
- **Consistency checker** — finds the same source string translated differently across the file, with canonical-form picker and batch replace
- **Standalone fix script** (`scripts/apply_quality_fixes.py`) — apply auto-fixes from a JSON quality report to an SST XML file without opening the GUI

### UI / workflow
- **Command palette** (Ctrl+K) and vim-style navigation (j/k, G)
- **Keyboard shortcuts** editor — rebind any action
- **F7** → jump to next untranslated string; **Ctrl+Enter** → approve; **Ctrl+R** → reject
- **Version comparison** — diff two game versions, migrate unchanged translations, export CSV/HTML reports
- **Pre-translation estimator** — scores each string 0–100 to predict translation difficulty before the AI runs
- **Encoding detection** — auto-detects UTF-8, CP1251, CP1252, CP1250 (Polish), and BOM variants; override per-file
- **Themes** — built-in Slate theme plus custom QSS support
- **UI translations** — interface available in Ukrainian ✓, German, Spanish, French, Polish, Czech (community WIP)

---

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) running locally (or a Claude API key for the Claude backend)

```bash
pip install -r requirements.txt
```

Core dependencies: `PySide6>=6.6`, `requests>=2.31`, `cryptography>=43.0`, `anthropic>=0.25`

Optional: `keyring>=25.0` (API key storage), `hunspell>=0.5.5` or `spylls>=0.1.7` (spell-check Python bindings — the hunspell CLI is used as a fallback if neither is installed)

---

## Running

```bash
python main.py
```

Logs are written to both stdout and `translator.log` in the project root.

---

## Local model setup

If you prefer to build the models from a local GGUF file instead of pulling from the hub:

```bash
# Edit Modelfile to set the correct FROM path, then:
ollama create translategemma3-st -f Modelfile

# Edit Modelfile.qc to set the correct FROM path, then:
ollama create qcgemma4-st -f Modelfile.qc
```

All generation parameters in the Modelfiles are overridden at runtime by the app (except `min_p` and `repeat_last_n` for the translation model).

---

## Project structure

```
bethesda_strings/           Pure Python parsing library (no Qt dependency)
  core.py                   Binary parser/writer for .strings/.dlstrings/.ilstrings
  ba2_handler.py            BA2 archive reader/writer (Starfield v2, FO4 v1)
  esp_handler.py            ESP/ESM plugin parser (non-localized plugins)
  xml_handler.py            xTranslator SST XML import/export
  encoding.py               Encoding detection and conversion (UTF-8/CP1251/CP1252/CP1250/GBK/…)
  version_diff.py           Game-version diff and translation migration

gui/                        PySide6 application layer
  main_window.py            Top-level window, file I/O, translation orchestration
  ollama_worker.py          QThread worker — parallel calls, per-language prompts, post-processing
  claude_translation_worker.py  Claude API drop-in replacement for OllamaWorker
  quality_checker.py        Post-translation QA checks and auto-fix
  quality_dialog.py         QA results dialog with filtering, auto-fix, retranslation
  spell_checker.py          Hunspell spell-check wrapper (3 backends: lib / spylls / CLI)
  string_table.py           QAbstractTableModel for strings and ESP modes
  term_protector.py         Placeholder-based term protection (8000+ terms)
  translation_cache.py      SHA-256-keyed persistent translation cache
  translation_memory.py     Pre-loaded map of string ID → known-good translation
  glossary.py               Glossary data model, CSV/TBX/JSON I/O
  consistency_checker.py    Finds inconsistent translations of identical source strings
  keyboard_manager.py       Rebindable shortcuts, vim navigation, command palette
  app_settings.py           AppSettings dataclass, JSON + QSettings persistence

scripts/
  apply_quality_fixes.py    CLI: apply auto-fixes from a JSON report to SST XML
  extract_sharegpt_dataset.py  Export EN→target string pairs as ShareGPT JSONL for fine-tuning
  create_qc_dataset.py      Generate QC training dataset (14,928 examples, 16 issue codes)
  compile_translations.sh   Recompile .ts → .qm UI translation files

data/
  english_words.txt         Word list for English-leak detection
  russian_words.txt         Word list for untranslated-source detection
  ukrainian_words.txt       Word list for Ukrainian coverage checks
```

---

## UI translation

UI translations live in `gui/translations/<locale>.ts`. After editing any `.ts` file:

```bash
./scripts/compile_translations.sh
```

Supported locales: `uk_UA`, `de_DE`, `fr_FR`, `es_ES`, `pl_PL`, `cs_CZ`.

---

## Tests

```bash
python -m pytest tests/
```

---

## License

MIT — see [LICENSE](LICENSE).
