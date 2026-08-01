Forking
=======

The full fork guide lives in `FORKING.md
<https://github.com/0xra0/bethesda-strings-editor/blob/main/FORKING.md>`_ in the
repository root, next to ``CONTRIBUTING.md``. It is kept there rather than
duplicated here — this page is the map.

Two things are called "language"
--------------------------------

Editing the wrong one is the usual first mistake:

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * -
     - Interface language
     - Translation target
   * - What it is
     - The app's own menus and dialogs
     - The language game strings are translated *into*
   * - Lives in
     - ``gui/translations/<locale>.ts``
     - Locale-code tables in ``gui/`` and ``bethesda_strings/``
   * - Codes
     - BCP-47 — ``cs_CZ``, ``pl_PL``
     - Starfield codes — ``cs``, ``pl``, ``ptbr``, ``zhhans``
   * - Guide
     - `TRANSLATING.md <https://github.com/0xra0/bethesda-strings-editor/blob/main/TRANSLATING.md>`_
     - `FORKING.md <https://github.com/0xra0/bethesda-strings-editor/blob/main/FORKING.md>`_

The two are independent: neither requires the other.

Adding a translation target language
------------------------------------

One entry in ``SUPPORTED_LANGUAGES`` (``gui/app_settings.py``) makes a language
selectable end to end — both pickers, all three backends, memory, glossary,
cache and the review tools. Everything after that is quality, and every
per-language table is a ``dict.get()`` with a fallback, so an unregistered code
degrades **silently**:

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - Table
     - Missing entry means
   * - ``ollama_worker._LANG_DISPLAY``
     - the prompt names the language by its bare code, and the Prompt Editor
       won't offer it
   * - ``ollama_worker._TARGET_STYLE``
     - a generic style rule instead of register/script/terminology guidance
   * - ``encoding.ENCODING_PAIRS``
     - legacy 8-bit files decode as Windows-1252
   * - ``ollama_worker._GENDERED_TARGETS``
     - no player-gender directive, no pre-batch nudge
   * - word list + ``gui/<code>_word_checker.py`` + ``_DICT_PRELOADERS``
     - no untranslated-output or coverage detection
   * - ``spell_checker.LANG_TO_DICT``
     - no spell check
   * - ``quality_checker`` script/leak sets
     - no source-leak or script-coverage check

``FORKING.md`` §2 walks each of these in order with a worked Czech example and a
checklist, and names the tests that fail if a word-list checker is added without
registering its downloader.

Custom prompts
--------------

Three layers, all reachable without a fork through **Translation → Translation
Prompt Editor…**: tuning dials, a per-language Rule 1 override, and a global
addendum. They are stored in ``AppSettings`` and installed at module scope in
``ollama_worker``, so every backend picks them up — the prompt is assembled in
exactly one place, ``TranslationRequest.to_system_prompt()``.

The token-preservation rules (2–7) are deliberately not editable: they are what
keeps ``<Alias=…>``, ``%s`` and ``[[STRUCT_BREAK_…]]`` intact.

Other extension points
----------------------

Backends (the worker signal interface), themes, plugin field types, string
categories, lore injection and new settings — all covered in ``FORKING.md``
§§4–5, along with how to keep a fork mergeable with upstream.
