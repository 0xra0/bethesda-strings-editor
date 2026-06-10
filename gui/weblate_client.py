"""
Weblate REST API client and PO format converter for game string sync.

Push: converts the string table to PO format and uploads via the Weblate
REST API so community translators can work on a familiar web interface.

Pull: downloads the translated PO back and returns {string_id: translation}
so the caller can merge it into the StringTableModel.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

import requests as _http

logger = logging.getLogger(__name__)

_TIMEOUT_QUICK = 10
_TIMEOUT_LONG  = 60


# ── PO format helpers ──────────────────────────────────────────────────────────

def _po_escape(text: str) -> str:
    """Return a PO-quoted string literal (handles multi-line via \\n continuation)."""
    if not text:
        return '""'
    escaped = (
        text
        .replace('\\', '\\\\')
        .replace('"',  '\\"')
        .replace('\t', '\\t')
        .replace('\n', '\\n"\n"')
    )
    result = f'"{escaped}"'
    # If the escaped value ended with an empty continuation line, clean it up
    result = re.sub(r'"\n""$', '"', result)
    return result


def _po_unescape(raw: str) -> str:
    """Reconstruct the original string from a PO quoted value (may span lines)."""
    parts: List[str] = []
    for line in raw.strip().splitlines():
        line = line.strip()
        if line.startswith('"') and line.endswith('"'):
            parts.append(line[1:-1])
    return (
        ''.join(parts)
        .replace('\\n',  '\n')
        .replace('\\t',  '\t')
        .replace('\\"',  '"')
        .replace('\\\\', '\\')
    )


def strings_to_po(
    data: List[dict],
    source_lang: str = 'en',
    target_lang: str = 'uk',
    include_translations: bool = True,
) -> str:
    """
    Convert a StringTableModel._data list to PO format.

    Each string becomes one PO entry:
        msgctxt "0x001234AB"   — FormID as disambiguation key
        msgid   "English …"   — source text
        msgstr  "Ukrainian…"  — translation (empty when untranslated)

    Only strings with a non-empty original are emitted.
    """
    lines: List[str] = [
        'msgid ""',
        'msgstr ""',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
        f'"X-Source-Language: {source_lang}\\n"',
        f'"X-Target-Language: {target_lang}\\n"',
        '"Plural-Forms: nplurals=1; plural=0;\\n"',
        '',
    ]
    for row in data:
        original = (row.get('original') or '').strip()
        if not original:
            continue
        string_id  = row.get('id', 0)
        translated = (row.get('translated') or '') if include_translations else ''
        lines += [
            f'# 0x{string_id:08X}',
            f'msgctxt "0x{string_id:08X}"',
            f'msgid {_po_escape(original)}',
            f'msgstr {_po_escape(translated)}',
            '',
        ]
    return '\n'.join(lines)


def po_to_strings(po_content: str) -> Dict[int, str]:
    """
    Parse a PO file and return {string_id: translated_text}.

    Only entries whose msgctxt starts with '0x' and whose msgstr is
    non-empty are included.  The source (msgid) is ignored.
    """
    result: Dict[int, str] = {}

    # Split on blank-line separators between entries
    for entry in re.split(r'\n{2,}', po_content.strip()):
        # Skip header (msgid == "")
        if re.match(r'\s*msgid\s+""', entry):
            continue

        m_ctx = re.search(r'^msgctxt\s+((?:".*?"\n?)+)', entry, re.MULTILINE)
        if not m_ctx:
            continue
        ctx = _po_unescape(m_ctx.group(1))
        if not ctx.lower().startswith('0x'):
            continue
        try:
            string_id = int(ctx, 16)
        except ValueError:
            continue

        m_str = re.search(r'^msgstr\s+((?:".*?"\n?)+)', entry, re.MULTILINE)
        if not m_str:
            continue
        translated = _po_unescape(m_str.group(1))
        if translated:
            result[string_id] = translated

    return result


# ── Weblate REST client ────────────────────────────────────────────────────────

class WeblateError(Exception):
    pass


class WeblateClient:
    """
    Synchronous Weblate REST API client.

    Call from a worker thread — all methods block until the server responds.
    Authentication uses the ``Token`` scheme (Settings → API access key on
    your Weblate instance or https://hosted.weblate.org).
    """

    def __init__(self, base_url: str, api_token: str) -> None:
        self.base_url = base_url.rstrip('/')
        self._s = _http.Session()
        self._s.headers.update({
            'Authorization': f'Token {api_token}',
            'Accept': 'application/json',
            'User-Agent': 'BethesdaStringsEditor/1.0',
        })

    # ── Connectivity ───────────────────────────────────────────────────────────

    def test_connection(self) -> Tuple[bool, str]:
        """Return (success, human-readable message)."""
        try:
            r = self._s.get(f'{self.base_url}/api/', timeout=_TIMEOUT_QUICK)
            if r.status_code == 401:
                return False, 'Invalid API token (401 Unauthorized)'
            if r.status_code == 403:
                return False, 'Access denied (403 Forbidden)'
            if r.status_code == 200:
                version = r.json().get('version', '?')
                return True, f'Connected — Weblate {version}'
            return False, f'HTTP {r.status_code}: {r.reason}'
        except _http.exceptions.ConnectionError as exc:
            return False, f'Connection error: {exc}'
        except Exception as exc:
            return False, str(exc)

    # ── Statistics ─────────────────────────────────────────────────────────────

    def get_component_stats(self, project: str, component: str) -> List[dict]:
        """Per-language statistics for one component (paginated)."""
        results: List[dict] = []
        url: Optional[str] = (
            f'{self.base_url}/api/components/{project}/{component}/statistics/'
        )
        while url:
            r = self._s.get(url, timeout=_TIMEOUT_QUICK)
            r.raise_for_status()
            data = r.json()
            results.extend(data.get('results', []))
            url = data.get('next')
        return results

    # ── File transfer ──────────────────────────────────────────────────────────

    def download_po(self, project: str, component: str, language: str) -> str:
        """Download the current PO file for *language*. Returns raw PO text."""
        r = self._s.get(
            f'{self.base_url}/api/translations/{project}/{component}/{language}/file/',
            timeout=_TIMEOUT_LONG,
        )
        r.raise_for_status()
        return r.text

    def upload_po(
        self,
        project: str,
        component: str,
        language: str,
        po_content: str,
        overwrite: bool = False,
        method: str = 'translate',
    ) -> dict:
        """
        Upload *po_content* for *language*.

        *method* controls what Weblate does with existing strings:
          - ``translate``  — add new translations, leave existing untouched
          - ``add``        — add new strings only, never update
          - ``suggest``    — add as suggestions (requires human approval)
          - ``fuzzy``      — mark as needing review
          - ``replace``    — replace the entire translation file

        Returns the Weblate import result dict
        (keys: total, accepted, not_found, skipped, ...).
        """
        r = self._s.post(
            f'{self.base_url}/api/translations/{project}/{component}/{language}/file/',
            data={
                'overwrite': '1' if overwrite else '0',
                'method': method,
            },
            files={
                'file': (
                    'strings.po',
                    po_content.encode('utf-8'),
                    'text/x-gettext-translation',
                )
            },
            timeout=_TIMEOUT_LONG,
        )
        r.raise_for_status()
        return r.json()
