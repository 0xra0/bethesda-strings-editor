Contributing
============

The full contributor guide lives in `CONTRIBUTING.md
<https://github.com/0xra0/bethesda-strings-editor/blob/main/CONTRIBUTING.md>`_
in the repository root. It is kept there rather than duplicated here, because
two copies of the same instructions drift apart — this page summarises it and
links out for the detail.

Quick reference
---------------

.. code-block:: bash

   git clone https://github.com/0xra0/bethesda-strings-editor
   cd bethesda-strings-editor
   pip install -r requirements.txt
   pip install pytest pyright ruff        # dev tools

   python main.py                                     # run the app
   QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q   # 959 tests, ~30s
   ruff check . && ruff format --check . && pyright   # what CI runs

Python 3.10 or newer; CI builds and tests on 3.12. ``requirements.txt``
separates the four core packages from the optional ones — every optional
package has a runtime fallback, so the app starts without them. The dev tools
are deliberately not listed there, because the release workflow runs
PyInstaller over that file.

No translation backend is needed to work on the code: the test suite makes no
network calls and starts no model.

Where things live
-----------------

.. code-block:: text

   bethesda_strings/   Pure-Python parsing library (no Qt)
   gui/                PySide6 application layer
   tests/              pytest suite (47 files)
   benchmarks/         Performance benchmarks, run by hand
   scripts/            Dictionary fetchers, dataset builders, release upload
   resources/          Icons, QSS stylesheet, NexusMods page assets
   data/               Word lists for language detection + bundled Starfield fonts
   packaging/          Linux desktop entry + MIME-type XML
   docs/               This documentation (Sphinx)
   .github/workflows/  CI: lint, test, release, docs

``CLAUDE.md`` in the repository root is the detailed architecture map: what each
module owns and why the non-obvious parts are the way they are. Read the
relevant section before changing a parser.

CI
--

Four workflows: ``lint`` (ruff, then pyright), ``test`` (pytest on Ubuntu and
Windows), ``docs`` (Sphinx with ``-W``, so a warning fails the build) and
``release``. A pull request must be green on the first three.

Releases
--------

Tags matching ``v*`` trigger the release workflow, which builds both platforms,
generates the changelog with git-cliff, writes ``SHA256SUMS`` and GPG-signs it.
``workflow_dispatch`` with ``dry_run`` rehearses the whole thing into a draft
release that creates no tag and never touches NexusMods. ``_version.py`` is
written by CI from the tag; the committed copy is a placeholder.

See `VERIFICATION.md
<https://github.com/0xra0/bethesda-strings-editor/blob/main/VERIFICATION.md>`_
for the signing key fingerprint and how users verify a download.
