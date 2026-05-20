# Changelog

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
- Arch Linux AUR package: `bethesda-strings-editor-bin`
- Sphinx documentation with API reference, format specification, and architecture overview, hosted on GitHub Pages
- git-cliff structured changelog from free-form commit messages

[0.1.1]: https://github.com/0xra0/bethesda-strings-editor/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/0xra0/bethesda-strings-editor/releases/tag/v0.1.0
