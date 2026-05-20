Bethesda Strings Editor
=======================

AI-assisted localization editor for Bethesda Starfield. Translates
``.strings`` / ``.dlstrings`` / ``.ilstrings`` and ESP/ESM plugin files
using a locally-running `Ollama <https://ollama.ai>`_ model.

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

Quick start
-----------

.. code-block:: bash

   pip install PySide6 requests
   python main.py

Requires `Ollama <https://ollama.ai>`_ running locally with the
``translategemma3-st`` model:

.. code-block:: bash

   ollama create translategemma3-st -f Modelfile

Indices
-------

* :ref:`genindex`
* :ref:`modindex`
