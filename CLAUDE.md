# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Application

```bash
python main.py
```

Dependencies are in `requirements.txt`:

```bash
pip install -r requirements.txt
# Core: PySide6>=6.6, requests>=2.31, cryptography>=43.0
# Optional: keyring>=25.0  (falls back to encrypted file store if absent)
```

Logging goes to both stdout and `translator.log` in the project root.

## Ollama Models

### Translation model (`translategemma3-st`)

```bash
ollama create translategemma3-st -f Modelfile
```

The `Modelfile` points to a local GGUF path (`/mnt/ssd/models/gguf/translategemma-27b-it.Q4_K_M.gguf`). All generation parameters in `Modelfile` are overridden at runtime — the file is only used for direct `ollama run` invocations.

### Quality-check model (`qcgemma4-st`)

Fine-tuned Gemma 4 E4B IT on `scripts/qc_dataset_sharegpt.jsonl` (14,928 examples, 16 issue codes). Creates or recreates the model:

```bash
ollama create qcgemma4-st -f Modelfile.qc
```

`Modelfile.qc` points to `/home/home/.unsloth/studio/exports/gemma-4-e4b-it-unsloth-bnb-4bit-gguf/gemma-4-e4b-it.Q4_K_M.gguf`. Uses `temperature 0.0` and `num_ctx 8192` for deterministic structured output. `num_predict 1024` to give the model's chain-of-thought reasoning enough budget before the structured VERDICT block. Input format matches the training data: `Check this Ukrainian translation:\n\nSource (English):\n{src}\n\nTranslation (Ukrainian):\n{tgt}`. Output is `VERDICT: GOOD` or `VERDICT: ISSUES_FOUND\nCODES: …\nSEVERITY: …\nDETAILS:\n- …\nACTION: AUTOFIX|RETRANSLATE`.

## Compiling UI Translations

UI translations live in `gui/translations/<locale>.ts` (source) and `.qm` (compiled binary). Supported locales: `uk_UA`, `de_DE`, `es_ES`, `fr_FR`, `ko_KR`, `pl_PL`, `cs_CZ`. After editing any `.ts` file:

```bash
./scripts/compile_translations.sh
```

## Architecture

### Two-layer structure

**`bethesda_strings/`** — pure Python parsing library, no Qt dependency:

- `core.py` — `BethesdaStringFile` / `StringDataObject`: binary parser/writer for `.strings`, `.dlstrings`, `.ilstrings`. Header is 8 bytes; each directory entry is 8 bytes (ID + relative offset). `.dlstrings`/`.ilstrings` have a 4-byte length prefix per string; `.strings` use null termination.
- `esp_handler.py` — `EspFile`: parses ESP/ESM/ESL plugin files. Only handles *non-localized* plugins (text stored directly in field buffers). Localized plugins (bit 0x80 in flags) use companion `.strings` files instead. Translatable field/record combinations are in `_FIELD_DEFS`. Also exposes module-level `scan_vmad(path)` / `apply_vmad_translations(path, edits)` (independent of `EspFile`, so they work on *both* localized and non-localized plugins): a GRUP-aware walk that decompresses record bodies, finds the `VMAD` field, parses script-property strings and rewrites only edited string spans — recomputing record + parent GRUP sizes and re-compressing compressed records. Writes a `.bak` before saving. See `vmad_handler.py`.
- `vmad_handler.py` — pure-Python VMAD (Papyrus script attachment) parser/classifier/safe-editor. `parse_vmad()` never raises (sets `fully_parsed=False` on truncation). Collects only String (type 2) and ArrayString (type 12) property *values* — never script/property *names* (editing those breaks binding). `classify_string_property()` tags each value `RISK_TRANSLATABLE` / `RISK_REVIEW` / `RISK_LOCKED` (resource paths/extensions/identifiers/event names → locked; multi-word phrases & text-named props → translatable; ambiguous single tokens → review). `replace_strings()` is a byte-splice editor: only edited value spans (and their 2-byte length prefixes) are rewritten, every other byte is copied verbatim, so unmodelled VMAD sub-structures (QUST aliases, script fragments) survive untouched. `find_vmad_field()` locates the VMAD payload in a decompressed record body (XXXX-oversize aware).
- `ba2_handler.py` — `BA2File`: reads BA2 archives (GNRL type only, zlib-compressed). Supports Fallout 4 v1 and Starfield v2 formats. Used to open `.strings` files bundled inside BA2 archives.
- `wwise_voice.py` — `VoiceIndex`: maps a Starfield dialogue **FormID → original voice clip** packed inside `*Voices*.ba2` archives. Starfield ships voice as Wwise `.wem` (RIFF/WAVE, Vorbis tag `0xFFFF`) at `sound/voice/<plugin>.esm/<voicetype>/<8hex-formid>.wem` — *not* `.fuz` like Skyrim/FO4. One FormID maps to several voice types, so `find()` returns a list. `ffmpeg` cannot decode `.wem`; `get_wav()` shells out to `vgmstream-cli` and caches the decoded WAV. Pure helpers: `classify_archive_language()` (no `_xx` suffix = English base game), `form_id_from_name()`, `voice_type_from_name()`.
- `xml_handler.py` — `XMLHandler`: imports/exports xTranslator SST XML format (match by `sID` hex first, fall back to `Source` text — mirrors xTranslator's Pascal logic).
- `encoding.py` — `EncodingConverter`: encoding detection and conversion (UTF-8/CP1251/CP1252/BOM).
- `operations.py` — factory functions for `BethesdaStringFile.filter_and_modify()`.
- `version_diff.py` — `VersionDiff`: computes per-string diff between two versions of the same file (added/removed/changed). Used by the version comparison dialog and batch folder comparison.

**`gui/`** — PySide6 application layer:

#### Core window & table
- `main_window.py` — `MainWindow`: top-level window. Owns the worker thread, file-open/save logic, audit log, crash recovery, and coordinates all other components.
- `string_table.py` — `StringTableModel` / `StringTableView`: `QAbstractTableModel` with two display modes — `"strings"` (for `.strings`/`.dlstrings`/`.ilstrings`) and `"esp"` (for ESP/ESM). Column layout differs between modes. Emits `Ctrl+C/V/Shift+C/Shift+V` clipboard shortcuts and a status-bar `Total/Done/Left %` + ETA label during batches.
- `app_settings.py` — `AppSettings` dataclass (`CONFIG_VERSION = 28`). Persisted as JSON (primary) + `QSettings` (secondary). Entry points: `load_settings()` / `save_settings()`. `nexusmods_api_key` is XOR+base64 obfuscated on disk via `_obfuscate()`/`_deobfuscate()`; in-memory value is always plaintext.
- `settings_dialog.py` — full settings UI (backend selector, model, keys, QC options, NexusMods, Audio/TTS, shortcuts, etc.).
- `theme_manager.py` — built-in QSS themes (`Slate`, etc.) + custom theme support. Applied as application-wide stylesheets.
- `theme_dialog.py` — theme picker/editor dialog.

#### Translation backends
- `ollama_worker.py` — `OllamaWorker`: runs in a dedicated `QThread`. Uses a `ThreadPoolExecutor` (default 10 workers) to call the Ollama HTTP API in parallel. Emits `translation_ready(index, text, string_id)`, `progress`, `error`, `finished`. Contains `_restore_dropped_tags()`: post-translation safety net that re-inserts Bethesda game tags (`<mag>`, `<dur>`, `<area>`, etc.) that the model dropped, using fractional position heuristics.
- `claude_client.py` — shared Claude API client (translation, chat, quality review). Manages API key via `SecretStore`. Model registry: Haiku 4.5 (default), Sonnet 4.6, Opus 4.8.
- `claude_translation_worker.py` — `ClaudeTranslationWorker`: drop-in replacement for `OllamaWorker` that calls the Claude API instead of Ollama. Selected via `AppSettings.translation_backend`.
- `claude_chat_panel.py` — dockable `QDockWidget` for chatting with Claude about the current string and applying its suggested translation.

#### Translation pipeline helpers
- `term_protector.py` — `TermProtector`: replaces protected terms with unique placeholder tokens before AI calls, restores them after. Uses a combined regex over 8000+ terms for performance. `STRUCTURAL_PATTERNS` also locks **deliberately-obfuscated in-game codes** (encrypted notes, passwords, scrambled terminal text — garbage by design, e.g. `VH1QCR4P$KU`, `WWFX`): `obf_code` (ASCII alnum run + embedded `$ # @` with ≥1 letter and ≥1 digit-or-symbol) and `obf_acronym` (all-uppercase vowel-less run; vowel-bearing acronyms like `DNA`→`ДНК` stay translatable). Overlap is resolved longest-span-wins, so `obf_code` beats the shorter `chemical_formula`/`form_id` on the same token.
- `translation_cache.py` — `TranslationCache`: thread-safe JSON-backed cache keyed on `sha256(text + model + source_lang + target_lang)`. Capped at 50,000 entries.
- `translation_memory.py` — `TranslationMemory`: pre-loaded map of string ID → correct translation from a prior file. Consulted before any model call so known strings are never retranslated.
- `glossary.py` — `GlossaryManager`: CSV/TBX/JSON I/O, suggest dock, AI prompt injection, GLOSSARY_MISMATCH QC check.
- `glossary_editor.py` — full-screen glossary editing dialog.
- `quick_add_term_dialog.py` — lightweight dialog to add a single term to the glossary from the main table.
- `protected_terms_dialog.py` — dialog for managing the protected terms list.
- `term_discoverer.py` — `discover_terms()`: heuristic scan of string pairs to suggest candidate protected terms (proper nouns, identifiers).
- `batch_translate_dialog.py` — "Batch Translate Folder" dialog for bulk AI retranslation of a whole directory of string files.
- `macro_recorder.py` — `MacroRecorder` + `MacroStep`/`MacroStepType`: records and replays sequences of edit operations as named macros.
- `macro_dialog.py` — macro editor/runner dialog (`Ctrl+M`).

#### Quality assurance
- `quality_checker.py` — post-translation QA. Checks: missing/extra game tags (`<Alias=…>`, `[PLYR]`, `%s`, `<mag>`, `<dur>`, etc.), encoding failures, suspicious length ratios, Russian character leakage into Ukrainian output, English text leakage, untranslated strings, AI repetition artifacts, newline count mismatches, and more. `_fix_missing_tags()` handles sign-prefixed tags (`+<mag>`, `-<mag>`). Exports `AUTOFIX_CODES` and `RETRANSLATE_CODES` sets.
- `quality_dialog.py` — QC results dialog. Shows issues with retry-hint messages, per-row auto-fix and retranslation queue, and an "Auto-Retranslate Issues" batch action.
- `ai_qc_worker.py` — background worker that runs the `qcgemma4-st` Ollama model for AI-assisted quality checking.
- `pre_translation_estimator.py` — `PreTranslationEstimator`: scores 0–100 difficulty before any AI call. Weights learned from manual corrections (persisted as JSON).
- `consistency_checker.py` — `ConsistencyChecker`: finds same-source strings with different translations across the file.
- `consistency_dialog.py` — canonical-form picker with auto-replace (`Ctrl+Alt+K`).
- `string_type_detector.py` — `StringType` enum + `classify()`: categorizes strings (UI, dialogue, description, etc.) for display icons and filtering.
- `plugin_validator_dialog.py` — scans ESP/ESM for NPC dialogue camera bugs: missing Localized flag, stray DIAL/SCEN/INFO records, ONAM overrides, missing master dependencies.
- `gender_checker.py` — Ukrainian gender agreement checker: detects adjective/noun gender mismatches using a `NOUN_GENDER` dictionary. (`Ctrl+Alt+G`)
- `gender_dialog.py` — `GenderDialog`: displays gender mismatch results, inline fix suggestions.
- `register_checker.py` — ти/ви register consistency checker: finds mixed formal/informal address within a file. (`Ctrl+Alt+R`)
- `register_dialog.py` — `RegisterDialog`: displays register violations with context.
- `spell_checker.py` — multi-backend spell checker: Hunspell (via ctypes), spylls (pure Python), or CLI fallback. Used by the font checker and as a standalone QA step.
- `font_checker_dialog.py` — scans translations for characters absent from the game's Scaleform SWF font atlases.

#### File handling & dialogs
- `ba2_picker_dialog.py` — `BA2PickerDialog`: lets the user pick which `.strings` file to open when a BA2 archive contains multiple entries.
- `version_compare_dialog.py` — game-version diff UI; migrates unchanged translations; exports CSV/HTML reports; supports batch folder comparison.
- `diff_viewer.py` — side-by-side diff viewer (source-vs-translation or comparison-vs-current). Word-level or character-level granularity. Editable right pane with live diff update. HTML export.
- `translation_dialog.py` — inline translation editor dialog.
- `translation_editor_pane.py` — detachable dock widget providing a large comfortable editing area for the currently selected string.
- `advanced_search_dialog.py` — regex/fuzzy search across source and translation columns.
- `file_dialog_helper.py` — helpers for file-open/save dialogs (extension filtering, last-used directory tracking).
- `dialogue_tree_dialog.py` — visualizes Quest → Topic → Response hierarchy from an ESP/ESM file as an interactive tree.
- `vmad_dialog.py` — `VmadDialog`: front-end for VMAD script-property analysis (Translation menu → "Script Property Analysis (VMAD)…"). Has its own file picker so it works standalone or auto-loads the open ESP/ESM. Operates on the file path via `scan_vmad`/`apply_vmad_translations` (deliberately *not* merged into the main translatable table, to prevent accidental bulk-AI-translation of risky script identifiers). Risk-coloured 7-column table; locked rows are read-only unless "Allow editing locked properties (advanced)" is ticked; "Apply && Save" confirms, backs up, and byte-splices the edits.
- `visual_context_preview.py` — dock panel that renders the selected string inside a faithful recreation of the in-game UI widget.
- `detached_table_window.py` — pop-out table window for multi-monitor workflows; mirrors the main string table.
- `focus_overlay.py` — Zen/Focus Mode full-screen overlay showing one string at a time for distraction-free translation.
- `lore_rag_dialog.py` — Lore RAG management dialog (import, search, stats tabs).
- `lore_rag_manager.py` — `LoreRAGManager`: vector-style retrieval of lore snippets injected into AI translation prompts for contextual accuracy.
- `profile_editor_dialog.py` / `profile_assign_dialog.py` — translator profile management (per-locale style rules, author metadata).

#### NexusMods integration
- `nexusmods_client.py` — `NexusClient`: wraps NexusMods REST v1 API + GraphQL v2 API. Search uses `api.nexusmods.com/v2/graphql` (`nameStemmed: MATCHES`) as primary path, falling back to `search.nexusmods.com`. File listing filters out `OLD_VERSION` (catId=4) and `ARCHIVED` (catId=7) entries. `PLUGIN_EXTS`, `STRINGS_EXTS`, `CONTAINER_EXTS` constants shared across modules.
- `nexusmods_browser_dialog.py` — `NexusModsBrowserDialog`: card-grid search UI (3 columns, 215×121 thumbnails loaded async with local disk cache). Signals: `tm_ready`, `merge_requested`, `open_file_requested` (auto-opens downloaded `.esp`/`.esm`/`.esl` in the editor and closes dialog). "Download & Open in Editor" button enabled for plugin files and archives.
- `nexusmods_upload_dialog.py` — UI for the NexusMods upload flow.
- `nexusmods_uploader.py` — NexusMods v3 multipart upload client (6-step: presigned URLs → S3 → finalise → poll → attach metadata).

#### Audio / TTS
- `audio_preview_panel.py` — dock panel: plays original game audio and synthesizes TTS read-out of translations for timing comparison. Also drives **native Starfield voice playback** via `bethesda_strings/wwise_voice.VoiceIndex`: a FormID box + voice-type combo + "Load voice" button decode the original `.wem` clip (built in a background `_VoiceIndexWorker`, decoded in `_VoiceDecodeWorker`). In ESP/ESM mode the row's FormID auto-fills the box; in `.strings` mode the user enters one manually. The decoded clip is fed into the existing original-audio slot so it reuses the player, duration label, and timing bar. Note: the model row dict keys the id as `"id"` (not `"string_id"`). Owns the single shared `VoiceIndex` and exposes `resolve_speaker(form_id)` + the `speakerResolved(form_id, voice_types)` signal consumed by the Speaker panel.
- `tts_engine.py` — local TTS abstraction supporting eSpeak-NG (built-in), Piper (neural, external binary), and duration-estimate-only mode.

#### Speaker (NPC) map
- `speaker_map.py` — pure-Python (no Qt) `describe_voice_type(voice_type) -> SpeakerInfo`: a layered parser that turns a raw Wwise voice-type folder name (`npcfsarahmorgan`, `crimsonfleetfemale03`, `announcerf…`, `cr_…`, `robot…`, `playervoice…`, `sfbgs001_…`) into a friendly name/gender/faction/category. Curated `NAMED_NPCS` dict for major characters; `_FACTION_LABELS` for generics. `describe_voice_types()` dedups by display name. Layers: content-pack prefix strip → cut marker → test/creature/announcer/robot/player/unity/crowd → named `npc[fmx]<name>` → generic `<faction><gender><variant>` → fallback.
- `speaker_panel.py` — `SpeakerPanel(QDockWidget)` (objectName `SpeakerPanel`): thin renderer that shows who speaks the selected dialogue line (name, gender, faction, category, raw voice type, plus "also voiced by" for shared lines) + a manual FormID lookup box. Never builds the index itself — calls a resolver (`AudioPreviewPanel.resolve_speaker`) and renders the async `update_speaker(form_id, voice_types)` result. Tabified with the Audio panel; shares its visibility/toggle.

#### Infrastructure
- `keyboard_manager.py` — `KeyboardManager`: app-wide shortcut registration. `CommandPalette` (`Ctrl+K`): fuzzy-searchable command list. Vim-style navigation. `F7` → next untranslated, `Ctrl+Enter` → approve, `Ctrl+R` → reject.
- `command_palette.py` — `CommandPalette` widget (also accessible from `keyboard_manager`).
- `session_manager.py` — `SessionStore` / `WorkSession` / `SearchState`: named translation sessions with persistent search/filter state. (`Ctrl+Shift+N` new, `Ctrl+Shift+S` save).
- `session_dialog.py` — `SessionManagerDialog` + `NewSessionDialog`: UI for listing, creating, and switching sessions.
- `micro_animations.py` — `SmoothProgressBar` (animated progress), `FadeInMixin` (dialog fade-in), `fade_in_overlay()`, `start_card_pulse()`/`stop_card_pulse()` (welcome card heartbeat), `show_toast()` (transient bottom-right notifications).
- `audit_log.py` — `AuditLog`: append-only JSON-lines security log. Records file operations, translation batches, settings changes, encryption events — never logs actual string content. Rotates at 5 MB.
- `crash_recovery.py` — `CrashRecoveryManager`: periodic auto-save of translation progress (JSON snapshot in config dir). `CrashRecoveryDialog`: offered at startup if the previous session ended unexpectedly.
- `secret_store.py` — `SecretStore`: API key storage. Primary: system keyring (`keyring` library). Fallback: AES-256-GCM encrypted file, key derived from machine ID via PBKDF2-HMAC-SHA256. Used for Claude API key only; NexusMods key is stored in the JSON config with XOR+base64 obfuscation.
- `desktop_notify.py` — `send_notification()`: desktop notification helper (used for batch-complete events).
- `fuzzy_match.py` — Levenshtein distance, longest common substring/prefix, word-level distance, unicode control char utilities. Used by advanced search and consistency checker.
- `en_word_checker.py`, `ru_word_checker.py`, `uk_word_checker.py`, `de_word_checker.py`, `fr_word_checker.py`, `es_word_checker.py`, `pl_word_checker.py`, `it_word_checker.py`, `ptbr_word_checker.py` — word-list-based detectors for untranslated source-language text in output. All extend `_word_checker_base.py`. Word lists are in `data/`.

### Key design notes

- **Native file dialogs are disabled** via `Qt.ApplicationAttribute.AA_DontUseNativeDialogs` (set before `QApplication` is created). This is intentional — GTK/KDE portal dialogs deadlock the Qt event loop on Linux tiling WMs.
- **Translation backend selection**: `AppSettings.translation_backend` controls whether `OllamaWorker` or `ClaudeTranslationWorker` is instantiated. Both implement the same signal interface.
- **Translation pipeline per string**: `TermProtector.protect()` → model API call → `TermProtector.restore()` → `_restore_dropped_tags()` → `QualityChecker` → emit `translation_ready`.
- **Game tag restoration**: `_restore_dropped_tags()` in `ollama_worker.py` re-inserts Bethesda variable placeholders (`<mag>`, `<dur>`, `<area>`, `<relat>`, `<basename>`, `<repetitions>`, `<N.Prop>`) that the model drops. Uses fractional-position heuristics: `frac ≤ 0.15` → prepend, `frac ≥ 0.92` → append, `0.70 ≤ frac < 0.92` → insert before last token (handles `<dur>с` ordering), `else` → walk to next word boundary. Sign-prefixed tags (`+<mag>`, `-<mag>`) have the sign preserved.
- **Config location**: JSON config file is written to a platform-appropriate config directory (see `get_config_path()` in `app_settings.py`), not the project root.
- **API key obfuscation**: `nexusmods_api_key` is stored in the JSON config as `enc:<base64(xor(value, fixed_salt))>`. Legacy plaintext values (no `enc:` prefix) are still read correctly.
- **Claude API key**: stored exclusively via `SecretStore` (system keyring or AES-256-GCM file), never in the JSON config.
- **Protected terms file**: `protected_terms_starfield_hq.txt` in the project root is the default terms list for Starfield localization.
- **Drag and drop**: `_DropOverlay` + `_WelcomeWidget` on the main window; green/red feedback; extension validation; `dragMoveEvent` required to prevent forbidden cursor.
- **NexusMods search**: primary path is `api.nexusmods.com/v2/graphql` (GraphQL `nameStemmed: MATCHES`); fallback to `search.nexusmods.com` (Elasticsearch, may fail DNS on some networks).

## Scripts

- `scripts/compile_translations.sh` — compiles all `.ts` → `.qm` UI translation files.
- `scripts/extract_sharegpt_dataset.py` — extracts EN→UK Starfield string pairs as ShareGPT JSONL for fine-tuning a translation model. Output: `scripts/starfield_en_uk_sharegpt.jsonl`.
- `scripts/create_qc_dataset.py` — generates a QC training dataset (ShareGPT JSONL) by running `QualityChecker` on real EN→UK pairs and injecting synthetic bad examples for all 16 issue codes. Output: `scripts/qc_dataset_sharegpt.jsonl` (14,928 examples).
- `scripts/train_qc_model.py` — standalone ROCm-compatible QLoRA fine-tuning script for a Gemma 3 1B QC model (bypasses Unsloth Studio). Sets `HSA_ENABLE_SDMA=0`, `PYTORCH_HIP_ALLOC_CONF`, uses `attn_implementation="eager"`. Output: `models/qc_gemma3_1b/`.
- `scripts/apply_quality_fixes.py`, `scripts/apply_uk_translations.py` — batch fix/apply scripts for offline use.
- `scripts/extract_starfield_glossary.py` — builds `starfield_glossary.json` from string files.
- `scripts/build_uk_dict.py`, `scripts/download_en_dict.py` — word list builders for `data/`.

## Tests

```bash
pytest tests/
```

Test files: `test_encoding_detection.py` (28 tests), `test_glossary.py`, `test_pre_translation_estimator.py`, `test_quality_checker.py`, `test_term_protector_threading.py`, `test_diff_viewer.py`, `test_ollama_artifact_fixups.py`, `test_wwise_voice.py` (Wwise voice index — pure-function tests always run; the end-to-end BA2 decode test self-skips when the game/`vgmstream-cli` are absent), `test_speaker_map.py` (47 tests — voice-type → speaker parser, pure functions, no game files), `test_vmad_handler.py` (34 tests — VMAD parse/classify/byte-splice + ESP-level scan/apply round-trips including compressed records and records inside GRUPs; pure functions with synthetic plugin bytes, no game files).
