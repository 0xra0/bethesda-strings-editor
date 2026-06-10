"""
Claude API translation worker — same signal interface as OllamaWorker.

Drop-in replacement: when a Claude model is selected, MainWindow uses this
worker instead of OllamaWorker.  Signals are identical so all existing
progress/results plumbing in main_window.py works unchanged.
"""

from __future__ import annotations

import hashlib
import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import List

from PySide6.QtCore import QMutex, QMutexLocker, QObject, Signal, Slot

logger = logging.getLogger(__name__)


class ClaudeTranslationWorker(QObject):
    """
    Translates game strings using the Claude API.

    Emits the same four signals as OllamaWorker:
      translation_ready(index, text, string_id)
      progress(done, total)
      error(message)
      finished(success_count, error_count)

    The worker is designed to be moved to a QThread and receive
    translate_batch() calls via QueuedConnection, exactly like OllamaWorker.
    """

    translation_ready = Signal(int, str, object)  # object avoids signed-int overflow for FormIDs > 0x7FFFFFFF
    progress = Signal(int, int)
    error = Signal(str)
    finished = Signal(int, int)

    def __init__(
        self,
        api_key: str,
        model: str,
        source_lang: str = "ru",
        target_lang: str = "uk",
        max_workers: int = 5,
        term_protector=None,
        translation_cache=None,
        protect_named_entities: bool = False,
    ) -> None:
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.max_workers = max(1, max_workers)
        self.term_protector = term_protector
        self.translation_cache = translation_cache
        self.protect_named_entities = protect_named_entities
        self.glossary_manager = None
        self.skipped_types: list = []

        self._stop_flag = False
        self._mutex = QMutex()

        # Shared client — one connection pool reused across all worker threads.
        # Creating a new ClaudeClient per request was wasteful and broke prompt
        # caching (each new client has a fresh cache-write on the first call).
        from gui.claude_client import ClaudeClient
        self._claude = ClaudeClient(api_key, model)

    def stop(self) -> None:
        """Signal the worker to stop after the current request."""
        with QMutexLocker(self._mutex):
            self._stop_flag = True

    def update_config(self, **kwargs) -> None:
        """Accept the same kwargs as OllamaWorker.update_config() for compatibility."""
        for key, val in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, val)

    # ── Main translation slot ──────────────────────────────────────────────────

    @Slot(list)
    def translate_batch(self, requests: list) -> None:
        """Translate a batch of TranslationRequest objects using Claude."""
        if not requests:
            self.finished.emit(0, 0)
            return

        with QMutexLocker(self._mutex):
            self._stop_flag = False

        total = len(requests)
        done = 0
        success = 0
        errors = 0

        def _translate_one(req):
            with QMutexLocker(self._mutex):
                if self._stop_flag:
                    return req.index, None, req.string_id

            # Normalize CRLF/CR → LF (same as OllamaWorker) before tokenization.
            source_text = req.original_text.replace("\r\n", "\n").replace("\r", "\n")

            # Skip strings whose content type is in the configured skipped list.
            if self.skipped_types:
                from gui.string_type_detector import classify
                if classify(source_text).name in self.skipped_types:
                    return req.index, None, req.string_id

            # Check translation cache (keyed the same way as OllamaWorker's cache)
            cache_key = None
            if self.translation_cache:
                cache_key = hashlib.sha256(
                    f"{source_text}\x00{self.model}\x00"
                    f"{self.source_lang}\x00{self.target_lang}".encode()
                ).hexdigest()
                cached = self.translation_cache.get(cache_key)
                if cached:
                    return req.index, cached, req.string_id

            # Check translation memory
            if hasattr(self, "translation_memory") and self.translation_memory:
                tm_result = self.translation_memory.get(req.string_id)
                if tm_result:
                    return req.index, tm_result, req.string_id

            # Term protection
            protected = source_text
            token_map: dict = {}
            if self.term_protector and req.protected_terms_enabled:
                try:
                    from gui.term_protector import SOFT_CATEGORIES
                    exclude = [] if self.protect_named_entities else list(SOFT_CATEGORIES)
                    protected, token_map = self.term_protector.protect_text(
                        source_text, exclude_categories=exclude
                    )
                except Exception as exc:
                    logger.warning("Term protection failed: %s", exc)

            # Glossary snippet
            glossary_snippet = req.glossary_snippet
            if not glossary_snippet and self.glossary_manager:
                try:
                    glossary_snippet = self.glossary_manager.build_prompt_snippet(source_text)
                except Exception:
                    glossary_snippet = ""

            try:
                result = self._claude.translate(
                    text=protected,
                    source_lang=self.source_lang,
                    target_lang=self.target_lang,
                    retry_hint=req.retry_hint,
                    glossary_snippet=glossary_snippet,
                    context_note=req.context_note,
                )
            except Exception as exc:
                logger.error(
                    "Claude translation error index=%d string_id=0x%08X: %s",
                    req.index, req.string_id, exc,
                )
                return req.index, None, req.string_id

            # Restore protected terms
            if token_map and self.term_protector:
                try:
                    result = self.term_protector.restore(result, token_map)
                except Exception as exc:
                    logger.warning("Term restore failed: %s", exc)

            # Store in cache
            if cache_key and self.translation_cache:
                self.translation_cache.put(cache_key, result)

            return req.index, result, req.string_id

        # Parallel API calls — Claude allows concurrent requests
        # Default max_workers=5 is conservative; raise in settings for faster throughput
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures: List[Future] = [pool.submit(_translate_one, req) for req in requests]
            for fut in as_completed(futures):
                with QMutexLocker(self._mutex):
                    stopped = self._stop_flag
                if stopped:
                    pool.shutdown(wait=False, cancel_futures=True)
                    break

                try:
                    idx, result, string_id = fut.result()
                except Exception as exc:
                    errors += 1
                    self.error.emit(str(exc))
                    done += 1
                    self.progress.emit(done, total)
                    continue

                if result is not None:
                    self.translation_ready.emit(idx, result, string_id)
                    success += 1
                else:
                    errors += 1
                    self.error.emit(
                        self.tr("Translation failed for string index {idx}").format(idx=idx)
                    )

                done += 1
                self.progress.emit(done, total)

        self.finished.emit(success, errors)
