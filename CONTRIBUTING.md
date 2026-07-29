# Contributing

## Development setup

```bash
git clone https://github.com/0xra0/bethesda-strings-editor
cd bethesda-strings-editor
pip install -r requirements.txt
pip install pytest pyright ruff        # dev tools — see below
```

`requirements.txt` is the source of truth and is grouped: four **core**
packages the app will not start without (PySide6, requests, cryptography,
anthropic) and four **optional** ones that each have a runtime fallback, so
the app runs without them. The dev tools are commented out there on purpose —
the release workflow runs PyInstaller over that file and would otherwise bundle
pytest and pyright into the shipped build.

Python **3.10 or newer**. CI builds and tests on 3.12; development happens on
3.10, so avoid syntax newer than that.

Run the application:

```bash
python main.py
```

## Tests

```bash
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
```

47 files, **959 tests**, about 30 seconds. `QT_QPA_PLATFORM=offscreen` is what
lets the Qt tests run without an X11/Wayland server; it is what CI sets on
Linux, and it is unnecessary on Windows.

The suite needs no game files: everything runs from synthetic bytes, word lists
tracked under `data/`, and the real Starfield fonts committed to `data/fonts/`.
One test self-skips — the end-to-end Wwise voice decode, which needs a Starfield
install and `vgmstream-cli`. Four more are POSIX-only and skip on Windows.

On Linux the offscreen Qt plugin links `libEGL.so.1`, so a headless machine
needs it (`libegl1` on Debian/Ubuntu — CI installs it). The Hunspell spell-check
tests fall back to other backends when `hunspell` is absent; see
`requirements.txt` for the per-distro install line if you want to exercise it.

## Lint and type-check

```bash
ruff check .
ruff format --check .
pyright
```

Configuration lives in `pyproject.toml` (ruff) and `pyrightconfig.json`.

- **ruff** — `E`, `W`, `F` rule sets, line length **110**. `E501` (line too
  long) is ignored globally because QSS theme strings and Qt file-filter
  strings cannot be wrapped; `E741` and `E731` are ignored as deliberate style.
- **pyright** — `standard` mode, `pythonVersion` 3.10 (the supported floor, not
  the 3.12 CI runs it under), whole tree checked, and three Qt-noisy rules
  turned off (`reportAttributeAccessIssue`, `reportCallIssue`,
  `reportIncompatibleMethodOverride`) because PySide6's stubs generate 473
  false positives across them.

**Run pyright in the same environment you installed `requirements.txt` into.**
It resolves imports from whatever interpreter it finds, so running it against a
bare system Python reports dozens of phantom errors for PySide6, keyring and
anthropic that CI does not see. CI is authoritative: it installs
`requirements.txt` first.

`pyproject.toml` also carries a `[tool.mypy]` section. No workflow runs mypy —
pyright is the type checker; the config is left in place for anyone who prefers
it locally.

## Pre-commit hooks

```bash
pip install pre-commit
pre-commit install
```

One hook runs on `git commit`: `ruff check --fix`, the same lint CI enforces.
It rewrites what it can and then fails, so you re-stage the fixed files.

`ruff-format` is in the config but commented out on purpose. The codebase has
never been run through the formatter (`ruff format --check .` would reformat
167 of 190 files) and no workflow checks formatting, so enabling the hook would
block any commit touching one of those files and force an unrelated whole-file
reformat into the diff. Adopting it is a one-off repo-wide reformat plus a CI
step, not a local toggle.

## UI translations

Seven locales live in `gui/translations/*.ts`. After editing any of them:

```bash
./scripts/compile_translations.sh
```

Commit only the `.ts` source — `.qm` binaries are gitignored and are rebuilt by
the release workflow. See [TRANSLATING.md](TRANSLATING.md) for the full guide,
including who maintains which locale.

## Translation backends

The app has three, chosen by the model name in the settings — there is no
separate backend switch:

| Backend | Selected by | Needs |
|---|---|---|
| Claude API | a `claude-*` model id | an Anthropic API key (metered) |
| Claude Code CLI | a `claude-code:*` model id | the local `claude` binary (subscription) |
| Ollama | anything else | a local Ollama server |

**No backend is required to work on the code** — the test suite makes no
network calls and spawns no model.

For the Ollama path, three `Modelfile`s are tracked (`Modelfile`,
`Modelfile.qc`, `Modelfile.gemma4-opus48`). None of them names a real GGUF:
every `FROM` is a placeholder path, because the fine-tunes are unpublished.
Point `FROM` at a GGUF you have, then:

```bash
ollama create translategemma3-st -f Modelfile
```

`CLAUDE.md` documents each model and, importantly, how per-model parameter
precedence works before you edit a Modelfile.

## PyInstaller build (local)

```bash
./scripts/compile_translations.sh
echo "__version__ = 'dev'" > _version.py
pip install pyinstaller
pyinstaller bethesda_strings_editor.spec
```

The bundle lands in `dist/bethesda-strings-editor/`. `_version.py` is
overwritten by CI from the git tag; the committed copy is a placeholder.

Release builds compile the PyInstaller bootloader from source
(`pip install --no-binary pyinstaller`) because the prebuilt wheel's bootloader
bytes trip antivirus heuristics. A local build does not need that.

## File associations

`gui/file_associations.py` registers the app as the handler for `.strings`,
`.esp`, `.ba2` and friends — per-user, no root or admin:

```bash
python main.py --register-file-types      # or: ./scripts/install_file_associations.sh
python main.py --unregister-file-types
```

It is split into pure **plan** functions and **apply** functions specifically so
the Windows registry layout is unit-tested on Linux CI (`tests/test_file_associations.py`).
Keep new logic on the plan side where you can.

## CI

Four workflows, all under `.github/workflows/`:

| Workflow | Runs on | Does |
|---|---|---|
| `lint.yml` | every PR and push to `main` | ruff, then pyright |
| `test.yml` | every PR and push to `main` | pytest on Ubuntu **and** Windows |
| `docs.yml` | every PR; deploys from `main` | Sphinx with `-W`, so a warning fails the build |
| `release.yml` | tags matching `v*`, plus manual dispatch | build, sign, publish |

A PR must be green on lint, test (both OSes) and docs before it merges.
`docs.yml`'s `-W` means a malformed docstring in a documented module breaks the
build, not just the page.

## Release process

1. Merge everything into `main`.
2. Rehearse: Actions → **Build and Release** → *Run workflow* with `dry_run`
   checked. It builds both platforms, signs the checksums, and creates a
   **draft** release — private, notifies nobody, creates no tag, and cannot
   reach the NexusMods job.
3. Tag and push:

```bash
git tag vX.Y.Z && git push origin vX.Y.Z
```

CI then builds Linux + Windows bundles, generates the changelog with git-cliff,
writes `SHA256SUMS`, GPG-signs it into `SHA256SUMS.asc`, and publishes the
GitHub Release. A tag containing `-` (e.g. `v0.3.0-rc1`) is marked a
pre-release and skips the NexusMods upload.

What the release reads, and from where — the distinction is not cosmetic:
**secrets are masked in logs and never readable back, variables are stored and
displayed in plaintext.** Every credential belongs in the first column.

| Repository **secret** | Used for |
|---|---|
| `GPG_PRIVATE_KEY` | base64 armored private key — signs `SHA256SUMS` |
| `GPG_PASSPHRASE` | the key's passphrase; optional, read only when set |
| `NEXUSMODS_API_KEY` | mod-page upload; missing → upload skips with a warning |

| Repository **variable** | Used for |
|---|---|
| `NEXUSMODS_FILE_GROUP_ID_LINUX` / `_WINDOWS` | which file group to replace |

The public half of the signing key is committed as `release-signing-key.asc`;
[VERIFICATION.md](VERIFICATION.md) documents the fingerprint and how users
verify a download.

## Project layout

| Path | Contents |
|------|----------|
| `bethesda_strings/` | Pure-Python parsing library (no Qt) |
| `gui/` | PySide6 application layer |
| `tests/` | pytest suite (47 files) |
| `benchmarks/` | Performance benchmarks, run by hand |
| `scripts/` | Dictionary fetchers, dataset builders, translation compile, release upload |
| `resources/` | Icons, QSS stylesheet, NexusMods page assets |
| `data/` | Word lists for language detection, and the bundled Starfield fonts |
| `packaging/` | Linux desktop entry + MIME-type XML (bundled into the build) |
| `docs/` | Sphinx documentation |
| `.github/workflows/` | CI: lint, test, release, docs |
| `Modelfile*` | Ollama model definitions |

Two files in the root are **untracked by design**:
`protected_terms_starfield_hq.txt` (a user extension point — the app ships
without one and falls back to a built-in set) and `starfield_glossary.json` (a
build artifact of `scripts/extract_starfield_glossary.py` that no code opens by
that name). The `.spec` adds the terms file conditionally, because PyInstaller
aborts on a missing `datas` path.

## Architecture

`CLAUDE.md` in the repository root is the detailed map: every module, what it
owns, and — more usefully — *why* the non-obvious parts are the way they are
(independent string-ID spaces per file extension, occurrence-indexed ESP field
write-back, per-model Ollama parameter precedence). Read the relevant section
before changing a parser; several of them encode failure modes that are silent
rather than loud.

## Commit message conventions

Write **conventional-commit** subjects — `type(scope): description`, lowercase
description, scope optional:

```
feat(qa): check the _lrg large-font menus as the width worst case
fix(esp): keep extraction and write-back on the same occurrence index
docs: correct SECURITY.md against the real release
```

git-cliff turns those into the GitHub Release notes. The type picks the
section, the scope is printed in bold, and the `(#28)` a squash merge appends
becomes a link to the pull request:

| Type | Changelog section |
|------|-------------------|
| `feat` | Added |
| `fix` | Fixed |
| `perf`, `refactor` | Changed |
| `revert` | Removed |
| `security` | Security |
| `docs` | Documentation |
| `i18n` | Translations |
| `chore`, `ci`, `build`, `test`, `style` | Maintenance |
| `type!:` or a `BREAKING CHANGE:` footer | Breaking changes (listed first) |
| anything else | Other |

The **subject line only** goes into the release notes, so the body is yours to
use freely — explain *why* there, at whatever length the change deserves.

Older history uses imperative verbs (`Add …`, `Fix …`, `Remove …`, `Update …`)
and `.cliff.toml` still groups those, so a full regenerate of the changelog
reads correctly. New commits should use the conventional form.

Preview what a release would publish before tagging:

```bash
pip install git-cliff
git-cliff --unreleased        # commits since the last tag
git-cliff --latest            # what the last tag published
```
