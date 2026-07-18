"""
Translation memory: a pre-loaded dictionary of correct translations keyed by
string ID and source text.

Intended for reference files where a prior (human or assisted) translation
already exists.  OllamaWorker checks this before calling the model, so known
strings are never retranslated.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

_SNAPSHOT_VERSION = 1

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
        # Lazily built candidate pre-filter for get_fuzzy(); rebuilt on demand
        # after any write to _by_src.  None = needs rebuilding.  Guarded by a
        # lock because get_fuzzy() runs on every translation worker thread at
        # once: without it, a batch start has ten threads each building their
        # own copy of the same index before any of them can use one.
        self._fuzzy_index = None  # Optional[gui.fuzzy_match.FuzzyIndex]
        self._fuzzy_lock = threading.Lock()

    def _invalidate_fuzzy_index(self) -> None:
        """Drop the fuzzy pre-filter after a write to the source-keyed map."""
        with self._fuzzy_lock:
            self._fuzzy_index = None

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

        self._invalidate_fuzzy_index()
        self.loaded_count = len(self)
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
        self.loaded_count = len(self)
        self.source_path = str(path)
        return count

    def add_pairs(
        self,
        pairs: Iterable[tuple[str, str]],
        *,
        prefer_existing: bool = False,
    ) -> int:
        """Merge ``(source, translation)`` pairs into the source-keyed memory.

        Used by the Official-TM miner to fold Bethesda's canonical
        source→target renderings straight into the memory (no string IDs — the
        official TM is matched by source text).  Empty sides are skipped.

        When *prefer_existing* is True an already-loaded source keeps its current
        translation (so a hand-curated entry is never overwritten by mined data);
        otherwise the incoming pair wins.  Returns the number of new sources added.
        """
        added = 0
        for src, tgt in pairs:
            if not src or not tgt:
                continue
            if src in self._by_src:
                if not prefer_existing:
                    self._by_src[src] = tgt
                continue
            self._by_src[src] = tgt
            added += 1
        self._invalidate_fuzzy_index()
        self.loaded_count = len(self)
        return added

    def clear(self) -> None:
        self._by_id.clear()
        self._by_src.clear()
        self._invalidate_fuzzy_index()
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

        A :class:`~gui.fuzzy_match.FuzzyIndex` narrows the candidate pool first.
        Scoring every entry is seconds per lookup once the memory reaches the
        size the Official-TM miner produces, and this is the path taken by every
        string that misses both exact lookups.  The pre-filter is sound (see
        FuzzyIndex), so the result is identical to scanning the whole memory.
        """
        if not self._by_src:
            return None
        try:
            from gui.fuzzy_match import FuzzyIndex, best_fuzzy_match
        except ImportError:
            return None

        with self._fuzzy_lock:
            index = self._fuzzy_index
            if index is None:
                index = self._fuzzy_index = FuzzyIndex(self._by_src)

        # One dict lookup per candidate, tolerating a miss: the index may name a
        # source that a concurrent clear() or reload has since dropped, and this
        # runs on every translation worker thread at once.  A subscript (even
        # behind an `in` test) would raise KeyError in that window.
        by_src = self._by_src
        candidates = []
        for src in index.candidates(original):
            translation = by_src.get(src)
            if translation:
                candidates.append((src, translation))
        if not candidates:
            return None

        result = best_fuzzy_match(
            original,
            candidates,
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

        self._invalidate_fuzzy_index()
        self.loaded_count = len(self)
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

    def entries(self) -> list[tuple[str, str, str]]:
        """All (id_hex, source, translation) rows for the browser dialog.

        ID-keyed entries whose source text is also known are merged; source-only
        entries (from TMX) get a blank ID. Sorted by source text.
        """
        # Reverse map translation→source is ambiguous; instead expose both views.
        src_by_trans: dict[str, str] = {}
        for src, tr in self._by_src.items():
            src_by_trans.setdefault(tr, src)
        rows: list[tuple[str, str, str]] = []
        seen_src: set[str] = set()
        for sid, tr in self._by_id.items():
            src = src_by_trans.get(tr, "")
            if src:
                seen_src.add(src)
            rows.append((f"0x{sid:08X}", src, tr))
        for src, tr in self._by_src.items():
            if src not in seen_src:
                rows.append(("", src, tr))
        rows.sort(key=lambda r: (r[1] or r[2]).lower())
        return rows

    # ── JSON snapshot persistence ───────────────────────────────────────────────

    def save_snapshot(self, path: str | Path) -> None:
        """Persist the memory as a compact JSON snapshot (id + source maps).

        Lets a loaded TM survive across sessions without re-importing the source
        TXT/TMX every launch (mirrors how the glossary persists).
        """
        path = Path(path)
        data = {
            "version": _SNAPSHOT_VERSION,
            "source_path": self.source_path,
            # JSON keys must be strings — store ids as hex.
            "by_id": {f"{k:x}": v for k, v in self._by_id.items()},
            "by_src": self._by_src,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def load_snapshot(self, path: str | Path) -> int:
        """Load a JSON snapshot written by :meth:`save_snapshot`. Merges.

        Returns the number of ID-keyed entries after loading. Returns 0 and logs
        (never raises) if the file is missing or malformed.
        """
        path = Path(path)
        if not path.exists():
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for k, v in (data.get("by_id") or {}).items():
                if isinstance(v, str):
                    self._by_id[int(k, 16)] = v
            for k, v in (data.get("by_src") or {}).items():
                if isinstance(v, str):
                    self._by_src[k] = v
            self.source_path = data.get("source_path", "") or self.source_path
        except (ValueError, OSError, TypeError) as exc:
            logger.warning("Failed to load TM snapshot %s: %s", path, exc)
            return 0
        self._invalidate_fuzzy_index()
        self.loaded_count = len(self)
        return len(self)

    def __len__(self) -> int:
        """Number of entries the memory can resolve.

        Both indexes count.  ``len(self._by_id)`` alone reported 0 for a memory
        loaded purely from TMX or mined by the Official-TM miner — both are
        source-keyed — which silently disabled every feature gated on the size
        or truthiness of the TM (worker attachment, the lookup gate in both
        translation backends, snapshot-on-exit, the browser, the indicator).

        ``load()`` fills both maps for one logical entry, so the maximum — not
        the sum — is the entry count.  It under-reports only when ID-keyed and
        source-keyed entries were loaded from *different* files (e.g. a
        ``.strings`` TM plus mined official pairs); it never reports zero for a
        non-empty memory, which is the failure that mattered.
        """
        return max(len(self._by_id), len(self._by_src))

    def __bool__(self) -> bool:
        return bool(self._by_id or self._by_src)
