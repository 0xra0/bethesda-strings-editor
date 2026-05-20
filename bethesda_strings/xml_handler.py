"""
Handler for xTranslator SST XML files.

xTranslator XML format (XMLExportbase / XMLImportbase in TESVT_XMLFunc.pas)::

    <SSTXMLRessources>
      <Params>
        <Addon>Starfield.esm</Addon>
        <Source>Russian</Source>
        <Dest>Ukrainian</Dest>
        <Version>2</Version>
      </Params>
      <Content>
        <String List="0" sID="0012A5" [Partial="1"|"2"]>
          <EDID>SomeEditorId</EDID>
          <REC [id="N"] [idMax="M"]>DIAL:INFO</REC>
          <Source>Original text</Source>
          <Dest>Translated text</Dest>
        </String>
        ...
      </Content>
    </SSTXMLRessources>

Key format details (from Pascal source):

- Root: ``SSTXMLRessources``
- Entries: ``Content/String`` (NOT Entry, NOT Rec)
- List: 0=.strings 1=.dlstrings 2=.ilstrings
- sID: hex string, no 0x prefix, minimum 6 digits
- Partial: absent=translated, "1"=incompleteTrans, "2"=lockedTrans
- Source: original text in source language (primary match key in xTranslator)
- Dest:    translated text (the value we want)

Matching strategy (mirrors xTranslator behaviour):
  1. Match by sID (parsed as hex integer)
  2. Fallback: match by Source text (exact)
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SSTEntry:
    string_id: Optional[int]   # sID parsed as hex, or None
    source:    str              # <Source> text
    dest:      str              # <Dest> text
    list_idx:  int              # List attribute: 0/1/2
    partial:   int              # 0=translated, 1=incomplete, 2=locked


@dataclass
class SSTParseResult:
    """Result returned by XMLHandler.parse_sst_xml."""
    by_id:     dict[int, str]  = field(default_factory=dict)  # sID → dest
    by_source: dict[str, str]  = field(default_factory=dict)  # source → dest
    entries:   list[SSTEntry]  = field(default_factory=list)
    source_lang: str = ""
    dest_lang:   str = ""

    @property
    def count(self) -> int:
        return len(self.entries)


def _node_text(elem: Optional[ET.Element]) -> str:
    """Safe text extraction from an XML element (None-safe)."""
    if elem is None:
        return ""
    return (elem.text or "").replace("\r\n", "\n").replace("\r", "\n")


def _parse_sid(sid_str: str) -> Optional[int]:
    """Parse a sID attribute value (hex without 0x prefix)."""
    if not sid_str:
        return None
    try:
        return int(sid_str, 16)
    except ValueError:
        # Try decimal as fallback (some edge cases)
        try:
            return int(sid_str)
        except ValueError:
            return None


class XMLHandler:
    """Reads and writes xTranslator SST XML files."""

    # ── Parsing ───────────────────────────────────────────────────────────────

    @staticmethod
    def parse_sst_xml(file_path: str) -> SSTParseResult:
        """Parse an SST XML file.

        Returns an SSTParseResult with:
          .by_id     — sID (int) → Dest text  (for ID-based matching)
          .by_source — Source text → Dest text (for text-based matching)
          .entries   — full list of SSTEntry for further processing
        """
        result = SSTParseResult()
        path = Path(file_path)

        try:
            raw = path.read_bytes()
            # Strip UTF-8 BOM if present
            if raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]

            try:
                root = ET.fromstring(raw)
            except ET.ParseError as exc:
                raise ValueError(f"XML parse error: {exc}") from exc

            # Validate root element
            if root.tag != "SSTXMLRessources":
                raise ValueError(
                    f"Not an SST XML file — expected root 'SSTXMLRessources', "
                    f"got '{root.tag}'"
                )

            # Read Params
            params = root.find("Params")
            if params is not None:
                result.source_lang = _node_text(params.find("Source"))
                result.dest_lang   = _node_text(params.find("Dest"))

            # Locate entries — xTranslator: Content/String
            #                   our own export: Content/Entry (legacy)
            content = root.find("Content")
            if content is None:
                logger.warning("No <Content> node found in SST XML")
                return result

            string_nodes = content.findall("String") or content.findall("Entry")
            if not string_nodes:
                logger.warning("No <String> or <Entry> elements found under <Content>")
                return result

            for node in string_nodes:
                sid_attr = node.get("sID") or node.get("ID") or node.get("id") or ""
                list_attr = node.get("List", "0")
                partial_attr = node.get("Partial", "0")

                string_id = _parse_sid(sid_attr)
                try:
                    list_idx = int(list_attr)
                except ValueError:
                    list_idx = 0
                try:
                    partial = int(partial_attr) if partial_attr else 0
                except ValueError:
                    partial = 0

                source = _node_text(node.find("Source"))
                dest   = _node_text(node.find("Dest"))

                # Skip entries with no translation
                if not dest:
                    continue

                entry = SSTEntry(
                    string_id=string_id,
                    source=source,
                    dest=dest,
                    list_idx=list_idx,
                    partial=partial,
                )
                result.entries.append(entry)

                if string_id is not None:
                    result.by_id[string_id] = dest

                # Source-text map: only store if source is non-empty
                # (xTranslator's primary matching key)
                if source:
                    result.by_source[source] = dest

            logger.info(
                f"Parsed SST XML '{path.name}': {result.count} entries "
                f"({len(result.by_id)} with ID, {len(result.by_source)} with source text), "
                f"{result.source_lang} → {result.dest_lang}"
            )

        except Exception as exc:
            logger.error(f"Failed to parse SST XML '{file_path}': {exc}")
            raise

        return result

    # ── Writing ───────────────────────────────────────────────────────────────

    @staticmethod
    def write_sst_xml(
        file_path: str,
        data: List[Dict[str, Any]],
        source_lang: str = "Russian",
        dest_lang: str = "Ukrainian",
    ) -> None:
        """Write translations to an SST XML file in xTranslator format.

        Args:
            file_path:   output path
            data:        list of dicts with keys: id, original, translated, list_index
            source_lang: source language name
            dest_lang:   destination language name
        """
        try:
            root = ET.Element("SSTXMLRessources")

            params = ET.SubElement(root, "Params")
            ET.SubElement(params, "Addon").text  = ""
            ET.SubElement(params, "Source").text = source_lang
            ET.SubElement(params, "Dest").text   = dest_lang
            ET.SubElement(params, "Version").text = "2"

            content = ET.SubElement(root, "Content")

            for item in data:
                string_id = item.get("id")
                if string_id is None:
                    continue
                original   = item.get("original", "") or ""
                translated = item.get("translated", "") or ""
                list_idx   = item.get("list_index", 0)

                # xTranslator uses 'String' with 6-digit hex sID
                node = ET.SubElement(content, "String")
                node.set("List", str(list_idx))
                node.set("sID",  f"{string_id:06X}")
                ET.SubElement(node, "Source").text = original
                ET.SubElement(node, "Dest").text   = translated

            tree = ET.ElementTree(root)
            ET.indent(tree, space="  ")
            tree.write(file_path, encoding="utf-8", xml_declaration=True)

            logger.info(f"Wrote {len(data)} entries to SST XML '{file_path}'")

        except Exception as exc:
            logger.error(f"Failed to write SST XML '{file_path}': {exc}")
            raise
