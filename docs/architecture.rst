Architecture
============

The application is split into two layers with a hard boundary: the
``bethesda_strings`` parsing library has no Qt dependency and can be
used from scripts; the ``gui`` package contains all PySide6 code.

Component diagram
-----------------

.. code-block:: text

   ┌──────────────────────────────────────────────────────────────────┐
   │  gui/                                                            │
   │                                                                  │
   │  MainWindow ──────────────────────────────────────────────────┐  │
   │    │  owns                                                     │  │
   │    ├── StringTableModel / StringTableView (QAbstractTableModel)│  │
   │    ├── OllamaWorker · ClaudeTranslationWorker (QThread)        │  │
   │    │     ├── TermProtector          (placeholder tokens)       │  │
   │    │     ├── TranslationCache       (sha256 → translation)     │  │
   │    │     ├── TranslationMemory      (string_id → translation)  │  │
   │    │     └── ThreadPoolExecutor     (parallel HTTP calls)      │  │
   │    ├── QualityChecker               (post-translation QA)      │  │
   │    ├── PreTranslationEstimator      (difficulty score 0–100)   │  │
   │    ├── ConsistencyChecker           (same source, diff trans)  │  │
   │    ├── GlossaryManager              (CSV/TBX/JSON terms)       │  │
   │    └── KeyboardManager              (vim nav, command palette) │  │
   │                                                                   │
   └───────────────────────────────────────────────────────────────────┘
                │  reads / writes
   ┌────────────▼──────────────────┐
   │  bethesda_strings/            │
   │    BethesdaStringFile  (.strings/.dlstrings/.ilstrings)        │
   │    EspFile             (ESP/ESM non-localized plugins)         │
   │    XMLHandler          (xTranslator SST XML)                   │
   │    EncodingConverter   (UTF-8 / CP1251 / CP1252 detection)     │
   │    VersionDiff         (game-version comparison)               │
   └───────────────────────────────┘

Translation pipeline
--------------------

For each string queued for AI translation:

.. code-block:: text

   raw original text
        │
        ▼
   TermProtector.protect()       — replace proper nouns with «PH_0», «PH_1», …
        │
        ▼
   TranslationMemory lookup      — return known translation immediately if hit
        │  (miss)
        ▼
   TranslationCache lookup       — return cached result immediately if hit
        │  (miss)
        ▼
   model backend call            — Ollama /api/generate · Claude API · Claude Code CLI
                                   (parallel via ThreadPoolExecutor)
        │
        ▼
   TermProtector.restore()       — replace «PH_0», «PH_1», … back to original terms
        │
        ▼
   _restore_dropped_tags()       — re-insert game tags the model dropped (<mag>, …)
        │
        ▼
   QualityChecker.check()        — emit issues (tag mismatch, truncation, …)
        │
        ▼
   emit translation_ready(index, text, string_id)
        │
        ▼
   StringTableModel.set_translated_text()

File I/O
--------

**Opening a file**

``MainWindow._open_file()`` inspects the extension:

- ``.strings`` / ``.dlstrings`` / ``.ilstrings`` → ``BethesdaStringFile``
  → ``StringTableModel`` in ``"strings"`` mode
- ``.esp`` / ``.esm`` → ``EspFile`` → ``StringTableModel`` in ``"esp"`` mode
- ``.xml`` → ``XMLHandler.import_xml()`` merges translations into the
  currently open file

**Saving a file**

``MainWindow._save_file()`` calls ``BethesdaStringFile.save()`` or
``EspFile.save()`` which rebuild the binary from the in-memory
``StringDataObject`` list.

Settings
--------

``AppSettings`` (``CONFIG_VERSION = 43``) is a ``dataclass`` persisted as
``config.json`` under a per-OS config directory (a ``QSettings`` store
mirrors it as a secondary):

- Linux: ``$XDG_CONFIG_HOME/BethesdaModTools/config.json`` (default
  ``~/.config/BethesdaModTools/config.json``)
- Windows: ``%APPDATA%\BethesdaModTools\config.json``
- macOS: ``~/Library/Application Support/BethesdaModTools/config.json``

Set the ``BSE_CONFIG_DIR`` environment variable to override the directory.
``load_settings()`` applies a migration chain when the stored
``config_version`` is lower than the current constant, so old configs
are upgraded without data loss.

Theme system
------------

``ThemeManager`` ships sixteen built-in QSS themes — ``Slate`` (the
default dark), ``Light``, ``High Contrast`` (WCAG AAA), and palette themes
such as ``Nord``, ``Dracula``, ``Catppuccin``, ``Solarized Dark`` /
``Solarized Light``, ``Gruvbox``, ``Tokyo Night``, ``Monokai``,
``One Dark``, ``Sepia`` and ``Starfield`` — plus custom themes.  Each is
applied as an application-wide stylesheet via
``QApplication.setStyleSheet()``; when the app follows the OS colour
scheme, ``Slate`` and ``Light`` are the auto dark/light pair.

Quality checks
--------------

``QualityChecker.check()`` runs a battery of checks (34 issue codes);
a representative subset:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Code
     - What it detects
   * - ``MISSING_TAG``
     - Game tags absent from output
   * - ``EXTRA_TAG``
     - Tags added by the model
   * - ``NEWLINE_COUNT_MISMATCH``
     - Different ``\n`` count
   * - ``TRANSLATION_TRUNCATED``
     - AI stopped mid-sentence
   * - ``SUSPICIOUSLY_SHORT``
     - Output far shorter than input
   * - ``ENCODING_ERROR``
     - Non-target-language characters
   * - ``SOURCE_LANGUAGE_LEAK``
     - Source-language text left untranslated
   * - ``GLOSSARY_MISMATCH``
     - Term translated inconsistently
   * - ``UI_OVERFLOW``
     - Too wide for its length-critical UI widget
   * - ``KO_PARTICLE_MISMATCH``
     - Korean particle (조사) disagreement

Auto-fixable codes are listed in ``AUTOFIX_CODES``; codes that warrant
AI retranslation are in ``RETRANSLATE_CODES``.
