# Changelog

## [Unreleased]

---

## [0.2.5] — 2026-07-20

### Added
- **UI Width-Fit Simulator** (Translation → UI Width-Fit Simulator) — the font checker proves a glyph exists; this proves the label it spells *fits its box*. Cyrillic/German/Polish run 15–30 % longer than English and Scaleform widgets clip rather than shrink, so a correctly-spelled translation still ships as a truncated button. Rendered width is summed from the real per-glyph advance widths (SWF `FontAdvanceTable` / TTF `hmtx`) of the actual Starfield faces, markup and value placeholders resolved first; results are colour-coded worst-overflow-first with CSV export, plus a live "does it fit" indicator in the Visual Context Preview
- **Real widget bounds read from the game's own SWFs** — budgets are no longer guessed. `scan_game_ui` reads every `Interface/*.swf` and `*Interface*.ba2` (~250 SWFs, ~4600 fields, ~2560 clipping) and takes each field's authored width, margins, clip behaviour and font *class* straight from its `DefineEditText` record. Font size is marked DECLARED when the SWF states it and DERIVED when inferred from box height, and the two are never flattened — width scales linearly with size, so a 20 % size error is a 20 % wrong verdict
- **Large-font (accessibility) menus checked as the width worst case** — Starfield ships a `_lrg` build of most menus where the box usually stays the same size while the font grows (up to ×2.64), so a label can pass the standard menu and clip only for players using the large font. The tightest build is chosen per widget on `capacity_em`, and the dialog names the build it measured and why
- **Player-gender-aware translation** — English "you" carries no grammatical gender but many targets do, so the model was guessing one per line. Declare the player character's gender once (Settings → Translation Preferences, or the Prompt Editor) and every backend applies it consistently; Translation → "Find Player-Referring Strings" selects the gender-sensitive rows. A pre-batch nudge warns before any AI call — and only when it matters: gender unset, target inflects, and the batch actually contains player-referring lines
- **Official-TM miner** (Translation → Mine Official Terminology) — Bethesda ships every official language for a plugin side by side on identical string IDs, so aligning English against an official target yields their canonical rendering of every weapon/faction/UI/quest term with **zero AI calls**. Verified on real Starfield data: EN→DE mined 190,367 TM entries + 17,815 glossary terms in 27 s. Imported translations only fill pending rows, never clobbering in-progress work
- **Claude Code CLI backend** — a subscription-backed drop-in for the metered Anthropic API for translation, chat and review. `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` are stripped from the child process so it always uses subscription auth and never silently bills the API; real per-call token usage is accumulated and reported after each batch
- **Translation Prompt Editor** (Translation → Translation Prompt Editor) — customize the system prompt without editing code: six structured tuning dials (style, formality, vocabulary, grammar, expression/localization, rigor), a per-target-language override for the style/register rule, and a global addendum, with a live preview of the assembled prompt. The token-preservation rules stay fixed so placeholders can't be broken. All backends honour it
- **Korean particle (조사) checker** — allomorphs are computed from the 받침 arithmetic, never guessed. Flags single-form particles after value placeholders (always a latent bug — the substituted noun's 받침 is unknowable at translation time) and particle/stem mismatches, restricted to the provably sound subset. Measured at 0 false positives over 5,353 real Korean strings; both codes are auto-fixable, and the same rule is taught to the model
- **Translation-memory visibility** — a searchable TM browser dialog, a status-bar "TM: N" indicator, and a JSON snapshot so a loaded TM survives across sessions
- **Apply Translation to All Identical Originals** (Ctrl+Alt+D), and Delete to clear the selected rows' translation

### Changed
- **Word-frequency dictionaries are preloaded per language pair**, not wholesale — all nine lists loaded on every launch regardless of the session's languages, ~330 MB resident for a 35 MB Russian list nobody asked for. Measured 331 MB → 44 MB for an EN→KO session
- **Fuzzy TM lookup is ~10× faster with byte-identical results** — `best_fuzzy_match` was a linear scan costing ~90 ms per lookup against a six-figure mined TM, on the path every uncached string takes. A sound pre-filter narrows the pool first, dropping only candidates the scoring function would have rejected anyway
- **Length-ratio quality checks are now width-aware** — the thresholds are character counts standing in for display widths, calibrated on scripts that draw one narrow glyph per character. Korean and zh-Hans do neither, so the checks were useless in both directions; ratios are converted to Latin-equivalent width units first, leaving narrow-script targets untouched
- **ESP extraction** gains GPOF/GPOG settings-menu titles, a resource-path safety filter (asset paths masquerading as text are no longer translated into a broken mod), and an anti-hallucination context note on quest titles
- **Claude API calls retry 429/5xx with exponential backoff**, honouring `Retry-After`; non-retriable errors still propagate immediately
- Settings' GPU tuning help moved into the rotating tips, and all 61 tips are now translated in every one of the 7 UI locales; UI translations re-synced (1675 strings, 0 unfinished per locale)
- The release pipeline is rehearsable — a `workflow_dispatch` dry run builds, signs and creates a **draft** release to prove the publish step works without tagging, notifying anyone, or touching NexusMods; CI actions moved to their Node 24 majors

### Fixed
- **A source-keyed translation memory reported itself empty** — `__len__`/`__bool__` read only the ID index, and five separate gates test exactly that value (worker attachment, the lookup gate in both backends, the save-on-exit snapshot, the browser, the status-bar indicator). Everything the Official-TM miner and TMX import produce is source-keyed, so the TM half of "Mine Official Terminology" was dead on arrival
- **The Claude backend failed every string in a batch the moment any TM was loaded** — it called a `TranslationMemory` method that does not exist, outside a try/except. It also consulted string IDs only, leaving source-keyed memories unusable on that backend; it now runs the same id → source → fuzzy chain as the Ollama path
- **The chat panel's "Use as Translation" could never work** — the reply handler rewrites every code fence into a `<pre>` block before display, so the rendered text it scanned held no backticks, while the enable check ran against the raw reply and lit the button up anyway. Extraction now reads the stored history
- **Two crashes in the gender-agreement dialog**, both on the normal path: it touched a table that is never built when there are no mismatches, and connected a signal `QTableWidget` does not have when there were any
- **Hard abort on startup with the animated video background** — PySide6's bundled FFmpeg needs a VA-API symbol the shipped stub library doesn't export, producing an uncatchable symbol lookup error. The real system libva is preloaded first, keeping hardware decoding working (no-op off Linux or without libva)
- **Two unrelated fields of one menu could be paired as large-font variants** — a field name is commonly reused for several boxes of the same width (`text_tf` appears 146×), so `chargenmenu › text_tf` (844 px) was measured against a 126 px title in the same menu, a six-character budget that calls every translation an overflow. Ambiguous pairings are now refused, and the refusal is reported rather than passing as silence
- **Book and note reviews were truncated mid-sentence** by a flat token budget, and the half-finished text then rode into the next suggestion via the chat history; the budget now scales with the text under review
- **Protected terms leaked as placeholder tokens** into saved translations on the Claude backend, whose cache was also permanently disabled and whose retranslations returned the same stale string; term protection was silently a no-op in the batch-folder dialog
- **Crash recovery offered to restore work already saved** — the snapshot was only cleared on a clean exit
- **Group-box titles were struck through by their own border** in every built-in theme and dialog; the title band is now reserved in em units so it tracks the user's font size
- Fuzzy TM matches whose numeric runs differ from the source (`28LY` vs `30LY`) are rejected; a real `fontconfig.txt` had every mapping silently dropped by a regex that didn't allow Starfield's own `=` syntax; five real Korean particle defects in our own UI translation

---

## [0.2.4] — 2026-07-05

### Added
- **Claude MCP connector** — the Claude AI Assistant chat panel can now call tools on remote MCP servers through the Messages API MCP connector (Anthropic makes the connection and runs the tools server-side); servers are configured in Settings → Claude MCP Servers (name / URL / optional auth token, XOR-obfuscated on disk), multi-round tool turns resume automatically, and each tool invocation is surfaced live in the chat panel. Chat-only — the deterministic translation pipeline never issues server-side tool calls
- **Translation-folder validator** (Translation → Validate Translation Folder) — scans a translated Strings folder against the game Data folder (loose files **and** `.ba2` archives) and flags the files/IDs that will show an in-game `<Error: Unknown lstring ID …>` before you launch the game
- **Companion Strings viewer** (Translation → Companion Strings) — read-only viewer for a loaded `.strings`/`.dlstrings`/`.ilstrings` triplet, with file-type and text/ID filters
- **NexusMods SSO sign-in** — replaces pasted personal API keys with the browser-based Single Sign-On flow required by the NexusMods API Acceptable Use Policy; the per-user SSO key is stored obfuscated and used for search, download, and upload (the SSO app slug is configurable and ships pinned to the registered, staff-approved value)

### Changed
- **ти/ви register consistency** is now enforced inline by the translation system prompt (shared by both the Ollama and Claude backends) instead of a separate post-hoc checker — the model keeps informal ти / formal ви consistent per speaker and never mixes the two
- **"Protect English text" (RU→UK)** now transliterates bare capitalised proper nouns (character/planet names) into the target script instead of leaving them English; exact spellings can be pinned deterministically via the glossary (no model call), which beats even ALL-CAPS protection
- **`.strings`/`.dlstrings`/`.ilstrings` companions load as a read-only reference** and are never merged into the saved file — the three file types have independent string-ID spaces, so merging them contaminated the output with foreign IDs
- Korean added to the language dropdown; server-side Ollama GPU environment variables documented in the Settings help; a real local-GGUF install path documented for the translation model
- Build hardening to reduce antivirus false positives (source bootloader, embedded version-info, SignPath Foundation code-signing links)

### Fixed
- **MamayLM (RU→UK) output healing** — strips stray Ukrainian stress/accent marks, heals Russian-word leakage into Ukrainian output, and repairs EN-number placeholder plus appended-label artifacts
- **Triplet-merge contamination** — companion files are no longer deduped into one ID space, eliminating `<Error: Unknown lstring ID …>` from foreign IDs
- Numerous RU→UK quality-checker false positives — coverage, untranslated, and leak checks; malformed `\ n` / `\н` escapes and `[TK:]` hallucinations; glued-URL `MISSING_URL`/`EXTRA_TAG`; and valid short RU→UK output being blanked by `_clean_translation`
- Crashes formatting string-keyed IDs as hex in Starfield interface TXT mode; a redundant "Translate Starfield Interface TXT" menu action removed; quality checker tuned for interface TXT `{0}`-brace placeholders
- Segfault when the Settings model poll touched a freed fetcher thread; GPU stats now polled off the UI thread (no Windows console flashing)
- Unreadable white first-run "Quick-start tips" dialog on themed UI
- `QThread: Destroyed while thread is still running` crash on SSO sign-in

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

[0.2.4]: https://github.com/0xra0/bethesda-strings-editor/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/0xra0/bethesda-strings-editor/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/0xra0/bethesda-strings-editor/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/0xra0/bethesda-strings-editor/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/0xra0/bethesda-strings-editor/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/0xra0/bethesda-strings-editor/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/0xra0/bethesda-strings-editor/releases/tag/v0.1.0
