"""Async Ollama AI quality-check worker (qcgemma4-st model)."""
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple
from urllib import request as urllib_request
from urllib.error import URLError

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a quality checker for Bethesda Starfield Ukrainian game localization. "
    "Given a source string and its Ukrainian translation, detect quality issues.\n\n"
    "Issue codes to detect:\n"
    "  MISSING_TAG        — game format tag present in source but absent in translation\n"
    "  EXTRA_TAG          — tag in translation not present in source\n"
    "  SOURCE_LANGUAGE_LEAK — Russian characters (ы/э/ё/ъ) or Russian words in Ukrainian output\n"
    "  ENGLISH_LEAK       — untranslated English words remaining in Ukrainian output\n"
    "  UNTRANSLATED       — translation is identical to the source\n"
    "  EMPTY_TRANSLATION  — translation is empty or whitespace-only\n"
    "  SUSPICIOUSLY_SHORT — translation is <20% the length of the source\n"
    "  SUSPICIOUSLY_LONG  — translation is >500% the length of the source\n"
    "  AI_ARTIFACT        — AI commentary prefix (Note:, Translation:, Переклад:, etc.)\n"
    "  REPETITIVE_CONTENT — same phrase repeated 3+ times (hallucination)\n"
    "  MISSING_NEWLINES   — source has \\n but translation has none\n"
    "  NEWLINE_COUNT_MISMATCH — newline count differs between source and translation\n"
    "  MISSING_NUMBER     — standalone number in source absent from translation\n"
    "  LOW_UKRAINIAN_COVERAGE — too few recognized Ukrainian words (<25%)\n"
    "  CASE_MISMATCH      — source starts uppercase, translation starts lowercase\n"
    "  TRANSLATION_TRUNCATED — translation is a cut-off prefix of the source\n\n"
    "Output for clean translation:\n"
    "VERDICT: GOOD\n\n"
    "Output for problematic translation:\n"
    "VERDICT: ISSUES_FOUND\n"
    "CODES: CODE1, CODE2\n"
    "SEVERITY: error|warning|info\n"
    "DETAILS:\n"
    "- [CODE] description\n"
    "ACTION: AUTOFIX|RETRANSLATE"
)


def _call_ollama(ollama_url: str, model: str, source: str, translation: str) -> str:
    prompt = (
        "Check this Ukrainian translation:\n\n"
        f"Source (English):\n{source}\n\n"
        f"Translation (Ukrainian):\n{translation}"
    )
    body = json.dumps({
        "model": model,
        "system": _SYSTEM_PROMPT,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 1024},
    }).encode()
    req = urllib_request.Request(
        f"{ollama_url.rstrip('/')}/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib_request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())["response"]
    except URLError as exc:
        logger.warning("AI QC: Ollama unreachable — %s", exc)
        return ""
    except Exception as exc:
        logger.warning("AI QC call failed: %s", exc)
        return ""


class AiQcWorker(QThread):
    """Runs AI quality checks on a batch of translated strings via Ollama.

    Emits:
        result(row_index, issues)  — called for each row with detected issues
        progress(done, total)      — after each completed check
        finished()                 — when all checks are done or cancelled
    """

    result = Signal(int, list)   # (row_index, list[QualityIssue])
    progress = Signal(int, int)  # (done, total)
    finished = Signal()

    def __init__(
        self,
        items: List[Tuple[int, int, str, str]],  # (row_index, string_id, source, translation)
        ollama_url: str,
        model: str,
        max_workers: int = 4,
        parent=None,
    ):
        super().__init__(parent)
        self._items = items
        self._url = ollama_url
        self._model = model
        self._max_workers = max_workers
        self._done = 0
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        total = len(self._items)
        with ThreadPoolExecutor(max_workers=self._max_workers) as ex:
            future_to_row = {
                ex.submit(self._check_one, item): item[0]
                for item in self._items
            }
            for future in as_completed(future_to_row):
                if self._cancel:
                    ex.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    row_index, issues = future.result()
                    if issues:
                        self.result.emit(row_index, issues)
                except Exception as exc:
                    logger.warning("AI QC row error: %s", exc)
                self._done += 1
                self.progress.emit(self._done, total)
        self.finished.emit()

    def _check_one(self, item: Tuple[int, int, str, str]) -> Tuple[int, list]:
        from gui.quality_checker import QualityChecker

        row_index, _, source, translation = item
        response = _call_ollama(self._url, self._model, source, translation)
        issues = QualityChecker.parse_ai_verdict(response)
        return row_index, issues
