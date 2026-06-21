# Changelog

## [Unreleased]

---

## [0.2.3] — 2026-06-21

### Added
- **Cross-platform support (Windows & macOS)** — native Explorer/Finder file dialogs on Windows/macOS (the GTK/portal deadlock workaround is now Linux-only); config stored in the OS-native location (`%APPDATA%` on Windows, `~/Library/Application Support` on macOS, `$XDG_CONFIG_HOME`/`~/.config` on Linux) with automatic migration from the legacy path; owner-only config permissions enforced on Windows via `icacls` as well as POSIX `chmod`; per-platform audio playback (macOS `afplay`, Windows `ffplay`/PowerShell WAV player, Linux `paplay`/`ffplay`/`aplay`); machine-id derivation, temp paths, and subprocess console-window suppression all made portable
- **ESP/ESM Mod Update Migration** (Translation → Mod Update Migration) — xTranslator-style tool that diffs an old and new version of a plugin keyed on `(FormID, record, field, occurrence)` and carries existing translations forward to the updated plugin; risk-coloured 7-column diff with changed-only filter and CSV/HTML export; only fills pending/empty rows so in-progress work is never clobbered
- **VMAD script-property analysis** (Translation → Script Property Analysis) — pure-Python Papyrus VMAD parser/classifier with safe byte-splice editing; each script-property string is tagged translatable / review / locked (resource paths, identifiers, and event names locked by default); works on both localized and non-localized plugins, recomputes record + GRUP sizes, re-compresses compressed records, and writes a `.bak` before saving
- **NPC & Speaker Mapping panel** — shows who speaks the selected dialogue line (name, gender, faction, category, raw voice type, and "also voiced by" for shared lines), resolved from the Wwise voice-type folder name via a layered parser with a curated named-NPC table
- **Native Starfield voice playback** — decodes the original Wwise `.wem` voice clip for a dialogue FormID (via `vgmstream-cli`) straight out of the `*Voices*.ba2` archives and plays it through the audio panel for timing comparison
- **Ollama force-stop** — frees a wedged GPU by restarting the Ollama service without leaving the app; on Linux a privileged restart uses the app's own Qt-themed `sudo -S` password dialog (askpass/pkexec fallback), on Windows it stops the service via `taskkill` with no console flash
- **Ollama model auto-detection** — the model dropdown in Settings loads installed models automatically and keeps refreshing while the window is open, so a model pulled with `ollama pull` appears without clicking Refresh
- **Automatic update checker** — checks the GitHub releases API on startup and offers to download a newer build (toggle in Settings)
- **"What's New" panel** — recent GitHub release notes are fetched and rendered on the welcome screen
- **NexusMods Translation Browser** — search NexusMods for existing translation mods (GraphQL v2 search with Elasticsearch fallback), browse their files in a card grid, and import `.strings`/`.dlstrings`/`.ilstrings` directly as a Translation Memory or merge them into the current file; "Download & Open in Editor" auto-opens downloaded `.esp`/`.esm`/`.esl`; archives are auto-extracted; free-account downloads handled via browser cookies (`curl-cffi`)
- **NexusMods upload** — v3 multipart upload flow (presigned URLs → S3 → finalise → poll → attach metadata) with a dedicated upload dialog
- **Visual Context Preview** (View → Visual Context Preview, Ctrl+Shift+P) — renders the current string inside a faithful recreation of the Bethesda Starfield UI box using the actual game fonts; auto-detects context (Dialogue, Quest, Book, Note, Terminal, UI), shows box dimensions on the 1280×720 Scaleform canvas, and flags overflow when a translation is too long
- **Named Translation Sessions** (Ctrl+Shift+N new, Ctrl+Shift+S save) — persistent sessions with saved search/filter state
- **Vim-style Macro Recording** (Ctrl+M) — record and replay sequences of edit operations as named macros
- **Ukrainian gender-agreement checker** (Ctrl+Alt+G) and **ти/ви register-consistency checker** (Ctrl+Alt+R)
- **Starfield interface TXT support** — translate `translate_en.txt` / `translate_ru.txt` interface string files
- **AI post-translation self-review** — automatically fixes critical issues (skips purely visual ones) after each translation
- **Obfuscated in-game code locking** — deliberately-garbled codes (encrypted notes, passwords, scrambled terminal text) are detected and locked through translation
- **8 new themes** — Gruvbox, Tokyo Night, Monokai, One Dark, Solarized Light, Sepia, Starfield, and Starfield Terminal
- **GPU monitor** — status-bar widget showing GPU utilisation, VRAM, and temperature (AMD via Linux sysfs, NVIDIA via `nvidia-smi` on all platforms; auto-hides if no GPU)
- **Bundled Hunspell dictionaries** — `scripts/fetch_dictionaries.py` populates `dicts/` so Windows/macOS builds ship working spell-check
- **Korean (ko_KR) UI translation** and Korean source-language leak detection
- **Restore dropped Bethesda game tags** — re-inserts `<mag>`, `<dur>`, `<area>`, etc. that the model drops, using fractional-position heuristics
- **Auto-Fix All** — one-click batch application of all mechanically correctable QC issues
- **Per-code hide filter** in the QC dialog
- **UI Constraint Enforcer** — flags translations more than 40% longer than the English original
- **Custom background / wallpaper support** with theme integration
- **Full About dialog**, colour-coded `[INFO]`/`[WARN]`/`[ERROR]` logging, a redesigned app icon, and core I/O / fuzzy-match / cache benchmarks

### Changed
- API keys obfuscated in the JSON config (XOR + base64); Claude key remains in the system keyring / AES-256-GCM store only
- Protected-terms list trimmed to token names — Starfield in-game terms are translatable
- Redesigned NexusMods page description and header banner
- GPG release signing + SHA256 verification added to the release pipeline

### Removed
- Weblate community-translation integration
- AUR packaging (desktop integration relocated to `packaging/`)
- `CONDITIONAL_BLOCKS` QC check and non-existent Starfield bracket/name tokens

### Fixed
- Numerous mamaylm batch-translation timeout and GPU-wedge issues (stall watchdog, queue-depth-aware timeouts, single-stream / pinned-context, wedge breaker)
- White/unthemed welcome screen and "What's New" panel (offscreen `QGraphicsEffect` render + transparent viewport)
- Windows tray icon and post-translation notifications
- Shutdown hang and `Ctrl+C`/IOT crashes (drain executor threads before terminate; catch `BaseException` in the shutdown path; `gpu_monitor` polling)
- Many QC false positives — printf format specifiers, `% for`/`% chance`, brackets, guillemets, sentence/newline counts, RU→UK identical short words
- Several PyInstaller bundle gaps (log path, missing data files, theme dir, `LD_LIBRARY_PATH` pollution)

---

## [0.2.2] — 2026-06-11

### Added
- **Lore RAG** — local SQLite FTS5 lore database (UESP downloader built-in); relevant faction/location/character articles are retrieved per string and injected into the AI prompt so terminology stays accurate
- **Font & Glyph Checker** — parses Scaleform SWF font atlases and TTF/OTF cmap tables; flags translation characters that will render as squares in-game and suggests auto-fixable substitutes (em-dash → `-`, NBSP → space, curly quotes, etc.)
- **Character Persona Profiling** — per-NPC voice system; tag any string or quest with a built-in profile (Freestar Ranger, SysDef Officer, Crimson Fleet Pirate, House Va'ruun Zealot, UC Civilian, Robot/Automaton, Narrator) or create custom ones; each profile overrides the AI system prompt and temperature at translation time
- **Audio / TTS Preview** — dockable panel (View → Audio Preview, Ctrl+Shift+A) with eSpeak-NG and Piper backends; synthesizes a TTS read-out of the translation so timing can be compared against the original audio; colour-coded timing bar (green ≤ 110 %, orange ≤ 130 %, red > 130 %); auto-locates original game audio files by form ID
- **Zen / Focus Mode** — full-screen distraction-free editor (View → Zen / Focus Mode, F11); GitHub-dark palette with large source and translation panels, pending-string counter, per-string status badge; Ctrl+Enter approve, F7 next untranslated, Esc exit
- **Multi-Monitor / Detached Panes** — Translation Editor dock (Ctrl+Shift+E) provides a large editing area that floats to any monitor; Pop Out String List (Ctrl+Shift+L) opens a second table window sharing the same model and selection model so clicking in either window syncs both; all dock positions persisted via `QMainWindow.saveState()` across sessions; second monitor auto-detected for initial placement
- **Dialogue Tree Visualizer** — interactive quest → topic → response node graph (Translation → Dialogue Tree); click any node to jump to that string in the table
- **Claude API pre-flight cost estimator** — shows token count and estimated cost before starting a batch translation
- **Weblate community translation sync** — push/pull strings to a self-hosted or hosted Weblate instance from the File menu
- **Error-code filter in QC dialog** — filter quality issues by code (MISSING_TAGS, NEWLINE_COUNT_MISMATCH, etc.)
- **Find & Replace in Advanced Search** — batch regex replace across all translation cells
- **Skip-string-types setting** — exclude Book, Note, or other string categories from AI batch translation
- **Protect named entities** — opt-in setting to extend term protection to faction/ship/character names inferred from the loaded file
- **AI Quality Check (qcgemma4-st)** — fine-tuned Gemma 4 E4B model with 16 issue codes and chain-of-thought reasoning; AUTOFIX / RETRANSLATE action codes; Modelfile and 14,928-example ShareGPT training dataset included
- **Spell-check QC** — Hunspell-backed `SPELL_ERROR` check for all supported target languages
- **mamaylm model config** — author-recommended sampling parameters registered in `MODEL_CONFIGS`

### Fixed
- `SENTENCE_COUNT_MISMATCH` false positive on strings containing `%.2f` / `%+.3g` and other printf format specifiers — the decimal point inside specifiers was counted as a sentence terminator; format specs are now stripped before the sentence count is measured
- Tag names forgotten by the AI across a paragraph boundary — reformulated the tag-protection rule in the system prompt
- Newline structure corrupted when the model emitted `[[STRUCT_BREAK_*]]` tokens in the wrong order — restoration now validates token sequence before applying
- Line count mismatch in multi-line list strings — paragraph splitter now preserves trailing blank lines
- `SignalOverflow` crash when a translated FormID > 0x7FFFFFFF was emitted via `Signal(int)` — changed to `Signal(int, str, object)`
- Encoding detection incorrectly classified English UTF-8 files as Windows-1252
- `Ctrl+Shift+A` shortcut conflict between two actions
- Three chunked-translation bugs causing truncation and lost paragraphs in book strings
- `[[STRUCT_BREAK_*]]` tokens leaking verbatim into translated output
- Leaked/garbled `[[...]]` tokens after restore — comprehensive post-restore cleanup pass added
- English bracket spans `[like this]` in book strings not translated
- `%` format specifiers leaking through `_clean_translation`
- Multiple model artifact leaks in `_clean_translation` (thinking-model `<think>` blocks, repeated system-prompt echoes)

---

## [0.2.1] — 2026-06-03

### Added
- **Claude AI backend** — drop-in replacement for Ollama using the Anthropic API; model selector includes Haiku 4.5 (default), Sonnet 4.6, and Opus 4.7; prompt caching and streaming supported; selected via Settings → Backend
- **Claude AI Assistant dock** — dockable chat panel (Claude AI menu, Ctrl+Shift+C) for discussing the current string and applying Claude's suggested translation with one click
- **Claude AI quality review** — ask Claude to review the selected string's translation for issues (Ctrl+Shift+R)
- **Batch Translate Folder** — translate a whole directory of string files in one operation (Translation menu)
- **Content-type icons** — Phosphor icon set in the string table Kind column identifies dialogue, book, UI, item description, and other string types at a glance; theme-aware (light/dark variants)
- **NexusMods upload** — v3 multipart upload client with presigned S3 URLs, 6-step flow; File → Upload to NexusMods; release workflow uploads automatically on tag push
- **Gemma 4 4B IT Modelfile** — registered in `MODEL_CONFIGS` alongside the 27B model
- **QC training dataset generator** — `scripts/create_qc_dataset.py` produces a 14,928-example ShareGPT JSONL from real EN→UK pairs with synthetic bad examples for all 16 issue codes
- Icons added to all menu actions; main toolbar extended with glossary and AI assistant buttons

### Changed
- Ukrainian UI translation completed (844/844 strings); German, French, Spanish, Polish, Czech translations also complete
- AT-SPI accessibility bus warning suppressed on startup on headless/Wayland systems

### Fixed
- Encoding detection: English UTF-8 string files were misclassified as Windows-1252
- `Ctrl+Shift+A` shortcut assigned to two separate actions simultaneously
- Ruff lint errors: unused imports and local re-imports removed

---

## [0.2.0] — 2026-05-27

### Added
- **BA2 archive support** — read and write Starfield v2 and Fallout 4 v1 BA2 archives (GNRL type, zlib-compressed); picker dialog for multi-entry archives; integrated into file open/save
- **All 9 official Starfield languages** — English, German, Spanish, French, Italian, Japanese, Polish, Portuguese (Brazilian), and Chinese (Simplified) added to source/target selectors alongside Russian and Ukrainian; combo boxes now store locale codes (`en`, `de`, `es`, `fr`, `it`, `ja`, `pl`, `ptbr`, `zhhans`, `ru`, `uk`)
- **Language-specific Ollama prompts** — dedicated system prompt for every source→target pair with register rules, script conventions (Japanese polite forms, Chinese simplified terminology, Ukrainian-not-Russian vocabulary), and native translation examples; fully data-driven via module-level tables
- **Newline and whitespace structure restoration** — when the model drops `[[STRUCT_BREAK_*]]` tokens, output is re-split proportionally by character-count ratio and per-line leading whitespace is restored from the original; handles single `\n`, double `\n\n`, mixed patterns, and trailing newlines

### Changed
- Source and target language settings now store locale codes instead of display names (config version 19 → 20; existing configs migrated automatically)
- `EncodingConverter.ENCODING_PAIRS` and `get_encodings_for_locale()` accept Starfield locale codes (`de`, `ptbr`, `zhhans`, …) in addition to full display names and BCP-47 tags

### Fixed
- English→Ukrainian translation was silently skipped when source and target locale codes compared unequal due to mismatched format (display name vs. code)
- Stray placeholder tokens leaked into translated output when the model reproduced them verbatim; excess tokens are now stripped before restoration
- Mixed-script repair (`_fix_mixed_script`) incorrectly triggered on non-Cyrillic target languages; now gated on Cyrillic-script targets only
- Quality checker tag-detection patterns now correctly identify `<Alias=…>`, `[PLYR]`, and `%s` variants regardless of surrounding whitespace
- App icon updated to reflect multi-language scope (was "Ru → Ук" only)

---

## [0.1.1] — 2026-05-20

### Added
- **Security & Encryption**
  - AES-256-GCM at-rest encryption for the translation cache — opt-in via Settings → Security
  - `SecretStore` — system keyring (via `keyring` library) with PBKDF2-HMAC-SHA256 machine-key fallback for environments without a keyring daemon
  - Security audit log — append-only JSON-lines file recording file open/save, translation batches, and settings changes; no translated text is ever written; 5 MB rotation
  - `cryptography>=43.0` added to requirements; `keyring>=25.0` optional dependency
- **Accessibility**
  - "High Contrast" theme — WCAG AAA black/white/cyan palette with yellow focus rings (follows Windows High Contrast convention)
  - Visible focus indicators on all interactive widgets (buttons, toolbuttons, checkboxes, tabs, list/table views) via QSS focus mixin applied to every theme
  - `Qt.AccessibleTextRole` in `StringTableModel` — screen readers (AT-SPI2 on Linux, MSAA/UIA on Windows) now read "Translated — quality error" instead of "⚠✗"
  - `setAccessibleName()` on font-size spinner and color-blind checkbox in Settings
  - Font size control in Settings → Appearance (0 = OS default, 8–24 pt); applied as `QApplication.setFont()` at startup so every widget scales
  - Color-blind mode toggle — replaces green/red status colors with blue/orange for deuteranopia safety; symbols (✓/⚠/✗) always distinguish states regardless of color; takes effect immediately without restart
- Multi-language UI support: German (`de_DE`), Spanish (`es_ES`), French (`fr_FR`), Polish (`pl_PL`), Czech (`cs_CZ`) skeleton `.ts` files ready for community translation
- RTL layout support — Arabic, Hebrew, Farsi, Urdu locales automatically mirror the UI via `Qt.LayoutDirection.RightToLeft`
- Language selector in Settings shows all available languages with native names; marks complete translations with ✓
- Restart-required notice appears inline when the UI language is changed
- `TRANSLATING.md` — contributor guide covering Qt Linguist workflow, placeholder rules, and adding new languages
- `.weblate/component.yml` — Weblate configuration for community-managed translations
- `scripts/compile_translations.sh` now compiles all `*.ts` files in `gui/translations/` instead of only `uk_UA.ts`
- PyInstaller spec bundles all compiled `*.qm` files automatically

### Changed
- `ui_language` setting now stores BCP-47 locale codes (`"uk_UA"`, `"en"`) instead of English display names; existing configs are migrated automatically (config version 16 → 17)
- Translation loader in `main.py` is now generic — loads `gui/translations/{locale}.qm` for any configured locale

### Fixed
- Glossary editor froze on open when the glossary contained many entries — the search index was being rebuilt once per entry during cloning (O(N²)). Now rebuilt once after all entries are inserted.

---

## [0.1.0] — 2026-05-20

Initial public release.

### Added

**Translation**
- Parallel AI translation via [Ollama](https://ollama.com) with configurable concurrency (default 10 workers)
- Translation memory — known strings are looked up before calling the model and never retranslated
- SHA-256 keyed translation cache persisted across sessions (up to 50,000 entries)
- Term protector — 8,000+ Starfield-specific proper nouns, locations, and UI labels replaced with placeholder tokens before AI inference and restored afterward
- Glossary system with CSV / TBX / JSON import-export, in-app editor, term suggestion dock, and automatic injection into AI prompts
- Pre-translation difficulty estimator (score 0–100) shown in the Status column

**File support**
- `.strings` (null-terminated), `.dlstrings` / `.ilstrings` (4-byte length-prefixed)
- ESP/ESM non-localized plugin files — extracts and writes back translatable fields
- xTranslator SST XML import/export (matches by `sID` hex, falls back to source text)
- Auto-detection of file encoding: UTF-8 BOM → valid UTF-8 → CP1251 heuristic → CP1252 fallback

**Quality checker**
- `MISSING_TAGS` / `EXTRA_TAGS` — game markup (`<Alias=…>`, `[PLYR]`, `%s`) present/absent check
- `NEWLINE_COUNT_MISMATCH` — line break count difference between original and translation
- `TRANSLATION_TRUNCATED` — normalized prefix match detects AI stopping mid-sentence
- `SUSPICIOUSLY_SHORT` — output length less than 20 % of source
- `ENCODING_ERROR` — non-target-language characters
- `RUSSIAN_LEAK` — Russian-only characters (`ё`, `ъ`, `ы`, `э`) in Ukrainian output
- `GLOSSARY_MISMATCH` — term translated inconsistently against the active glossary
- One-click auto-fix for fixable issues; one-click retranslate for AI issues
- Quality report dialog with batch auto-fix and auto-retranslate queue

**Review workflow**
- Consistency checker — finds identical source strings with differing translations, canonical-form picker, and batch replace
- Version diff — compare two game versions, migrate unchanged translations, CSV/HTML export
- Diff viewer with word-level and character-level highlighting

**UI**
- Dark / light / high-contrast / Catppuccin built-in themes plus custom `.qss` file support
- Ukrainian interface localization (`.ts` / `.qm` via Qt Linguist)
- Vim-style keyboard navigation, command palette (Ctrl+K), customizable shortcut editor
- Drag-and-drop file open with extension validation
- Status bar with live progress, translated count, and ETA during AI batches
- Clipboard shortcuts: Ctrl+C/V copy-paste original ↔ translation; Shift+C/V for full rows
- Desktop notifications on batch completion

**Infrastructure**
- PyInstaller onedir standalone builds for Linux x64 and Windows x64
- GitHub Actions: build + release on tag push, test CI (Linux + Windows), lint (ruff + Pyright)
- Sphinx documentation with API reference, format specification, and architecture overview, hosted on GitHub Pages
- git-cliff structured changelog from free-form commit messages

[0.2.3]: https://github.com/0xra0/bethesda-strings-editor/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/0xra0/bethesda-strings-editor/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/0xra0/bethesda-strings-editor/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/0xra0/bethesda-strings-editor/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/0xra0/bethesda-strings-editor/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/0xra0/bethesda-strings-editor/releases/tag/v0.1.0
