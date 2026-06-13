# Bethesda Strings Editor

AI-assisted localization tool for Bethesda game files (Starfield). Translates `.strings`, `.dlstrings`, `.ilstrings`, BA2 archives, and ESP/ESM plugin files between all 11 Starfield-supported languages using a locally-running Ollama model or the Claude API, with a full quality-checking and review workflow.

![NexusMods Header](resources/nexusmods_header.png)

![Python](https://img.shields.io/badge/Python-3.10%2B-blue) ![PySide6](https://img.shields.io/badge/UI-PySide6-green) ![Ollama](https://img.shields.io/badge/AI-Ollama-orange) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![NexusMods](https://img.shields.io/badge/NexusMods-Starfield-D98F40?logo=nexusmods&logoColor=white)](https://www.nexusmods.com/starfield/mods/17158) [![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/0xra0/bethesda-strings-editor) [![Built with Claude](https://img.shields.io/badge/Built%20with-Claude-8A2BE2?logo=anthropic&logoColor=white)](https://claude.ai)

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
- **Newline / spacing restoration** — when the model drops structural tokens, output is re-split proportionally and per-line indentation is restored from the original
- **Mixed-script repair** — stray Latin letters inside Cyrillic words are corrected automatically
- **Glossary system** — CSV/TBX/JSON glossary with in-app editor, term suggestions dock, and automatic injection into AI prompts
- **Character Persona Profiling** — assign a voice profile to any string or quest (Freestar Ranger, SysDef Officer, Crimson Fleet Pirate, House Va'ruun Zealot, UC Civilian, Robot/Automaton, Narrator, or custom); each profile overrides the AI system prompt and temperature so NPC dialogue stays in character
- **Lore RAG** — local SQLite FTS5 lore database (built-in UESP downloader); relevant faction, location, and character articles are retrieved per string and injected into the AI prompt so terminology stays accurate
- **Pre-translation estimator** — scores each string 0–100 to predict translation difficulty before the AI runs
- **Skip string types** — exclude Book, Note, or other categories from AI batch translation
- **Protect named entities** — opt-in setting to extend term protection to faction/ship/character names inferred from the loaded file
- **Claude pre-flight cost estimator** — shows token count and estimated cost before starting a batch translation

### File support
- **Binary string files**: `.strings` (null-terminated), `.dlstrings` / `.ilstrings` (length-prefixed)
- **BA2 archives**: read and write Starfield v2 BA2 files (GNRL type, zlib-compressed); picker dialog for multi-entry archives
- **ESP/ESM plugins**: non-localized plugins where text is stored directly in field buffers
- **xTranslator SST XML**: import/export in xTranslator format (match by `sID`, fallback to source text)
- **Drag-and-drop** file loading with format validation
- **NexusMods Translation Browser** — search NexusMods for existing translation mods, browse their files, and import `.strings`/`.dlstrings`/`.ilstrings` directly as a Translation Memory or merge into the current file; zip archives are automatically extracted
- **Weblate sync** — push/pull strings to a self-hosted or hosted Weblate instance

### Quality assurance
- **Quality checker** — 20+ checks: missing/extra game tags, empty or untranslated strings, source-language leakage, English leak, suspicious length ratios, newline mismatches, truncated AI output, AI artifact prefixes, encoding failures, script coverage (CJK), and more
- **Hunspell spell-check** — per-language `SPELL_ERROR` warnings using system dictionaries (`pacman -S hunspell-uk` / `apt install hunspell-uk`)
- **AI quality model** (`qcgemma4-st`) — fine-tuned Gemma 4 E4B that detects 16 issue codes with chain-of-thought reasoning and structured `VERDICT: GOOD / ISSUES_FOUND` output with `AUTOFIX`/`RETRANSLATE` recommendations
- **Font & Glyph Checker** — parses Scaleform SWF font atlases and TTF/OTF cmap tables; flags translation characters that will render as squares in-game and suggests auto-fixable substitutes
- **Auto-fix** for mechanically correctable issues (whitespace, capitalization, character substitution, missing newlines, truncated translations)
- **Retranslation queue** — strings flagged by QC are queued and retranslated with a per-string hint describing what went wrong
- **Error-code filter** — filter QC results by code (MISSING_TAGS, NEWLINE_COUNT_MISMATCH, etc.)
- **Consistency checker** — finds the same source string translated differently across the file, with canonical-form picker and batch replace
- **Plugin validator** — scans ESP/ESM for NPC dialogue camera bugs: missing Localized flag, stray DIAL/SCEN/INFO records, ONAM overrides, missing master dependencies

### Review tools
- **Visual Context Preview** (Ctrl+Shift+P) — dockable panel that renders the current string inside a faithful recreation of the Bethesda UI using actual game fonts extracted from `fonts_uk.swf` / `fonts_en.swf`; game-accurate dialogue panel with dark gradient background, noise texture, and pixel-exact borders sourced from `dialoguemenu.swf`; auto-detects context type (Dialogue, Quest, Book, Note, Terminal, UI); colour-coded overflow indicator; Source/Translation/Both view modes
- **Dialogue Tree Visualizer** — interactive quest → topic → response node graph (Translation → Dialogue Tree) rendered with the Starfield dark-space visual theme; click any node to jump to that string in the table
- **Audio / TTS Preview** (Ctrl+Shift+A) — dockable panel with eSpeak-NG and Piper backends; synthesizes a TTS read-out of the translation so timing can be compared against the original game audio; colour-coded timing bar (green ≤ 110 %, orange ≤ 130 %, red > 130 %)
- **Version comparison** — diff two game versions, migrate unchanged translations, export CSV/HTML reports; batch folder comparison
- **Diff viewer** — side-by-side word-level or character-level diff; editable right pane with live diff update; HTML export
- **Advanced search** — regex and fuzzy search across source and translation columns; batch Find & Replace

### UI / workflow
- **Zen / Focus Mode** (F11) — full-screen distraction-free editor with large source and translation panels, pending-string counter, per-string status badge
- **Multi-monitor / detached panes** — Translation Editor dock (Ctrl+Shift+E) floats to any monitor; Pop Out String List (Ctrl+Shift+L) opens a second table window sharing the same selection model
- **Claude AI Assistant dock** (Ctrl+Shift+C) — chat about the current string and apply Claude's suggested translation with one click
- **Command palette** (Ctrl+K) and vim-style navigation (j/k, G)
- **Keyboard shortcuts editor** — rebind any action
- **F7** → jump to next untranslated; **Ctrl+Enter** → approve; **Ctrl+R** → reject
- **Encoding detection** — auto-detects UTF-8, CP1251, CP1252, CP1250, GBK/GB2312, BOM variants; override per-file
- **Themes** — built-in Slate / High Contrast themes plus custom QSS support; colour-blind mode
- **UI translations** — interface available in Ukrainian ✓, German, Spanish, French, Polish, Czech (community WIP)
- **NexusMods upload** — v3 multipart upload client with presigned S3 URLs (File → Upload to NexusMods)
- **Desktop notifications** on batch completion
- **Crash recovery** — periodic auto-save; recovery dialog offered at startup if the previous session ended unexpectedly
- **Security audit log** — append-only JSON-lines file recording file operations and translation batches; API keys stored in system keyring with AES-256-GCM file fallback

---

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) running locally (or a Claude API key for the Claude backend)
- Audio playback requires `paplay` (PulseAudio), `ffplay`, or `aplay` — any of the three will be auto-detected

```bash
pip install -r requirements.txt
```

Core dependencies: `PySide6>=6.6`, `requests>=2.31`, `cryptography>=43.0`, `anthropic>=0.25`

Optional: `keyring>=25.0` (API key storage), `hunspell>=0.5.5` or `spylls>=0.1.7` (spell-check — hunspell CLI used as fallback)

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

All generation parameters in the Modelfiles are overridden at runtime by the app.

---

## Project structure

```
bethesda_strings/              Pure Python parsing library (no Qt dependency)
  core.py                      Binary parser/writer for .strings/.dlstrings/.ilstrings
  ba2_handler.py               BA2 archive reader/writer (Starfield v2, FO4 v1)
  esp_handler.py               ESP/ESM plugin parser (non-localized plugins)
  xml_handler.py               xTranslator SST XML import/export
  encoding.py                  Encoding detection and conversion
  version_diff.py              Game-version diff and translation migration

gui/                           PySide6 application layer
  main_window.py               Top-level window, file I/O, translation orchestration
  ollama_worker.py             QThread worker — parallel calls, per-language prompts
  claude_translation_worker.py Claude API drop-in replacement for OllamaWorker
  claude_chat_panel.py         Dockable AI assistant chat panel
  visual_context_preview.py    Game-accurate string rendering using extracted SWF fonts/assets
  dialogue_tree_dialog.py      Interactive quest → topic → response node graph
  audio_preview_panel.py       TTS preview dock (eSpeak-NG / Piper backends)
  tts_engine.py                TTS synthesis engine (eSpeak-NG, Piper, audio index)
  focus_overlay.py             Zen / full-screen focus mode
  lore_rag_manager.py          SQLite FTS5 lore database + UESP downloader
  lore_rag_dialog.py           Lore database management dialog
  quality_checker.py           Post-translation QA checks and auto-fix
  quality_dialog.py            QA results dialog with filtering, auto-fix, retranslation
  ai_qc_worker.py              Worker thread for qcgemma4-st quality model
  spell_checker.py             Hunspell spell-check wrapper (3 backends: lib / spylls / CLI)
  font_checker_dialog.py       SWF/TTF glyph coverage checker
  string_table.py              QAbstractTableModel for strings and ESP modes
  term_protector.py            Placeholder-based term protection (8000+ terms)
  translation_cache.py         SHA-256-keyed persistent translation cache
  translation_memory.py        Pre-loaded map of string ID → known-good translation
  glossary.py                  Glossary data model, CSV/TBX/JSON I/O
  consistency_checker.py       Finds inconsistent translations of identical source strings
  version_compare_dialog.py    Game-version diff UI, migration, CSV/HTML export
  diff_viewer.py               Side-by-side word/character-level diff viewer
  pre_translation_estimator.py Difficulty scorer (0–100) with weight learning
  profile_editor_dialog.py     Character persona profile editor
  profile_assign_dialog.py     Assign persona profiles to strings / quests
  keyboard_manager.py          Rebindable shortcuts, vim navigation, command palette
  nexusmods_uploader.py        NexusMods v3 multipart upload client
  nexusmods_browser_dialog.py  NexusMods translation browser and importer
  weblate_client.py            Weblate REST API client
  weblate_sync_dialog.py       Weblate push/pull dialog
  app_settings.py              AppSettings dataclass, JSON + QSettings persistence
  secret_store.py              API key storage (keyring + AES-256-GCM fallback)
  audit_log.py                 Append-only security audit log (JSON-lines)
  crash_recovery.py            Periodic auto-save and recovery dialog

data/
  fonts/                       Game fonts extracted from Starfield SWF assets
    RF_35_M.ttf                Cyrillic body font ($MAIN_Font / $NB_Grotesk_Semibold, UK locale)
    RF_55_M.ttf                Cyrillic bold
    RF_55_SB.ttf               Cyrillic semi-bold
    NB_Architekt_Light.ttf     Latin body font ($MAIN_Font, EN locale)
    NB_Architekt.ttf           Latin bold
  dialogue_bg_tile.png         50×50 noise tile from dialoguemenu.swf (used in preview)
  *_words.txt                  Word lists for source-language leak detection (11 languages)

scripts/
  apply_quality_fixes.py       CLI: apply auto-fixes from a JSON report to SST XML
  extract_sharegpt_dataset.py  Export EN→target string pairs as ShareGPT JSONL
  create_qc_dataset.py         Generate QC training dataset (14,928 examples, 16 issue codes)
  compile_translations.sh      Recompile .ts → .qm UI translation files
  download_lang_dicts.py       Download Hunspell dictionaries for all supported languages
  extract_starfield_glossary.py Build starfield_glossary.json from string files
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
