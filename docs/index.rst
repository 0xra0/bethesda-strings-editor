Bethesda Strings Editor
=======================

AI-assisted localization editor for Bethesda Starfield. Translates
``.strings`` / ``.dlstrings`` / ``.ilstrings`` and ESP/ESM plugin files
using either the Claude API, a Claude Code subscription, or a
locally-running `Ollama <https://ollama.ai>`_ model.

.. toctree::
   :maxdepth: 2
   :caption: User Guide

   format-spec
   architecture

.. toctree::
   :maxdepth: 2
   :caption: API Reference

   api/bethesda_strings

.. toctree::
   :maxdepth: 1
   :caption: Contributing

   contributing

.. toctree::
   :maxdepth: 1
   :caption: Deployment

   nexusmods_registration

Quick start
-----------

.. code-block:: bash

   pip install -r requirements.txt
   python main.py

The translation backend is chosen by the model name in Settings — there is no
separate backend switch. A ``claude-*`` model uses the Anthropic API, a
``claude-code:*`` model shells out to the local ``claude`` CLI on your
subscription, and anything else goes to `Ollama <https://ollama.ai>`_ running
locally:

.. code-block:: bash

   ollama create translategemma3-st -f Modelfile

The tracked Modelfiles carry placeholder ``FROM`` paths — point them at a GGUF
you have first.

Indices
-------

* :ref:`genindex`
* :ref:`modindex`
