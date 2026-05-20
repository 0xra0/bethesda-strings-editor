Bethesda String File Format
===========================

Starfield (and earlier Creation Engine games) store localizable text in
three binary file types.  All multi-byte integers are **little-endian**.

File extensions
---------------

+----------------+---------------------+-------------------------------+
| Extension      | String termination  | Typical contents              |
+================+=====================+===============================+
| ``.strings``   | Null byte ``\x00``  | Short strings, names, labels  |
+----------------+---------------------+-------------------------------+
| ``.dlstrings`` | 4-byte length prefix| Dialogue subtitles, books     |
+----------------+---------------------+-------------------------------+
| ``.ilstrings`` | 4-byte length prefix| Interface / menu strings      |
+----------------+---------------------+-------------------------------+

All three share the same on-disk structure; only the string payload
format differs.

Layout
------

.. code-block:: text

   ┌─────────────────────────────────────────────┐
   │ Header (8 bytes)                            │
   │   uint32  count     — number of entries     │
   │   uint32  data_size — byte length of data   │
   ├─────────────────────────────────────────────┤
   │ Directory (count × 8 bytes)                 │
   │   uint32  string_id  — numeric ID           │
   │   uint32  rel_offset — offset from data start│
   ├─────────────────────────────────────────────┤
   │ Data section (data_size bytes)              │
   │   [string payloads; see below]              │
   └─────────────────────────────────────────────┘

String payloads
---------------

**.strings** — null-terminated
   Raw bytes followed by ``\x00``.  No length prefix.

**.dlstrings / .ilstrings** — length-prefixed
   A ``uint32`` length field (including the null terminator), followed by
   the string bytes and a ``\x00`` terminator.

   .. code-block:: text

      uint32  length      (= len(text_bytes) + 1)
      bytes   text_bytes  (UTF-8 or CP1251/CP1252)
      byte    0x00        (null terminator)

Encoding
--------

Bethesda files do not embed a charset marker.  The parser auto-detects
encoding in this order:

1. UTF-8 BOM (``\xEF\xBB\xBF``)
2. Valid UTF-8 decode
3. CP1251 heuristic (high proportion of Cyrillic code points)
4. CP1252 fallback

The detected encoding is exposed as ``BethesdaStringFile.encoding``.

String IDs
----------

String IDs are opaque ``uint32`` values assigned by the Creation Kit.
They are used by ESP/ESM records to reference strings in companion
``.strings`` files.  IDs are unique within a single file but may overlap
across files (the game disambiguates by file type and plugin load order).

Writing / round-tripping
------------------------

:meth:`BethesdaStringFile.save` rebuilds the directory and data sections
from scratch:

1. Re-encodes every ``StringDataObject.string_array`` with the target encoding.
2. Recalculates ``rel_offset`` values sequentially.
3. Recalculates ``data_size`` and ``count`` in the header.

The output is byte-for-byte equivalent to Creation Kit output for
unchanged strings; modified strings use the new byte lengths.

ESP / ESM plugin files
----------------------

ESP/ESM files are **not** string files.  They use a different record-based
binary format (the *Plugin File Format*).  The
:mod:`bethesda_strings.esp_handler` module handles a specific subset:

**Localized plugins** (flag bit ``0x80`` in the TES4 record flags) store
string IDs, not text — those IDs refer to companion ``.strings`` files.
:class:`EspFile` does **not** parse localized plugins; use
:class:`BethesdaStringFile` with the companion files instead.

**Non-localized plugins** store text directly in field buffers.  The
extractor scans every record for the field/record combinations listed in
``_FIELD_DEFS`` (derived from xEdit's ``_recorddefs.txt``), applies
per-record validation (e.g. GMST DATA fields must have an EDID starting
with ``s``), and returns a flat list of :class:`EspStringEntry` objects.

xTranslator SST XML format
---------------------------

The :class:`XMLHandler` reads and writes the SST XML format used by
`xTranslator <https://www.nexusmods.com/skyrimspecialedition/mods/134>`_:

.. code-block:: xml

   <SSTXMLRessources>
     <Content>
       <String sID="0000ABCD" Partial="0">
         <Source>Original English text</Source>
         <Dest>Translated text</Dest>
       </String>
     </Content>
   </SSTXMLRessources>

Matching strategy (mirrors xTranslator Pascal logic):

1. Match by ``sID`` hex value (primary).
2. Fall back to matching by ``<Source>`` text if ``sID`` is not found.
