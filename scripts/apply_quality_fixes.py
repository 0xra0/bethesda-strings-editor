"""
apply_quality_fixes.py — Apply auto-fixable quality issues from a JSON report
to an xTranslator SST XML translation file.

Usage:
    python scripts/apply_quality_fixes.py <xml_file> <quality_report_json> [output_xml]

If output_xml is omitted the input file is overwritten (a .bak backup is kept).
"""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
# Make sure project root is on sys.path when run from any cwd
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from gui.quality_checker import QualityChecker, QualityIssue, QualityReport, AUTOFIX_CODES


# ── XML helpers ────────────────────────────────────────────────────────────────

def _parse_sid(s: str):
    if not s:
        return None
    try:
        return int(s, 16)
    except ValueError:
        try:
            return int(s)
        except ValueError:
            return None


def _load_xml(path: Path):
    """Return (root, id_to_dest_elem) where id_to_dest_elem maps sID→<Dest> element."""
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    root = ET.fromstring(raw)
    if root.tag != "SSTXMLRessources":
        raise ValueError(f"Not an SST XML file — root tag is '{root.tag}'")

    content = root.find("Content")
    if content is None:
        raise ValueError("No <Content> element in XML")

    id_to_dest: dict = {}
    for node in content.findall("String") or content.findall("Entry"):
        sid_str = node.get("sID") or node.get("ID") or node.get("id") or ""
        sid = _parse_sid(sid_str)
        if sid is None:
            continue
        dest_elem = node.find("Dest")
        if dest_elem is not None:
            id_to_dest[sid] = dest_elem

    return root, id_to_dest


def _write_xml(root: ET.Element, path: Path) -> None:
    tree = ET.ElementTree(root)
    # ET.indent adds pretty-printing (Python 3.9+); skip gracefully if not available
    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass
    tree.write(str(path), encoding="utf-8", xml_declaration=True)


# ── Report loading ─────────────────────────────────────────────────────────────

def _load_report(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError(f"Unsupported report version {data.get('version')}")
    reports = []
    for raw in data.get("reports", []):
        issues = [
            QualityIssue(
                severity=i.get("severity", "info"),
                code=i.get("code", ""),
                message=i.get("message", ""),
                detail=i.get("detail", ""),
            )
            for i in raw.get("issues", [])
        ]
        reports.append(
            QualityReport(
                row_index=raw.get("row_index", -1),
                string_id=raw.get("string_id", 0),
                original=raw.get("original", ""),
                translated=raw.get("translated", ""),
                issues=issues,
            )
        )
    return reports


# ── Main ───────────────────────────────────────────────────────────────────────

def main(xml_path: str, report_path: str, output_path: str | None = None) -> None:
    xml_p    = Path(xml_path)
    report_p = Path(report_path)
    out_p    = Path(output_path) if output_path else xml_p

    if not xml_p.exists():
        print(f"ERROR: XML file not found: {xml_p}", file=sys.stderr)
        sys.exit(1)
    if not report_p.exists():
        print(f"ERROR: Report file not found: {report_p}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading XML:    {xml_p}")
    root, id_to_dest = _load_xml(xml_p)
    print(f"  {len(id_to_dest)} <Dest> elements indexed")

    print(f"Loading report: {report_p}")
    reports = _load_report(report_p)
    print(f"  {len(reports)} reports")

    checker = QualityChecker(target_encoding="utf-8", target_language="Ukrainian",
                              source_language="Russian")

    fixed_count = 0
    skipped_no_elem = 0
    skipped_not_fixable = 0
    fix_log: list = []

    for report in reports:
        # Skip if no auto-fixable issue codes
        fixable_codes = {i.code for i in report.issues} & AUTOFIX_CODES
        if not fixable_codes:
            skipped_not_fixable += 1
            continue

        dest_elem = id_to_dest.get(report.string_id)
        if dest_elem is None:
            skipped_no_elem += 1
            continue

        current_text = dest_elem.text or ""
        fixed_text, applied = checker.auto_fix(report.original, current_text, report)

        if applied and fixed_text != current_text:
            dest_elem.text = fixed_text
            fixed_count += 1
            fix_log.append(
                f"  0x{report.string_id:08X} [{', '.join(applied)}]"
            )

    print(f"\nResults:")
    print(f"  Fixed:             {fixed_count}")
    print(f"  Not fixable:       {skipped_not_fixable}")
    print(f"  Missing in XML:    {skipped_no_elem}")

    if fixed_count == 0:
        print("\nNo changes to write.")
        return

    # Backup original if writing in-place
    if out_p == xml_p:
        bak = xml_p.with_suffix(xml_p.suffix + ".bak")
        import shutil
        shutil.copy2(xml_p, bak)
        print(f"\nBackup written:  {bak}")

    _write_xml(root, out_p)
    print(f"Fixed XML written: {out_p}")

    if fix_log:
        print(f"\nFix log ({min(len(fix_log), 50)} of {len(fix_log)}):")
        for line in fix_log[:50]:
            print(line)
        if len(fix_log) > 50:
            print(f"  … and {len(fix_log) - 50} more")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    main(
        sys.argv[1],
        sys.argv[2],
        sys.argv[3] if len(sys.argv) > 3 else None,
    )
