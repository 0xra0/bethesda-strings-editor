"""
Translation memory: a pre-loaded dictionary of correct translations keyed by
string ID and source text.

Intended for reference files where a prior (human or assisted) translation
already exists.  OllamaWorker checks this before calling the model, so known
strings are never retranslated.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Matches the app's TXT export format:
#   {line_num} 0x{ID} "{Original}" "{Translated}"
_LINE_RE = re.compile(
    r'^\d+\s+0x([0-9A-Fa-f]+)\s+"((?:[^"\\]|\\.)*)"\s+"((?:[^"\\]|\\.)*)"$',
    re.MULTILINE,
)

_BACKSLASH_RE = re.compile(r'\\(.)')
_ESCAPE_MAP = {'n': '\n', 't': '\t', '"': '"', '\\': '\\'}


def _unescape(s: str) -> str:
    return _BACKSLASH_RE.sub(lambda m: _ESCAPE_MAP.get(m.group(1), m.group(1)), s)


class TranslationMemory:
    """
    In-memory map of string ID → correct translation text.

    Supports two loading modes:

    * Normal mode (``use_original=False``):
      Uses the "Translated" column.  Entries with empty "Translated" are skipped.

    * Reference mode (``use_original=True``):
      When "Translated" is empty, falls back to the "Original" column.
      Use this for reference files where the *source file* is already in the
      target language (e.g. the ``_ru.ILSTRINGS`` slot already holds Ukrainian
      text from a previous translation pass).
    """

    def __init__(self) -> None:
        self._by_id:  dict[int, str] = {}   # string_id → translation
        self._by_src: dict[str, str] = {}   # original_text → translation
        self.source_path: str = ""
        self.loaded_count: int = 0

    # ── Loading ───────────────────────────────────────────────────────────────

    def load(
        self,
        path: str | Path,
        use_original: bool = False,
    ) -> int:
        """
        Parse *path* and populate the memory.

        Returns the number of entries loaded.
        Merges with any previously loaded data (call :meth:`clear` first
        if you want a clean slate).
        """
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        self.source_path = str(path)
        count = 0

        for m in _LINE_RE.finditer(text):
            sid  = int(m.group(1), 16)
            orig = _unescape(m.group(2))
            trans = _unescape(m.group(3))

            if trans:
                self._by_id[sid]   = trans
                self._by_src[orig] = trans
                count += 1
            elif use_original and orig:
                # Reference-mode: "Original" already in target language
                self._by_id[sid] = orig
                count += 1

        self.loaded_count = len(self._by_id)
        return count

    def load_strings_file(self, path: str | Path) -> int:
        """Load a BethesdaStringFile (.strings/.dlstrings/.ilstrings) as a TM.

        String IDs map directly to translated text.  Skips empty entries.
        Returns the number of entries loaded.  Merges with existing data.
        """
        from bethesda_strings.core import BethesdaStringFile
        sf = BethesdaStringFile(str(path))
        count = 0
        for string_id, text in sf.strings.items():
            if text and text.strip():
                self._by_id[string_id] = text
                count += 1
        self.loaded_count = len(self._by_id)
        self.source_path = str(path)
        return count

    def clear(self) -> None:
        self._by_id.clear()
        self._by_src.clear()
        self.loaded_count = 0

    # ── Lookup ────────────────────────────────────────────────────────────────

    def get_by_id(self, string_id: int) -> str | None:
        """Return translation for *string_id*, or None if not found."""
        return self._by_id.get(string_id)

    def get_by_source(self, original: str) -> str | None:
        """Return translation for *original* source text, or None."""
        return self._by_src.get(original)

    def get_fuzzy(self, original: str, max_score: float = 3.0) -> Optional[str]:
        """Return the best fuzzy match for *original* from the memory source texts.

        Uses xTranslator's word-hash heuristic (gui.fuzzy_match).
        Returns None when no candidate scores below *max_score* or the
        fuzzy_match module is unavailable.

        Only called after get_by_id() and get_by_source() both return None.
        """
        if not self._by_src:
            return None
        try:
            from gui.fuzzy_match import best_fuzzy_match
        except ImportError:
            return None
        result = best_fuzzy_match(
            original,
            self._by_src.items(),
            max_score=max_score,
        )
        return result[0] if result else None

    # ── TMX support ───────────────────────────────────────────────────────────

    def load_tmx(
        self,
        path: str | Path,
        source_lang: str = "",
        target_lang: str = "",
    ) -> int:
        """Parse a TMX file and merge its translation units into memory.

        *source_lang* and *target_lang* are BCP-47 language tags (e.g. ``"ru"``,
        ``"uk"``, ``"en-US"``).  If either is empty the method picks the first
        two ``<tuv>`` elements in each ``<tu>`` as source and target respectively.

        Returns the number of new entries loaded.
        """
        path = Path(path)
        self.source_path = str(path)
        count = 0
        try:
            tree = ET.parse(path)
        except ET.ParseError as e:
            raise ValueError(f"Invalid TMX file: {e}") from e

        root = tree.getroot()
        # Strip namespace prefix if present
        def _tag(elem: ET.Element) -> str:
            t = elem.tag
            return t.split("}")[-1] if "}" in t else t

        src_lower = source_lang.lower()
        tgt_lower = target_lang.lower()

        for tu in root.iter():
            if _tag(tu) != "tu":
                continue
            tuvs: list[tuple[str, str]] = []  # (lang, seg_text)
            for tuv in tu:
                if _tag(tuv) != "tuv":
                    continue
                lang = (tuv.get("lang") or tuv.get("{http://www.w3.org/XML/1998/namespace}lang") or "").lower()
                seg = next((c for c in tuv if _tag(c) == "seg"), None)
                if seg is not None:
                    tuvs.append((lang, (seg.text or "").strip()))

            if len(tuvs) < 2:
                continue

            if src_lower and tgt_lower:
                src_text = next((t for l, t in tuvs if l.startswith(src_lower)), "")
                tgt_text = next((t for l, t in tuvs if l.startswith(tgt_lower)), "")
            else:
                src_text = tuvs[0][1]
                tgt_text = tuvs[1][1] if len(tuvs) > 1 else ""

            if src_text and tgt_text:
                self._by_src[src_text] = tgt_text
                count += 1

        self.loaded_count = len(self._by_id) + len(self._by_src)
        return count

    def export_tmx(
        self,
        path: str | Path,
        source_lang: str = "ru",
        target_lang: str = "uk",
        tool_name: str = "Bethesda Strings AI Translator",
    ) -> int:
        """Write the current source→translation pairs as a TMX 1.4b file.

        Returns the number of translation units written.
        """
        path = Path(path)
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        root = ET.Element("tmx", version="1.4")
        header = ET.SubElement(root, "header")
        header.set("creationtool", tool_name)
        header.set("creationtoolversion", "1.0")
        header.set("datatype", "plaintext")
        header.set("segtype", "sentence")
        header.set("adminlang", "en-US")
        header.set("srclang", source_lang)
        header.set("creationdate", now)

        body = ET.SubElement(root, "body")
        count = 0
        for src_text, tgt_text in sorted(self._by_src.items()):
            tu = ET.SubElement(body, "tu")
            tu.set("creationdate", now)

            tuv_src = ET.SubElement(tu, "tuv")
            tuv_src.set("{http://www.w3.org/XML/1998/namespace}lang", source_lang)
            ET.SubElement(tuv_src, "seg").text = src_text

            tuv_tgt = ET.SubElement(tu, "tuv")
            tuv_tgt.set("{http://www.w3.org/XML/1998/namespace}lang", target_lang)
            ET.SubElement(tuv_tgt, "seg").text = tgt_text

            count += 1

        ET.indent(root, space="  ")
        tree = ET.ElementTree(root)
        tree.write(path, encoding="utf-8", xml_declaration=True)
        return count

    def as_id_dict(self) -> dict[int, str]:
        """Return a copy of the ID→translation mapping."""
        return dict(self._by_id)

    def __len__(self) -> int:
        return len(self._by_id)

    def __bool__(self) -> bool:
        return bool(self._by_id)
