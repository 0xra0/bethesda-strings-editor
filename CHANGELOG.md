# Changelog

## [Unreleased]

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

[Unreleased]: https://github.com/0xra0/bethesda-strings-editor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/0xra0/bethesda-strings-editor/releases/tag/v0.1.0
