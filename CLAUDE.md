# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

```bash
python main.py
```

Requires PySide6 and `requests`. No `requirements.txt` exists — install manually:

```bash
pip install PySide6 requests
```

Logging goes to both stdout and `translator.log` in the project root.

## Ollama Model

The app uses a custom Ollama model named `translategemma3-st`. To create or recreate it:

```bash
ollama create translategemma3-st -f Modelfile
```

The `Modelfile` points to a local GGUF path (`/mnt/ssd/models/gguf/translategemma-27b-it.Q4_K_M.gguf`). All generation parameters in `Modelfile` are overridden at runtime by the app — the file is only used for direct `ollama run` invocations.

## Compiling UI Translations

The Ukrainian UI translation lives in `gui/translations/uk_UA.ts` (source) and `uk_UA.qm` (compiled binary). After editing the `.ts` file, recompile:

```bash
./scripts/compile_translations.sh
```

## Architecture

### Two-layer structure

**`bethesda_strings/`** — pure Python parsing library, no Qt dependency:
- `core.py` — `BethesdaStringFile` / `StringDataObject`: binary parser and writer for `.strings`, `.dlstrings`, `.ilstrings`. Header is 8 bytes; each directory entry is 8 bytes (ID + relative offset). `.dlstrings`/`.ilstrings` have a 4-byte length prefix per string; `.strings` use null termination.
- `esp_handler.py` — `EspFile`: parses ESP/ESM plugin files. Only handles *non-localized* plugins where text is stored directly in field buffers. Localized plugins (bit 0x80 in flags) use companion `.strings` files instead. Field/record combinations that contain translatable text are defined in `_FIELD_DEFS`.
- `xml_handler.py` — `XMLHandler`: imports/exports xTranslator SST XML format (matching xTranslator's own Pascal logic: match by `sID` hex first, fall back to `Source` text).
- `encoding.py` — `EncodingConverter`: encoding detection/conversion utilities.
- `operations.py` — factory functions for `BethesdaStringFile.filter_and_modify()`.

**`gui/`** — PySide6 application layer:
- `main_window.py` — `MainWindow`: top-level window. Owns the `OllamaWorker` thread, file-open/save logic, and coordinates all other components.
- `string_table.py` — `StringTableModel` / `StringTableView`: `QAbstractTableModel` with two display modes — `"strings"` (for `.strings`/`.dlstrings`/`.ilstrings`) and `"esp"` (for ESP/ESM). Column layout differs between modes.
- `ollama_worker.py` — `OllamaWorker`: runs in a dedicated `QThread`. Uses a `ThreadPoolExecutor` (default 10 workers) to call the Ollama HTTP API in parallel. Emits `translation_ready(index, text, string_id)`, `progress`, `error`, `finished`.
- `term_protector.py` — `TermProtector`: replaces protected terms (game names, proper nouns) with unique placeholder tokens before sending text to the AI model, then restores them after. Uses combined regex for performance with 8000+ terms.
- `translation_cache.py` — `TranslationCache`: thread-safe JSON-backed cache keyed on `sha256(text + model + source_lang + target_lang)`. Capped at 50,000 entries.
- `translation_memory.py` — `TranslationMemory`: pre-loaded map of string ID → correct translation from a prior human/assisted translation file. `OllamaWorker` consults this before calling the model, so known strings are never retranslated.
- `quality_checker.py` — post-translation QA: checks for missing game tags (`<Alias=…>`, `[PLYR]`, `%s`), encoding failures, suspicious length ratios, Russian character leakage into Ukrainian output, and AI repetition artifacts.
- `app_settings.py` — `AppSettings` dataclass (currently `CONFIG_VERSION = 9`). Settings are persisted as JSON (primary) and `QSettings` (secondary). `load_settings()` / `save_settings()` are the entry points.
- `theme_manager.py` — built-in QSS themes (`Slate`, etc.) plus custom theme support. Themes are applied as application-wide stylesheets.

### Key design notes

- **Native file dialogs are disabled** via `Qt.ApplicationAttribute.AA_DontUseNativeDialogs` (set before `QApplication` is created). This is intentional — GTK/KDE portal dialogs deadlock the Qt event loop on Linux tiling WMs.
- **Translation pipeline per string**: `TermProtector.protect()` → Ollama API call → `TermProtector.restore()` → `QualityChecker` → emit `translation_ready`.
- **Config location**: JSON config file is written to a platform-appropriate config directory (see `get_config_path()` in `app_settings.py`), not the project root.
- **Word checkers** (`en_word_checker.py`, `ru_word_checker.py`, `uk_word_checker.py`) use word lists from `data/` for detecting untranslated source-language text in output. They are preloaded in the background when `OllamaWorker` initializes.
- **Protected terms file**: `protected_terms_starfield_hq.txt` in the project root is the default terms list for Starfield localization.
