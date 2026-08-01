# Forking Guide

This editor was built around one language pair — Starfield EN/RU → UK — but
almost nothing is hard-wired to it. Target languages, prompts, glossaries,
checks and backends are all data or small tables. This guide is for the fork
that wants **its own target language**, **its own translation voice**, or **its
own quality rules**, and it says which of those need a fork at all.

Everything below was checked against the code in this repository rather than
recalled — where a fallback is silent or a default is wrong for your language,
it says so.

---

## Two different things are called "language"

Get this distinction right first, or you will edit the wrong file.

| | **Interface language** | **Translation target** |
|---|---|---|
| What it is | The app's own menus, dialogs, buttons | The language the *game strings* are translated into |
| Lives in | `gui/translations/<locale>.ts` (Qt XML) | Locale-code tables across `gui/` and `bethesda_strings/` |
| Codes | BCP-47 — `cs_CZ`, `pl_PL`, `uk_UA` | Starfield locale codes — `cs`, `pl`, `uk`, `ptbr`, `zhhans` |
| Picked in | Settings → Appearance → Interface Language | The toolbar Source/Target combos |
| Guide | **[TRANSLATING.md](TRANSLATING.md)** | **[§2](#2-adding-a-translation-target-language) of this document** |

The two are independent. Adding Czech as a translation *target* does not
require a Czech UI, and a Czech UI does not make Czech available as a target.

---

## 1. What you don't need a fork for

Most "I want it to behave differently" asks are settings, not code. Check this
table before editing Python.

| You want | Where | Notes |
|---|---|---|
| A different translation voice / register | **Translation → Translation Prompt Editor…** | Tuning dials, a per-language Rule 1 override, and a free-text addendum. Applies to Ollama, the Claude API and the Claude Code CLI alike — see [§3.1](#31-no-code-the-prompt-editor) |
| Fixed spellings for names and terms | Glossary (`Ctrl+G` → editor) | CSV / TBX / JSON import; injected into every prompt and enforced by the `GLOSSARY_MISMATCH` check |
| Terms the AI must never touch | Settings → Game Term Protection → *Custom terms file* | A plain UTF-8 list, one term per line. `AppSettings.protected_terms_file`; the built-in set in `gui/term_protector.py` is used when unset |
| Player character's grammatical gender | Settings → Translation Preferences, or the Prompt Editor | `AppSettings.player_gender` — only affects targets in `_GENDERED_TARGETS` |
| Different sampling / context size | The `Modelfile`s in the repo root | Read the **Parameter precedence** section of `CLAUDE.md` first: the app overrides `system`/`temperature`/`num_ctx`/`num_predict` always, and the sampling knobs only when the model's `OllamaWorker.MODEL_CONFIGS` profile names them |
| A different colour scheme | Drop a `.qss` file into `themes/` | Picked up at startup by `ThemeManager._load_custom_themes()`; in a frozen build the directory is `<config dir>/themes` |
| Config somewhere else | `BSE_CONFIG_DIR` env var | Or the `.config_dir_override` bootstrap file — see `get_config_dir_override()` in `gui/app_settings.py` |

If you only need the list above, you don't need a fork — you need a settings
file and maybe a `themes/` directory.

---

## 2. Adding a translation target language

The running example is **Czech (`cs`)**, chosen because the repo is already
half-wired for it (`gui/spell_checker.py` and `quality_checker._LATIN_SCRIPT_TARGETS`
know `cs`; a `cs_CZ` UI translation ships) — so it shows both what you add and
what silently defaults to something wrong.

### 2.1 The 30-second version

One line makes a language selectable end to end:

```python
# gui/app_settings.py
SUPPORTED_LANGUAGES: list[tuple[str, str]] = [
    ("English",              "en"),
    ("Czech",                "cs"),   # ← added
    ...
]
```

`MainWindow` and `SettingsDialog` both build their pickers from this one table
(`tests/test_language_settings.py` pins that they stay one table), the code
flows into `TranslationRequest.target_lang`, and every backend produces Czech.

**What you get for free:** both language pickers, settings persistence and
validation, prompt assembly, all three backends, translation memory, glossary,
cache, tag restoration, the width-fit simulator, the diff and migration tools.

**What silently degrades**, because every per-language table is a `dict.get()`
with a fallback and none of them raise on an unknown code:

| Symptom | Cause | Fixed in |
|---|---|---|
| The prompt says *"polished **cs**"* instead of *"polished Czech"* | not in `_LANG_DISPLAY` | [2.2](#22-display-name) |
| Generic style rule — no register, script or terminology guidance | not in `_TARGET_STYLE` | [2.3](#23-the-style-rule-prompt-rule-1) |
| Not offered in the Prompt Editor's per-language override combo | that combo is built from `_LANG_DISPLAY`, **not** `SUPPORTED_LANGUAGES` | [2.2](#22-display-name) |
| Legacy files decode with the wrong 8-bit codepage | `ENCODING_PAIRS` has `'czech'` but no `'cs'`, and the fallback is `windows-1252` | [2.4](#24-encoding-pair) |
| No gender directive for player-referring lines | not in `_GENDERED_TARGETS` | [2.5](#25-grammatical-gender) |
| No untranslated-output detection, no coverage check | no word list / checker module | [2.6](#26-word-list-and-checker-module) |
| No spell check | no Hunspell mapping | [2.7](#27-spell-check) |

That encoding one is worth seeing, because nothing warns you:

```pycon
>>> EncodingConverter.get_encodings_for_locale("cs")
('utf-8', 'windows-1252')      # wrong — Czech is Central European
>>> EncodingConverter.get_encodings_for_locale("czech")
('utf-8', 'windows-1250')      # the entry that exists
```

### 2.2 Display name

```python
# gui/ollama_worker.py
_LANG_DISPLAY: dict[str, str] = {
    "cs": "Czech",
    ...
}
```

This is the name written into the prompt (`"Translate the English text to
natural, polished Czech"`) and into the Claude clients' user turns
(`claude_client.py`, `claude_code_client.py`, `claude_chat_panel.py` all read
it). It is also what the **Prompt Editor** builds its language combo from
(`_ordered_langs()` in `gui/prompt_editor_dialog.py`), so a language missing
here cannot be given a custom Rule 1 through the UI — add the code to
`_LANG_ORDER` in that module too if you want it near the top of the list.

### 2.3 The style rule (prompt Rule 1)

This is the single highest-leverage edit in a fork. Rule 1 of the system prompt
is the target language's register, script and terminology guidance:

```python
# gui/ollama_worker.py
_TARGET_STYLE: dict[str, str] = {
    "cs": (
        "Write standard Czech with correct case and gender agreement. "
        "Match the source register: formal (vykání) stays formal, casual "
        "(tykání) stays casual, and never mix the two when addressing one "
        "person. Preserve Starfield's NASApunk tone — technical, precise, "
        "modern. Technical readouts use present tense. "
        "Avoid unnecessary Anglicisms."
    ),
    ...
}
```

Read the existing entries before writing yours — the `uk` and `ko` ones show
how much weight this carries. The Ukrainian rule is what stops Russian
vocabulary leaking in and pins the ти/ви register; the Korean rule teaches
받침-correct particle allomorphs *and* how to read Latin acronyms aloud before
choosing one. Both were written against observed model failures, which is the
right way to grow yours: translate a few hundred strings, look at what the
model gets wrong systematically, and put that in Rule 1.

Both the normal and the AI-fix prompt paths resolve this through
`effective_style_rule(target_lang)`, so a user override from the Prompt Editor
replaces it cleanly and you never need to touch the callers.

### 2.4 Encoding pair

```python
# bethesda_strings/encoding.py
ENCODING_PAIRS = {
    ...
    'czech':    ('utf-8', 'windows-1250'),
    'cs':       ('utf-8', 'windows-1250'),   # ← add the code, not just the name
}
```

Add **both** the locale code and the lowercase display name. The lookup tries
the exact key, then a separator-stripped form (`uk_UA` → `uk`), then the
sub-tag — and when all three miss it returns `('utf-8', 'windows-1252')`
without logging anything. Modern Starfield string files are UTF-8, so this only
bites on older or hand-edited files, which is exactly when it is hardest to
diagnose.

### 2.5 Grammatical gender

If second-person address or player-referring adjectives inflect for gender in
your language, add the code:

```python
# gui/ollama_worker.py
_GENDERED_TARGETS: frozenset[str] = frozenset(
    {"cs", "de", "es", "fr", "it", "pl", "ptbr", "ru", "uk"}
)
```

This gates three things at once: `_player_gender_directive()` (injected into
both prompt paths), the public `is_gendered_target()` predicate, and the
pre-batch nudge in `main_window._check_player_gender_nudge()` that warns when
the batch contains player-referring lines and no gender is set. Leave the code
out for languages where the directive would be a no-op (`en`, `ja`, `ko`,
`zhhans`) — the nudge would be pure noise there.

### 2.6 Word list and checker module

The word list answers *"is this token a real word in language X"*, which is how
the quality checker detects untranslated output. Four files, in order:

**a. Register the download** — `scripts/download_lang_dicts.py`:

```python
LANG_CONFIGS = {
    "cs": {
        "url": f"{_BASE}/cs/cs_50k.txt",
        "out": "czech_words.txt",
        "display": "Czech",
    },
    ...
}
```

Source is [hermitdave/FrequencyWords](https://github.com/hermitdave/FrequencyWords)
(MIT), format `word count` per line. `_BASE` pins the 2018 corpus; `_BASE_2016`
exists only for Korean and there is a comment explaining why — don't reuse it
casually.

**b. Fetch it** — `python scripts/download_lang_dicts.py cs`, writing
`data/czech_words.txt`.

**c. Write the checker** — `gui/cs_word_checker.py`, sixteen lines like every
other one:

```python
"""Czech word checker — data/czech_words.txt (hermitdave/FrequencyWords)."""
from typing import Optional

from gui._word_checker_base import WordChecker as _WC

_checker = _WC("czech_words.txt", "Czech")


def word_is_czech(word: str) -> Optional[bool]:
    return _checker.word_in(word)


def text_has_czech_words(text: str, threshold: int = 4) -> bool:
    return _checker.text_has_words(text, threshold)


def dict_loaded() -> bool:
    return _checker.is_loaded()


def preload() -> None:
    _checker.preload()
```

`WordChecker` handles thread-safe lazy loading, both file formats, and a
missing file (one warning, then every answer is `None` — the checks that use it
go quiet rather than crash). The one knob is `min_word_len`, which defaults to
3 for Latin scripts; Korean passes `1` because a Hangul block is a whole
syllable.

**d. Register the preloader** — `gui/ollama_worker.py`, an import alias and one
dict entry:

```python
from gui.cs_word_checker import preload as _preload_cs_dict

_DICT_PRELOADERS: dict[str, Callable[[], None]] = {
    "cs": _preload_cs_dict,
    ...
}
```

`preload_language_dictionaries(source, target)` runs at the start of every
batch and warms **only** the pair plus English (plus Russian for a Ukrainian
target). This is deliberate and load-bearing: all ten lists resident at once
cost 329 MB. An unregistered code is skipped silently — the list still loads,
just lazily on the first lookup, on a worker thread, mid-batch.

> **A test enforces step (a).** `tests/test_download_lang_dicts.py` derives its
> coverage check from the checkers themselves: any `gui/*_word_checker.py` that
> owns a `WordChecker` must have a `LANG_CONFIGS` entry. Adding (c) without (a)
> fails the suite. That check exists because Korean shipped without a
> downloader entry and nobody noticed.

See [`data/README.md`](data/README.md) for the formats, sizes and memory cost
of each list.

### 2.7 Spell check

```python
# gui/spell_checker.py
LANG_TO_DICT: Dict[str, str] = {
    "cs": "cs_CZ", "czech": "cs_CZ",   # already present
    ...
}
```

The value is a Hunspell dictionary name; the checker searches bundled `dicts/`,
per-user and system paths, and reports unavailable rather than failing when the
`.dic`/`.aff` pair is absent. To ship one with a Windows/macOS build, add the
locale to `SOURCES` in `scripts/fetch_dictionaries.py` — and to `DEFAULT_LANGS`
in the same file if it should be fetched without being named explicitly.

### 2.8 Quality-check wiring

`gui/quality_checker.py` keys several checks on the target code. All of them
accept *both* the code and the lowercase display name — follow that convention.

| Set / map | What it turns on |
|---|---|
| `_LATIN_SCRIPT_TARGETS` | Cyrillic-in-output = source leak, when the source is RU/UK |
| `_CYRILLIC_SOURCES` | the source half of that check |
| `_CJK_TARGETS`, `_HANGUL_TARGETS` | `LOW_SCRIPT_COVERAGE`, the CJK width factor, and suppression of length-ratio checks |
| `_LATIN_CHECKER_MAP` in `_check_latin_coverage()` | `LOW_TARGET_COVERAGE` — maps the code to your `(module, word_is_X, display)` triple |

For Czech that is one entry in `_LATIN_CHECKER_MAP`
(`"cs": ("gui.cs_word_checker", "word_is_czech", "Czech")`) plus the `"czech"`
alias; `_LATIN_SCRIPT_TARGETS` already lists it.

If you add a **new issue code**, also decide which of the two sets at the top of
the module it belongs to — `AUTOFIX_CODES` (fixable mechanically, no AI) or
`RETRANSLATE_CODES` (needs another model call). A code in neither is reported
and nothing more.

### 2.9 A language-specific checker (optional)

`gui/ko_particle_checker.py` and `gui/gender_checker.py` are the two worked
examples of a check that only makes sense for one language. Read the Korean one
before writing yours — the useful part is not the Hangul arithmetic but the
discipline:

- It checks **only** the subset it can prove. The 은/는 and 을/를 particle pairs
  are verified; 가/과/로 are not, because they are also productive
  Sino-Korean noun suffixes and no word list can tell the two apart.
- It is **silent** when its word list is missing, rather than guessing — the
  auto-fixer acts on what it reports.
- It was **measured**: 0 false positives over 5,353 real Korean strings.

A checker that fires on good text is worse than no checker, because its output
feeds an auto-fixer. Wire yours into `QualityChecker` behind a target-language
gate, import the module lazily so other targets never pay for it, and add it to
`AUTOFIX_CODES` only if the fix is deterministic.

### 2.10 Can the game actually draw it?

A correct translation still ships broken if Starfield's font atlases have no
glyph for your script, or if the drawn label overruns its widget. Both are
answerable before you ship, with no fork required:

- **Translation → Font Glyph Checker…** — finds text that renders as tofu
  against the game's Scaleform SWF atlases (`bethesda_strings/font_checker.py`).
- **Translation → UI Width-Fit Simulator…** — measures real font advances
  against real widget bounds read out of the game's own SWFs, including the
  large-font accessibility build (`bethesda_strings/width_fit.py`,
  `swf_widgets.py`).

If your script is absent from the bundled faces in `data/fonts/`, that is a
font-replacement problem in the mod, not a code change here — but find out
early.

### 2.11 Checklist

| # | File | Symbol | Required? |
|---|---|---|---|
| 1 | `gui/app_settings.py` | `SUPPORTED_LANGUAGES` | **Yes** — nothing is selectable without it |
| 2 | `gui/ollama_worker.py` | `_LANG_DISPLAY` | Strongly recommended (prompt wording + Prompt Editor) |
| 3 | `gui/ollama_worker.py` | `_TARGET_STYLE` | Strongly recommended (translation quality) |
| 4 | `bethesda_strings/encoding.py` | `ENCODING_PAIRS` | Yes if not Latin-1 |
| 5 | `gui/ollama_worker.py` | `_GENDERED_TARGETS` | Yes if the language inflects for gender |
| 6 | `scripts/download_lang_dicts.py` + `data/` + `gui/<code>_word_checker.py` + `_DICT_PRELOADERS` | word list | Recommended — enables leak/coverage checks (**and a test requires the downloader entry**) |
| 7 | `gui/spell_checker.py` | `LANG_TO_DICT` | Optional |
| 8 | `gui/quality_checker.py` | `_LATIN_SCRIPT_TARGETS` / `_CJK_TARGETS` / `_HANGUL_TARGETS` / `_LATIN_CHECKER_MAP` | Recommended |
| 9 | `gui/ollama_worker.py` | `_SOURCE_EXTRA`, `_PAIR_EXTRA`, `_LANG_EXAMPLES` | Optional — pair-specific notes and few-shot examples |
| 10 | `tests/` | your own | Recommended — see [§6](#6-keeping-the-fork-healthy) |

---

## 3. Custom prompts

### 3.1 No code: the Prompt Editor

**Translation → Translation Prompt Editor…** edits three layers, with a live
preview of the fully assembled prompt:

1. **Tuning dials** — structured knobs (language style, formality, vocabulary,
   grammar, expression localization, translation rigor). Only non-default
   choices contribute text.
2. **Rule 1 override** — replaces the built-in `_TARGET_STYLE` entry for one
   target language.
3. **Addendum** — free text appended to every prompt.

Saved as `AppSettings.custom_style_rules` / `custom_prompt_addendum` /
`custom_prompt_dials`, installed by `main_window._apply_prompt_customizations()`
at startup and after every save. Because all three backends assemble their
prompt through `TranslationRequest.to_system_prompt()`, edits apply to Ollama,
the Claude API and the Claude Code CLI identically.

The token-preservation rules (2–7) are deliberately not editable — they are
what keeps `<Alias=…>`, `%s` and `[[STRUCT_BREAK_…]]` intact, and a fork that
loosens them will corrupt game files, not just translate them differently.

### 3.2 In code: how the prompt is assembled

`TranslationRequest.to_system_prompt()` in `gui/ollama_worker.py` is the single
assembly point, with two paths — normal and AI-fix (proofreader). In order:

```
persona + task line              ← _LANG_DISPLAY for both language names
Rule 1                           ← effective_style_rule(target)  = override or _TARGET_STYLE
Rules 2–7                        ← fixed: tokens, brackets, quotes, spacing, punctuation
Note:                            ← _SOURCE_EXTRA[src] + _PAIR_EXTRA[(src, tgt)]
player-gender directive          ← _player_gender_directive(target), gated on _GENDERED_TARGETS
Examples:                        ← _LANG_EXAMPLES[(src, tgt)]
developer context note           ← per string, from the plugin's NLDT
Glossary:                        ← per string, matched terms
retry hint                       ← per string, on a QC-driven retranslation
Character Voice                  ← translator profile, if assigned
Translation preferences:         ← build_dials_prompt(_CUSTOM_PROMPT_DIALS)
addendum                         ← _CUSTOM_PROMPT_ADDENDUM
```

Customization state lives at **module scope** — `_CUSTOM_STYLE_OVERRIDES`,
`_CUSTOM_PROMPT_ADDENDUM`, `_CUSTOM_PROMPT_DIALS`, installed by
`set_prompt_customizations()`. That is a deliberate choice: `TranslationRequest`
is constructed in dozens of places, and threading three more arguments through
all of them would guarantee a caller that misses them. `_PLAYER_GENDER` is a
separate module-level layer for the same reason, kept *out* of the
customization 3-tuple so `get_prompt_customizations() == ({}, "", {})` stays
the tested contract.

**If you fork the prompt itself, know what is Ukrainian-specific in it.**
Rule 3(a) — the dialogue-choice bracket tokens — lists its examples as
`[Lie]→[Збрехати]`, `[Persuade]→[Переконати]` and about thirty more. The *rule*
(translate the word, keep the brackets) is universal; the examples are not, and
they are emitted for **every** target — ask for German and the model still sees
a wall of Cyrillic illustrating rule 3(a). A fork with a different primary
target should swap them for its own language.

`_LANG_EXAMPLES` is the opposite case and is safe: it is keyed by
`(source, target)` and simply absent for a pair it doesn't have. Ten pairs ship
(`en` → `de es fr it ja ko pl ptbr uk zhhans`, plus `ru` → `uk`), so a new
target starts with no few-shot examples until you add an entry — worth doing,
since these are where tone is demonstrated rather than described.

### 3.3 Adding a tuning dial

`PROMPT_DIALS` is the single source of truth for both the prompt builder and
the editor UI — add an entry and the combo or checkbox group appears by itself:

```python
PROMPT_DIALS: list[dict] = [
    {
        "key": "humor", "label": "Humor", "multi": False, "default": "asis",
        "options": [
            ("asis",   "As in source", ""),          # neutral → contributes nothing
            ("dry",    "Dry",          "Render humor dryly and understatedly."),
            ("broad",  "Broad",        "Let comic lines land broadly; favor playful phrasing."),
        ],
    },
    ...
]
```

The neutral/default option **must** carry an empty instruction — that is what
keeps an untouched dial out of the prompt entirely, and
`tests/test_prompt_editor.py` asserts it, along with the dials block appearing
before the addendum and in the fix-mode path.

---

## 4. Adding a translation backend

Three exist: `OllamaWorker` (local HTTP), `ClaudeTranslationWorker` (Anthropic
API), and the same worker driving `ClaudeCodeClient` (the local `claude` CLI on
a subscription). To add a fourth:

1. **Implement the signal interface.** A worker is a `QObject` moved onto a
   `QThread` that emits:

   ```python
   translation_ready = Signal(int, str, object)   # row index, text, string_id
   progress          = Signal(int, int)           # done, total
   error             = Signal(str)
   finished          = Signal(int, int)           # succeeded, failed
   usage_ready       = Signal(object)             # optional: dict of real token usage
   ```

   `string_id` is `object`, not `int`, on purpose — FormIDs above `0x7FFFFFFF`
   overflow a signed int.

2. **Route to it** in `main_window._init_translation_worker()`, which currently
   dispatches on the model name (`is_claude_model()` → Claude worker, else
   Ollama). There is no separate backend setting; the model id *is* the
   selector, and `claude-code:*` ids are what pick the CLI client over the API
   one.

3. **Build the prompt through `TranslationRequest.to_system_prompt()`.** Don't
   write your own — that is what makes the Prompt Editor, glossary, player
   gender and profiles work on your backend for free.

4. **Keep the per-string pipeline**: `TermProtector.protect()` → model call →
   `restore()` → `_restore_dropped_tags()` → `QualityChecker`. `ClaudeTranslationWorker`
   is the shorter of the two to copy, and `tests/test_claude_worker_cache_terms.py`
   documents the wiring mistakes worth avoiding — in particular, use a real
   `TranslationMemory` in tests, never a `dict` stand-in: a dict has a `.get()`
   and hid a call to a method `TranslationMemory` doesn't have, failing every
   string in a batch the moment a TM was loaded.

---

## 5. Other extension points

| Extend | Start at | Notes |
|---|---|---|
| Themes | `gui/theme_manager.py` (`THEMES` dict) or a `.qss` in `themes/` | `tests/test_theme_groupbox_title.py` requires every built-in theme to reserve its `QGroupBox::title` band in **em** units |
| A new field type in plugins | `_FIELD_DEFS` in `bethesda_strings/esp_handler.py` | Extraction and write-back **must** agree on the `_field_translatable_text()` predicate — when they disagree, every later occurrence of that signature in the record shifts a slot and receives another field's translation |
| A new file format | `bethesda_strings/` — no Qt in this layer | Keep it pure Python and hand-build test bytes; the whole test suite runs without a game install |
| String categories / icons | `gui/string_type_detector.py` | Feeds display icons and filtering |
| Difficulty scoring | `gui/pre_translation_estimator.py` | Weights are learned from manual corrections and persisted as JSON |
| Lore injection | `gui/lore_rag_manager.py` | Snippets go in the *user* turn, not the system prompt, so Claude's prompt caching isn't broken per string |
| A new setting | `AppSettings` in `gui/app_settings.py` | Add the field, a `setdefault` in the migration, and bump `CONFIG_VERSION`; obfuscated fields go in `_OBFUSCATED_FIELDS` (nested structures need their own wrapper — see the MCP entries) |

---

## 6. Keeping the fork healthy

**Run the suite.** It is fast, pure Python, and needs no Qt event loop, no
network and no game install:

```bash
conda run -n bethesda-strings-editor python -m pytest tests/
```

The tests most likely to catch a language change are
`test_language_settings.py` (the two pickers are one table; codes not display
names), `test_download_lang_dicts.py` (every frequency list is downloadable —
**derived from your checker modules**, so a new checker without a downloader
entry fails), `test_dict_preload.py` (only the pair is warmed),
`test_prompt_editor.py` (dial spec sanity, layer ordering, fix-mode path), and
`test_player_gender.py` (the directive fires for gendered targets only).

**Lint and type-check** before committing — see [CONTRIBUTING.md](CONTRIBUTING.md)
for the exact `ruff` and `pyright` invocations and the pre-commit hooks.

**Stay mergeable with upstream.** Every language addition in §2 is an *entry in
an existing table*, never a modified line. Forks that keep it that way rebase
cleanly; forks that rewrite `_TARGET_STYLE` wholesale or edit Rules 2–7 in
place will conflict on every upstream prompt change. If you need a different
Rule 1 for an existing language, prefer `AppSettings.custom_style_rules` (the
Prompt Editor) over editing the dict — it survives a rebase untouched.

**Contributions welcome.** A new target language, done to the checklist in
§2.11, is exactly the kind of change this project wants upstream — open a PR
rather than carrying it in a fork. Commit conventions are in
[CONTRIBUTING.md](CONTRIBUTING.md); the project is MIT-licensed, and the
bundled word lists and fonts carry their own licences (see
[`data/README.md`](data/README.md)).
