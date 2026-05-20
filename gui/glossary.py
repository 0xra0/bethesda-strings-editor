"""
Terminology management: GlossaryEntry, Glossary, GlossaryManager.

Separate from TermProtector — this system manages prescribed translations
(what a term SHOULD be translated as) rather than protecting terms from
being sent to the AI.
"""
from __future__ import annotations

import csv
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

# Number of terms packed into each pre-compiled regex chunk.
# 500 gives ~40 passes for 20K terms instead of 20K individual regex calls.
_CHUNK_SIZE = 500
_WORD_BL = r"(?<![A-Za-zА-Яа-яІіЄєЇї])"
_WORD_BR = r"(?![A-Za-zА-Яа-яІіЄєЇї])"

logger = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────────────────────────


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# ── data classes ───────────────────────────────────────────────────────────────


@dataclass
class GlossaryEntry:
    source_term: str
    target_term: str
    category: str = ""
    definition: str = ""
    examples: List[str] = field(default_factory=list)
    notes: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid4())


# ── Glossary ───────────────────────────────────────────────────────────────────


class Glossary:
    """
    A single glossary — global or per-project.

    Entries are keyed by UUID so CRUD is O(1).  The file path is optional;
    glossaries can exist purely in memory.
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        label: str = "Global",
    ) -> None:
        self._path = path
        self.label = label
        self._entries: Dict[str, GlossaryEntry] = {}
        # Pre-compiled chunked patterns for fast multi-term search.
        # Rebuilt once after load/import; never rebuilt per search call.
        self._search_chunks: List[re.Pattern] = []
        self._term_lookup: Dict[str, GlossaryEntry] = {}  # lower_term → entry

        if path and path.exists():
            try:
                self.load_json(path)
            except Exception as exc:
                logger.error(f"Failed to load glossary from {path}: {exc}")

    # ── properties ─────────────────────────────────────────────────────────────

    @property
    def path(self) -> Optional[Path]:
        return self._path

    @path.setter
    def path(self, value: Optional[Path]) -> None:
        self._path = value

    @property
    def entries(self) -> List[GlossaryEntry]:
        return list(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[GlossaryEntry]:
        return iter(self._entries.values())

    # ── Search index ───────────────────────────────────────────────────────────

    def _rebuild_search_index(self) -> None:
        """Compile chunked regex patterns from current entries (one-time per load).

        Splits all terms into chunks of _CHUNK_SIZE and compiles one pattern per
        chunk.  A subsequent find_terms_in_text call runs ~40 finditer passes
        instead of 20,000 individual regex calls.
        """
        if not self._entries:
            self._search_chunks = []
            self._term_lookup = {}
            return

        # Longest terms first so a longer match shadows an overlapping shorter one
        pairs = sorted(
            [(e.source_term.strip(), e) for e in self._entries.values() if e.source_term.strip()],
            key=lambda x: len(x[0]),
            reverse=True,
        )
        self._term_lookup = {t.lower(): e for t, e in pairs}

        terms_lower = [t.lower() for t, _ in pairs]
        self._search_chunks = []
        for i in range(0, len(terms_lower), _CHUNK_SIZE):
            chunk = terms_lower[i : i + _CHUNK_SIZE]
            pat = _WORD_BL + "(?:" + "|".join(re.escape(t) for t in chunk) + ")" + _WORD_BR
            try:
                self._search_chunks.append(re.compile(pat))
            except re.error as exc:
                logger.warning("Skipping glossary chunk %d–%d: %s", i, i + len(chunk), exc)

        logger.debug(
            "Glossary search index built: %d terms → %d chunks",
            len(terms_lower),
            len(self._search_chunks),
        )

    # ── CRUD ───────────────────────────────────────────────────────────────────

    def add_entry(self, entry: GlossaryEntry, _rebuild: bool = True) -> None:
        self._entries[entry.id] = entry
        if _rebuild:
            self._rebuild_search_index()

    def update_entry(self, entry: GlossaryEntry) -> None:
        self._entries[entry.id] = entry
        self._rebuild_search_index()

    def remove_entry(self, entry_id: str) -> None:
        self._entries.pop(entry_id, None)
        self._rebuild_search_index()

    def remove_entries(self, ids: List[str]) -> None:
        for eid in ids:
            self._entries.pop(eid, None)
        self._rebuild_search_index()

    def get_entry(self, entry_id: str) -> Optional[GlossaryEntry]:
        return self._entries.get(entry_id)

    def clear(self) -> None:
        self._entries.clear()
        self._search_chunks = []
        self._term_lookup = {}

    # ── search ─────────────────────────────────────────────────────────────────

    def search(self, query: str) -> List[GlossaryEntry]:
        if not query:
            return self.entries
        q = query.lower()
        return [
            e
            for e in self._entries.values()
            if q in e.source_term.lower()
            or q in e.target_term.lower()
            or q in e.category.lower()
            or q in e.definition.lower()
        ]

    def filter_by_category(self, category: str) -> List[GlossaryEntry]:
        if not category:
            return self.entries
        return [e for e in self._entries.values() if e.category == category]

    def categories(self) -> List[str]:
        return sorted({e.category for e in self._entries.values() if e.category})

    def find_terms_in_text(self, text: str) -> List[Tuple[int, int, GlossaryEntry]]:
        """Return (start, end, entry) for each glossary term found in *text*.

        Uses pre-compiled chunked patterns (built at load time) instead of
        per-call regex compilation.  Word-boundary lookbehind/lookahead ensures
        "AI" doesn't match inside "MAIL".  Overlapping spans are skipped (longer
        terms, placed first in each chunk, shadow shorter sub-matches).
        """
        if not text or not self._search_chunks:
            return []

        text_lower = text.lower()
        results: List[Tuple[int, int, GlossaryEntry]] = []
        covered: List[Tuple[int, int]] = []

        for chunk in self._search_chunks:
            for m in chunk.finditer(text_lower):
                s, e = m.start(), m.end()
                if any(cs <= s < ce or cs < e <= ce for cs, ce in covered):
                    continue
                entry = self._term_lookup.get(m.group())
                if entry is not None:
                    covered.append((s, e))
                    results.append((s, e, entry))

        results.sort(key=lambda x: x[0])
        return results

    # ── JSON ───────────────────────────────────────────────────────────────────

    def load_json(self, path: Path) -> int:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        self._entries.clear()
        for item in data.get("entries", []):
            e = GlossaryEntry(
                source_term=item.get("source_term", ""),
                target_term=item.get("target_term", ""),
                category=item.get("category", ""),
                definition=item.get("definition", ""),
                examples=item.get("examples", []),
                notes=item.get("notes", ""),
                id=item.get("id") or str(uuid.uuid4()),
            )
            if e.source_term:
                self._entries[e.id] = e
        self._rebuild_search_index()
        return len(self._entries)

    def save_json(self, path: Optional[Path] = None) -> None:
        target = path or self._path
        if target is None:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        data = {"entries": [asdict(e) for e in self._entries.values()]}
        target.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"Saved {len(self._entries)} glossary entries to {target}")

    # ── CSV ────────────────────────────────────────────────────────────────────

    def import_csv(self, path: Path) -> int:
        """Import entries from CSV; returns count of imported entries."""
        count = 0
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                source = (
                    row.get("source_term") or row.get("Source") or ""
                ).strip()
                if not source:
                    continue
                target = (row.get("target_term") or row.get("Target") or "").strip()
                raw_ex = row.get("examples") or row.get("Examples") or ""
                e = GlossaryEntry(
                    source_term=source,
                    target_term=target,
                    category=(row.get("category") or row.get("Category") or "").strip(),
                    definition=(
                        row.get("definition") or row.get("Definition") or ""
                    ).strip(),
                    examples=[x.strip() for x in raw_ex.split("|") if x.strip()],
                    notes=(row.get("notes") or row.get("Notes") or "").strip(),
                )
                self._entries[e.id] = e
                count += 1
        logger.info(f"Imported {count} entries from {path}")
        self._rebuild_search_index()
        return count

    def export_csv(self, path: Path) -> None:
        fields = [
            "source_term",
            "target_term",
            "category",
            "definition",
            "examples",
            "notes",
        ]
        with path.open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for e in self._entries.values():
                writer.writerow(
                    {
                        "source_term": e.source_term,
                        "target_term": e.target_term,
                        "category": e.category,
                        "definition": e.definition,
                        "examples": " | ".join(e.examples),
                        "notes": e.notes,
                    }
                )
        logger.info(f"Exported {len(self._entries)} entries to {path}")

    # ── TBX ────────────────────────────────────────────────────────────────────

    _XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

    def import_tbx(self, path: Path) -> int:
        """Import entries from TBX (TermBase eXchange) XML."""
        from xml.etree import ElementTree as ET

        tree = ET.parse(path)
        root = tree.getroot()

        # Strip namespace prefixes so we can use bare tag names
        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]

        count = 0
        for concept in root.iter("conceptEntry"):
            source_term = target_term = definition = category = notes = ""
            examples: List[str] = []

            for d in concept.findall("descrip"):
                if d.get("type") == "subjectField" and d.text:
                    category = d.text.strip()

            for lang_sec in concept.findall("langSec"):
                lang = (
                    lang_sec.get(self._XML_LANG)
                    or lang_sec.get("xml:lang")
                    or ""
                )
                term_el = lang_sec.find(".//term")
                term_text = (term_el.text or "").strip() if term_el is not None else ""

                if lang.startswith("en"):
                    source_term = term_text
                    for d in lang_sec.iter("descrip"):
                        if d.get("type") == "definition" and d.text:
                            definition = d.text.strip()
                            break
                    for note in lang_sec.iter("note"):
                        if note.text:
                            notes = note.text.strip()
                            break
                else:
                    target_term = term_text
                    for d in lang_sec.iter("descrip"):
                        if d.get("type") == "example" and d.text:
                            examples.append(d.text.strip())

            if source_term:
                e = GlossaryEntry(
                    source_term=source_term,
                    target_term=target_term,
                    category=category,
                    definition=definition,
                    examples=examples,
                    notes=notes,
                )
                self._entries[e.id] = e
                count += 1

        logger.info(f"Imported {count} entries from TBX {path}")
        self._rebuild_search_index()
        return count

    def export_tbx(self, path: Path) -> None:
        """Export entries to TBX-Basic XML."""
        lines: List[str] = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<tbx type="TBX-Basic" style="dca" xml:lang="en"',
            '     xmlns="urn:iso:std:iso:30042:ed-2">',
            "  <tbxHeader>",
            "    <fileDesc>",
            "      <sourceDesc>",
            "        <p>Bethesda Strings Editor Glossary</p>",
            "      </sourceDesc>",
            "    </fileDesc>",
            "  </tbxHeader>",
            "  <text><body>",
        ]

        for i, entry in enumerate(self._entries.values(), 1):
            lines.append(f'    <conceptEntry id="c{i}">')
            if entry.category:
                lines.append(
                    f'      <descrip type="subjectField">'
                    f"{_xml_escape(entry.category)}</descrip>"
                )
            lines.append('      <langSec xml:lang="en">')
            if entry.definition:
                lines.append(
                    f"        <descripGrp>"
                    f'<descrip type="definition">'
                    f"{_xml_escape(entry.definition)}"
                    f"</descrip></descripGrp>"
                )
            lines.append(
                f"        <termSec>"
                f"<term>{_xml_escape(entry.source_term)}</term>"
            )
            if entry.notes:
                lines.append(f"          <note>{_xml_escape(entry.notes)}</note>")
            lines.append("        </termSec>")
            lines.append("      </langSec>")
            lines.append('      <langSec xml:lang="uk">')
            lines.append(
                f"        <termSec>"
                f"<term>{_xml_escape(entry.target_term)}</term>"
            )
            for ex in entry.examples:
                lines.append(
                    f'          <descrip type="example">'
                    f"{_xml_escape(ex)}</descrip>"
                )
            lines.append("        </termSec>")
            lines.append("      </langSec>")
            lines.append("    </conceptEntry>")

        lines += ["  </body></text>", "</tbx>"]
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Exported {len(self._entries)} entries to TBX {path}")


# ── Manager ────────────────────────────────────────────────────────────────────


class GlossaryManager:
    """
    Manages global + per-project glossaries with merged term lookup.

    Project entries take precedence over global ones for the same source term.
    """

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir
        global_path = config_dir / "glossary.json"
        self.global_glossary = Glossary(global_path, label="Global")
        self.project_glossary: Optional[Glossary] = None

    # ── project glossary ───────────────────────────────────────────────────────

    def load_project_glossary(self, source_file: Path) -> Glossary:
        """Return the per-project glossary for *source_file* (creates it if absent)."""
        proj_path = source_file.parent / (source_file.stem + ".glossary.json")
        self.project_glossary = Glossary(proj_path, label=source_file.name)
        return self.project_glossary

    def clear_project_glossary(self) -> None:
        self.project_glossary = None

    # ── merged lookup ──────────────────────────────────────────────────────────

    def find_terms_in_text(self, text: str) -> List[Tuple[int, int, GlossaryEntry]]:
        """Search both glossaries; project entries shadow global ones."""
        project_sources: set[str] = set()
        results: List[Tuple[int, int, GlossaryEntry]] = []

        if self.project_glossary:
            for hit in self.project_glossary.find_terms_in_text(text):
                results.append(hit)
                project_sources.add(hit[2].source_term.lower())

        for hit in self.global_glossary.find_terms_in_text(text):
            if hit[2].source_term.lower() not in project_sources:
                results.append(hit)

        results.sort(key=lambda x: x[0])
        return results

    def validate_translation(
        self, source: str, translation: str
    ) -> List[Tuple[GlossaryEntry, str]]:
        """Check prescribed translations.

        Returns (entry, "") for every glossary term whose target_term is absent
        from *translation*.  Only checks terms with a non-empty target.
        """
        hits = self.find_terms_in_text(source)
        issues: List[Tuple[GlossaryEntry, str]] = []
        trans_lower = (translation or "").lower()
        seen: set[str] = set()

        for _s, _e, entry in hits:
            key = entry.source_term.lower()
            if key in seen or not entry.target_term:
                continue
            seen.add(key)
            if entry.target_term.lower() not in trans_lower:
                issues.append((entry, ""))

        return issues

    def build_prompt_snippet(self, source: str) -> str:
        """Compact glossary lines for injection into the AI system prompt.

        Returns an empty string when no relevant terms are found.
        """
        hits = self.find_terms_in_text(source)
        if not hits:
            return ""

        seen: Dict[str, str] = {}
        for _s, _e, entry in hits:
            if entry.source_term not in seen and entry.target_term:
                seen[entry.source_term] = entry.target_term

        if not seen:
            return ""
        return "\n".join(f"  {src} → {tgt}" for src, tgt in seen.items())

    def all_entries(self) -> List[Tuple[str, GlossaryEntry]]:
        """All entries tagged with their scope: ``'global'`` or ``'project'``."""
        result: List[Tuple[str, GlossaryEntry]] = []
        for e in self.global_glossary.entries:
            result.append(("global", e))
        if self.project_glossary:
            for e in self.project_glossary.entries:
                result.append(("project", e))
        return result
