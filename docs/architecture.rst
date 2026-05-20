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
   │    ├── OllamaWorker (QThread)                                  │  │
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
   Ollama HTTP API               — POST /api/generate (parallel via ThreadPoolExecutor)
        │
        ▼
   TermProtector.restore()       — replace «PH_0», «PH_1», … back to original terms
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

``AppSettings`` (``CONFIG_VERSION = 9``) is a ``dataclass`` persisted as
JSON to the platform config directory:

- Linux: ``~/.config/BethesdaModTools/bethesda-strings-editor.json``
- Windows: ``%APPDATA%\BethesdaModTools\bethesda-strings-editor.json``

``load_settings()`` applies a migration chain when the stored
``config_version`` is lower than the current constant, so old configs
are upgraded without data loss.

Theme system
------------

``ThemeManager`` ships four built-in QSS themes (``Slate``, ``Dark``,
``Light``, ``High Contrast``) and supports loading arbitrary ``.qss``
files from disk.  The theme is applied as an application-wide stylesheet
via ``QApplication.setStyleSheet()``.

Quality checks
--------------

``QualityChecker.check()`` runs these checks in order:

+---------------------------+-------------------------------+
| Code                      | What it detects               |
+===========================+===============================+
| ``MISSING_TAGS``          | Game tags absent from output  |
+---------------------------+-------------------------------+
| ``EXTRA_TAGS``            | Tags added by the model       |
+---------------------------+-------------------------------+
| ``NEWLINE_COUNT_MISMATCH``| Different ``\n`` count        |
+---------------------------+-------------------------------+
| ``TRANSLATION_TRUNCATED`` | AI stopped mid-sentence       |
+---------------------------+-------------------------------+
| ``SUSPICIOUSLY_SHORT``    | Output < 20 % of input length |
+---------------------------+-------------------------------+
| ``ENCODING_ERROR``        | Non-target-language chars     |
+---------------------------+-------------------------------+
| ``RUSSIAN_LEAK``          | Russian-only chars in output  |
+---------------------------+-------------------------------+
| ``GLOSSARY_MISMATCH``     | Term translated inconsistently|
+---------------------------+-------------------------------+

Auto-fixable codes are listed in ``AUTOFIX_CODES``; codes that warrant
AI retranslation are in ``RETRANSLATE_CODES``.
