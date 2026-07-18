"""
Claude API translation worker — same signal interface as OllamaWorker.

Drop-in replacement: when a Claude model is selected, MainWindow uses this
worker instead of OllamaWorker.  Signals are identical so all existing
progress/results plumbing in main_window.py works unchanged.
"""

from __future__ import annotations

import hashlib
import logging
import re
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import List

from PySide6.QtCore import QMutex, QMutexLocker, QObject, Signal, Slot

logger = logging.getLogger(__name__)


def _close_unclosed_guillemets(text: str) -> str:
    """Append a closing » for every unclosed « on each line."""
    lines = text.split("\n")
    fixed = []
    for line in lines:
        missing = line.count("«") - line.count("»")
        if missing > 0:
            m = re.search(r'([.!?…]+)\s*$', line)
            if m:
                line = line[:m.start()] + "»" * missing + line[m.start():]
            else:
                line = line.rstrip() + "»" * missing
        fixed.append(line)
    return "\n".join(fixed)


def _restore_dropped_opening_brackets(translated: str, original: str) -> str:
    """Prepend missing [ when the model kept ] but dropped the opening [."""
    orig_lines = original.split("\n")
    trans_lines = translated.split("\n")
    fixed = []
    for i, line in enumerate(trans_lines):
        missing = line.count("]") - line.count("[")
        if missing > 0:
            orig_line = orig_lines[i] if i < len(orig_lines) else ""
            prefix = "[" * missing
            if orig_line.lstrip().startswith("["):
                stripped = line.lstrip()
                indent = line[: len(line) - len(stripped)]
                line = indent + prefix + stripped
            else:
                line = prefix + line
        fixed.append(line)
    return "\n".join(fixed)


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
    usage_ready = Signal(object)  # dict of real token usage (Claude Code CLI only)

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
        self.lore_rag_manager = None    # gui.lore_rag_manager.LoreRAGManager (optional)
        self.profile_manager = None     # bethesda_strings.character_profiles.ProfileManager (optional)
        self.profile_assignments = None # bethesda_strings.character_profiles.ProfileAssignments (optional)
        self.skipped_types: list = []
        # Attached by MainWindow after construction (the TM lives on the window so
        # it survives worker rebuilds).  Declared here so the lookup below reads a
        # real attribute rather than depending on the caller having set one.
        self.translation_memory = None  # Optional[gui.translation_memory.TranslationMemory]
        self.tm_fuzzy_max_score: float = 3.0

        self._stop_flag = False
        self._mutex = QMutex()

        # Shared client — one connection pool reused across all worker threads.
        # Creating a new ClaudeClient per request was wasteful and broke prompt
        # caching (each new client has a fresh cache-write on the first call).
        # A ``claude-code:*`` model selects the subscription-backed CLI client
        # (no API billing); anything else uses the metered Anthropic API client.
        from gui.claude_code_client import is_claude_code_model
        if is_claude_code_model(model):
            from gui.claude_code_client import ClaudeCodeClient
            self._claude = ClaudeCodeClient(api_key, model)
        else:
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

        # Claude Code CLI reports real per-call token usage — start each batch
        # from zero so the totals emitted at the end reflect only this batch.
        if hasattr(self._claude, "reset_usage"):
            self._claude.reset_usage()

        # Warm only the word lists this language pair can consult (quality
        # checks, English-leak detection); loading all of them costs hundreds
        # of MB on languages the session never touches.
        from gui.ollama_worker import preload_language_dictionaries
        preload_language_dictionaries(self.source_lang, self.target_lang)

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

            # Retranslation requests (retry_hint from a failed QC pass, or an AI-fix)
            # MUST reach the model — returning a cached/TM hit would hand back the exact
            # flawed string the retry is trying to replace, silently defeating it.
            is_retry = bool(req.retry_hint) or bool(req.fix_translation)

            # Check translation cache (keyed the same way as OllamaWorker's cache).
            # Use `is not None`, not truthiness: TranslationCache defines __len__, so an
            # empty cache is falsy — `if self.translation_cache:` would skip both read
            # and write and the cache could never populate.
            cache_key = None
            if self.translation_cache is not None:
                cache_key = hashlib.sha256(
                    f"{source_text}\x00{self.model}\x00"
                    f"{self.source_lang}\x00{self.target_lang}".encode()
                ).hexdigest()
                if not is_retry:
                    cached = self.translation_cache.get(cache_key)
                    if cached:
                        return req.index, cached, req.string_id

            # Check translation memory (skipped on retry for the same reason).
            # Same lookup order as OllamaWorker: id, then source text, then fuzzy.
            # There is no TranslationMemory.get() — calling it raised AttributeError
            # out of this function and failed every string in the batch.  Looking up
            # by id alone was also not enough: a memory mined by the Official-TM
            # miner or imported from TMX is keyed purely by source text.
            tm = self.translation_memory
            if not is_retry and tm:
                mem_hit = tm.get_by_id(req.string_id)
                if not mem_hit:
                    mem_hit = tm.get_by_source(source_text)
                if not mem_hit:
                    mem_hit = tm.get_fuzzy(
                        source_text, max_score=self.tm_fuzzy_max_score
                    )
                if mem_hit:
                    return req.index, mem_hit, req.string_id

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

            # Lore RAG context
            lore_snippet = req.lore_snippet
            if not lore_snippet and self.lore_rag_manager:
                try:
                    lore_snippet = self.lore_rag_manager.get_snippet(source_text)
                except Exception:
                    lore_snippet = ""

            # Character profile
            profile = req.character_profile
            if profile is None and self.profile_assignments and self.profile_manager:
                pid = self.profile_assignments.get(req.string_id)
                if pid:
                    profile = self.profile_manager.get(pid)

            try:
                result = self._claude.translate(
                    text=protected,
                    source_lang=self.source_lang,
                    target_lang=self.target_lang,
                    retry_hint=req.retry_hint,
                    glossary_snippet=glossary_snippet,
                    lore_snippet=lore_snippet,
                    context_note=req.context_note,
                    character_profile=profile,
                )
            except Exception as exc:
                logger.error(
                    "Claude translation error index=%d string_id=0x%08X: %s",
                    req.index, req.string_id, exc,
                )
                return req.index, None, req.string_id

            # Restore protected terms. The method is restore_text() — there is no
            # restore(); passing the protected source as the template preserves
            # whitespace/paragraph structure exactly (same as OllamaWorker).
            if token_map and self.term_protector:
                try:
                    result = self.term_protector.restore_text(result, token_map, protected)
                except Exception as exc:
                    logger.warning("Term restore failed: %s", exc)

            # Close any unclosed «guillemets left open by the model
            result = _close_unclosed_guillemets(result)
            # Restore [ dropped by the model when ] was kept
            result = _restore_dropped_opening_brackets(result, req.original_text)

            # Store in cache (also on retry — the corrected result should replace the
            # stale entry). `is not None` for the same __len__-falsy reason as above.
            # The write method is set(), not put() (matches OllamaWorker).
            if cache_key and self.translation_cache is not None:
                self.translation_cache.set(cache_key, result)

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

        # Report real token usage (Claude Code CLI) before signalling completion,
        # so the finished handler can display the actual totals for this batch.
        if hasattr(self._claude, "get_usage"):
            try:
                usage = self._claude.get_usage()
                if usage.get("calls"):
                    self.usage_ready.emit(usage)
            except Exception:
                pass

        self.finished.emit(success, errors)
