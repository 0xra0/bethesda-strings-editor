# Translation Guide

Thank you for helping translate Bethesda Strings Editor!

## Status

Every locale currently carries all **1,735** UI strings — none is a stub. What
differs is who has *read* them.

| Language  | Code    | Strings | Status | Maintained by |
|-----------|---------|---------|--------|---------------|
| English   | `en`    | 1,735 (source) | ✅ Source of truth | [@0xra0](https://github.com/0xra0) |
| Ukrainian | `uk_UA` | 1,735 | ✅ Complete, proofread | [@0xra0](https://github.com/0xra0) |
| German    | `de_DE` | 1,735 | 🤖 Machine-assisted · review welcome | community |
| Spanish   | `es_ES` | 1,735 | 🤖 Machine-assisted · review welcome | community |
| French    | `fr_FR` | 1,735 | 🤖 Machine-assisted · review welcome | community |
| Korean    | `ko_KR` | 1,735 | 🤖 Machine-assisted · review welcome | community |
| Polish    | `pl_PL` | 1,735 | 🤖 Machine-assisted · review welcome | community |
| Czech     | `cs_CZ` | 1,735 | 🤖 Machine-assisted · review welcome | community |

**@0xra0 maintains English and Ukrainian only.** Everything marked *community*
is exactly that: the author does not speak those languages and cannot judge a
correction, so review is taken on the contributor's word and merged. If a
locale reads badly to you, you are the only person who can tell — please open a
PR rather than assuming someone else is on it.

🤖 = every string is filled in (machine-assisted) but not proofread by a native
speaker. These are the most valuable to contribute to: you are polishing, not
starting from scratch.

Want to add a new language? Open an issue or a PR — see
[Adding a new language](#adding-a-new-language).

---

## Workflow

There is no Weblate or other hosted platform: translations are `.ts` files in
this repository, edited and sent as pull requests.

### 1. Prerequisites

```bash
pip install PySide6          # provides pyside6-linguist, pyside6-lupdate, pyside6-lrelease
```

Qt's own `lrelease` from a distro package works too (`qt6-l10n-tools` on
Debian/Ubuntu) — `scripts/compile_translations.sh` looks for either.

### 2. Edit the .ts file

```bash
pyside6-linguist gui/translations/de_DE.ts
```

Qt Linguist shows each source string alongside a field for the translation.
Green check = approved; yellow = unfinished. `.ts` files are plain XML, so a
text editor works as well if you prefer.

### 3. Compile and test

```bash
./scripts/compile_translations.sh
python main.py
# Settings → Appearance → Interface Language, select your language, restart
```

### 4. Open a pull request

Commit **only** the `.ts` file — `.qm` binaries are gitignored:

```bash
git add gui/translations/de_DE.ts
git commit -m "Review German translation (de_DE)"
```

---

## Translation rules

### Preserve placeholders

Qt uses `%1`, `%2`, … for runtime values. Never translate or remove them:

| Source | ✅ Correct | ❌ Wrong |
|--------|-----------|---------|
| `Loaded %1 strings` | `%1 Strings geladen` | `Geladen Strings` |

### Preserve game tags

Strings marked **Context: game content** may contain Bethesda markup — do
**not** translate these in the UI itself; they pass through to the AI.

### Keep accelerators

Menu items use `&` as an accelerator prefix. Keep it, but move it to a letter
that exists in your language:

| Source | ✅ | ❌ |
|--------|----|----|
| `&File` | `&Datei` | `Datei` |

### Preserve formatting

Match the capitalization style of the source string:
- Title Case labels → Title Case in translation
- Sentence case descriptions → Sentence case

### Untranslatable strings

Leave these **empty** (they fall back to English):
- Log messages (not shown in the UI)
- Internal identifiers / enum names
- Error codes like `MISSING_TAGS`, `RUSSIAN_LEAK`

---

## Adding a new language

1. Copy an existing locale as a starting template:

```bash
cp gui/translations/uk_UA.ts gui/translations/ja_JP.ts
```

2. Edit the `language` attribute at the top of the new file:

```xml
<TS version="2.1" language="ja_JP">
```

3. Clear all `<translation>` elements (set them to `type="unfinished"`).

4. Add the locale to `_UI_LANGUAGES` in `gui/settings_dialog.py` — the tuple is
   `(code, English name, native name, proofread)`, and that last flag is what
   puts the ✓ next to a language in the picker. Leave it `False` until a native
   speaker has read the whole file:

```python
("ja_JP", "Japanese", "日本語", False),
```

5. Open a PR — even partial translations (≥ 30 %) are welcome.

---

## File structure

```
gui/translations/
  uk_UA.ts    ← Ukrainian (proofread; the usual template for new languages)
  de_DE.ts  es_ES.ts  fr_FR.ts  ko_KR.ts  pl_PL.ts  cs_CZ.ts
  *.qm        ← compiled binaries (NOT committed — gitignored)
```

`.ts` files are XML — human-readable, diff-friendly, version-control friendly.
**Commit these.**

`.qm` files are the compiled binaries Qt loads at runtime. They are gitignored,
built locally by `scripts/compile_translations.sh`, and rebuilt from the `.ts`
sources by the release workflow (`.github/workflows/release.yml`) before
PyInstaller bundles them. **Do not commit them** — and note that the PR checks
do not compile them, so run the script yourself before testing a change.
