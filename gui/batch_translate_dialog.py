"""
Batch AI Translation dialog — scans a folder of binary string files,
auto-fixes mechanical issues, and sends untranslated/poor-quality strings
to Ollama for AI retranslation.

Typical workflow:
  1. Point UK folder at your translated binary files (_uk.strings etc.)
  2. Optionally point RU folder at the source language files (_ru.strings etc.)
  3. Choose what to fix: auto-fix, untranslated strings, Russian leaks
  4. Click Start — files are processed file by file, changes saved in place
"""

import logging
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests as _requests
from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)

# ── File discovery helpers ────────────────────────────────────────────────────

_BINARY_EXTS = frozenset({".strings", ".dlstrings", ".ilstrings"})

_UK_SUFFIXES = ("_uk_translated", "_uk")
_RU_SUFFIXES = ("_ru",)


def _module_key(path: str) -> Tuple[str, str]:
    """Return (base_module, ext) for a binary string file path."""
    name = os.path.basename(path).lower()
    base, _, ext_raw = name.rpartition(".")
    ext = ext_raw if ext_raw else ""
    for suf in _UK_SUFFIXES + _RU_SUFFIXES:
        if base.endswith(suf):
            base = base[: -len(suf)]
            break
    return base, ext


def _find_binary_files(folder: str) -> List[str]:
    """Recursively find all binary string files in *folder*."""
    result = []
    for root, _, files in os.walk(folder):
        for fn in sorted(files):
            if os.path.splitext(fn)[1].lower() in _BINARY_EXTS:
                result.append(os.path.join(root, fn))
    return sorted(result)


# ── Ollama API helper ──────────────────────────────────────────────────────────

_CHUNK_TIMEOUT = 300


def _call_ollama(
    session: _requests.Session,
    api_url: str,
    model: str,
    text: str,
    source_lang: str,
    target_lang: str,
    retry_hint: str = "",
) -> Optional[str]:
    """Call Ollama and return the translated text, or None on failure."""
    from gui.ollama_worker import TranslationRequest
    req = TranslationRequest(
        index=0,
        original_text=text,
        string_id=0,
        source_lang=source_lang,
        target_lang=target_lang,
        retry_hint=retry_hint,
    )

    input_len = len(text)
    for ctx in (4096, 8192, 16384, 32768):
        if input_len // 3 * 2 + 512 <= ctx:
            num_ctx = ctx
            break
    else:
        num_ctx = 32768

    num_predict = min(4096, max(200, input_len * 2))

    prompt = req.to_prompt()
    system = req.to_system_prompt()

    payload = {
        "model":  model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "keep_alive": -1,
        "options": {
            "temperature": 0.1,
            "num_predict": num_predict,
            "num_ctx": num_ctx,
            "top_k": 40,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
            "stop": ["<end_of_turn>", "<start_of_turn>", "user:", "model:"],
        },
    }

    resp = session.post(
        f"{api_url}/api/generate",
        json=payload,
        timeout=_CHUNK_TIMEOUT,
    )
    resp.raise_for_status()
    result = resp.json().get("response", "").strip()
    return result or None


# ── Worker thread ──────────────────────────────────────────────────────────────

class BatchTranslateWorker(QThread):
    """
    Background thread that processes a folder of binary string files.

    For each file it:
      1. Loads the UK (translated) binary file
      2. Loads the corresponding RU (source) binary file (if a RU folder is given)
      3. Builds row dicts for QualityChecker
      4. Optionally auto-fixes mechanical issues
      5. Collects strings that need AI translation
      6. Calls Ollama in parallel (ThreadPoolExecutor)
      7. Applies translations and saves the binary file
    """

    # (done_strings, total_strings, current_filename)
    progress = Signal(int, int, str)
    # Plain text log line
    log_message = Signal(str)
    # Dict with summary info when processing is complete
    finished = Signal(dict)

    def __init__(
        self,
        uk_folder: str,
        ru_folder: str,
        source_lang: str,
        target_lang: str,
        api_url: str,
        model: str,
        max_workers: int,
        do_autofix: bool,
        do_translate_untranslated: bool,
        do_retranslate_leaks: bool,
        terms_file: str,
        parent=None,
    ):
        super().__init__(parent)
        self.uk_folder = uk_folder
        self.ru_folder = ru_folder
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.api_url = api_url.rstrip("/")
        self.model = model
        self.max_workers = max(1, max_workers)
        self.do_autofix = do_autofix
        self.do_translate_untranslated = do_translate_untranslated
        self.do_retranslate_leaks = do_retranslate_leaks
        self.terms_file = terms_file
        self._stop = False

    @Slot()
    def stop(self):
        self._stop = True

    def run(self):
        from bethesda_strings.core import BethesdaStringFile
        from gui.quality_checker import QualityChecker

        t_start = time.time()
        summary: Dict = {
            "files_total":    0,
            "files_changed":  0,
            "auto_fixed":     0,
            "ai_translated":  0,
            "ai_errors":      0,
            "elapsed_sec":    0,
        }

        self._log("Scanning folder for binary string files…")
        uk_files = _find_binary_files(self.uk_folder)
        if not uk_files:
            self._log("No binary string files found in the selected folder.")
            self.finished.emit(summary)
            return

        # Build RU lookup: module_key → {string_id: text}
        ru_lookup: Dict[Tuple[str, str], Dict[int, str]] = {}
        if self.ru_folder and os.path.isdir(self.ru_folder):
            self._log(f"Loading source-language files from {self.ru_folder} …")
            for ru_path in _find_binary_files(self.ru_folder):
                fn = os.path.basename(ru_path).lower()
                if not any(fn.find(s) >= 0 for s in _RU_SUFFIXES):
                    continue  # skip uk/nexus files if mixed in the same folder
                key = _module_key(ru_path)
                try:
                    bf = BethesdaStringFile(ru_path)
                    ru_lookup[key] = {
                        s.string_id: s.get_string("utf-8", "replace")
                        for s in bf.strings
                    }
                except Exception as exc:
                    self._log(f"  Warning: could not load {os.path.basename(ru_path)}: {exc}")
            self._log(f"  Loaded {len(ru_lookup)} source-language file(s).")

        # Load term protector once
        term_protector = None
        if self.terms_file and os.path.isfile(self.terms_file):
            try:
                from gui.term_protector import TermProtector
                term_protector = TermProtector(game_terms_file=Path(self.terms_file))
                n_terms = len(term_protector.protected_terms)
                self._log(f"Term protection active ({n_terms} terms).")
            except Exception as exc:
                self._log(f"Warning: could not load terms file: {exc}")

        # Translation cache
        from gui.translation_cache import TranslationCache
        cache = TranslationCache()

        checker = QualityChecker(
            target_encoding="utf-8",
            target_language=self.target_lang,
            source_language=self.source_lang,
        )

        # HTTP session shared across all files
        session = _requests.Session()
        adapter = _requests.adapters.HTTPAdapter(
            pool_connections=self.max_workers + 2,
            pool_maxsize=self.max_workers + 2,
            max_retries=2,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        summary["files_total"] = len(uk_files)
        for file_idx, uk_path in enumerate(uk_files):
            if self._stop:
                self._log("Stopped by user.")
                break

            fn = os.path.basename(uk_path)
            self.progress.emit(file_idx, len(uk_files), fn)
            self._log(f"\n[{file_idx + 1}/{len(uk_files)}] {fn}")

            try:
                changed, fixed, translated, errors = self._process_file(
                    uk_path=uk_path,
                    ru_lookup=ru_lookup,
                    checker=checker,
                    term_protector=term_protector,
                    cache=cache,
                    session=session,
                )
            except Exception as exc:
                self._log(f"  ERROR: {exc}")
                logger.exception("Error processing %s", uk_path)
                continue

            if changed:
                summary["files_changed"] += 1
            summary["auto_fixed"] += fixed
            summary["ai_translated"] += translated
            summary["ai_errors"] += errors

        summary["elapsed_sec"] = time.time() - t_start
        self.progress.emit(len(uk_files), len(uk_files), "Done")
        self.finished.emit(summary)

    def _process_file(
        self,
        uk_path: str,
        ru_lookup: Dict[Tuple[str, str], Dict[int, str]],
        checker,
        term_protector,
        cache,
        session: _requests.Session,
    ) -> Tuple[bool, int, int, int]:
        """Process one UK binary file. Returns (changed, auto_fixed, ai_translated, ai_errors)."""
        from bethesda_strings.core import BethesdaStringFile

        bf = BethesdaStringFile(uk_path)

        # Find matching RU data
        key = _module_key(uk_path)
        ru_data: Dict[int, str] = ru_lookup.get(key, {})

        if not ru_data and self.ru_folder:
            self._log(f"  No RU source found for {key[0]}.{key[1]} — auto-fix only.")

        # Build row dicts for QualityChecker
        rows = []
        for s in bf.strings:
            try:
                uk_text = s.get_string("utf-8", "replace")
            except Exception:
                uk_text = ""
            ru_text = ru_data.get(s.string_id, uk_text)
            rows.append({
                "id":         s.string_id,
                "original":   ru_text,
                "translated": uk_text,
            })

        # ── Phase 1: quality check ─────────────────────────────────────────────
        reports = checker.check_all(rows)
        if not reports:
            self._log("  OK — no issues found.")
            return False, 0, 0, 0

        issue_codes = set()
        for r in reports:
            for i in r.issues:
                issue_codes.add(i.code)

        n_issues = len(reports)
        self._log(f"  {n_issues} string(s) with issues: {', '.join(sorted(issue_codes))}")

        auto_fixed = 0
        ai_translated = 0
        ai_errors = 0
        changed = False

        # ── Phase 2: auto-fix ──────────────────────────────────────────────────
        if self.do_autofix:
            fix_results = checker.fix_all(rows, reports)
            for row_idx, fixed_text, _applied in fix_results:
                s = bf.strings[row_idx]
                try:
                    s.set_string(fixed_text, "utf-8")
                    rows[row_idx]["translated"] = fixed_text
                    changed = True
                    auto_fixed += 1
                except Exception as exc:
                    logger.warning("Could not write auto-fix for %s: %s", s.string_id, exc)
            if auto_fixed:
                self._log(f"  Auto-fixed {auto_fixed} string(s).")
                # Re-run to get updated report list
                reports = checker.check_all(rows)

        # ── Phase 3: AI translation ────────────────────────────────────────────
        if not (self.do_translate_untranslated or self.do_retranslate_leaks):
            if changed:
                self._save(bf, uk_path)
            return changed, auto_fixed, ai_translated, ai_errors

        # Collect strings for AI
        ai_tasks: List[Tuple[int, str, str, str]] = []  # (row_idx, ru_text, uk_text, hint)
        for report in reports:
            row_idx = report.row_index
            codes = {i.code for i in report.issues}
            ru_text = rows[row_idx]["original"]
            uk_text = rows[row_idx]["translated"]

            wants_translate = self.do_translate_untranslated and (
                "UNTRANSLATED" in codes or "EMPTY_TRANSLATION" in codes
            )
            wants_retranslate = self.do_retranslate_leaks and (
                "SOURCE_LANGUAGE_LEAK" in codes or "LOW_UKRAINIAN_COVERAGE" in codes
            )

            if not (wants_translate or wants_retranslate):
                continue
            if not ru_text or not ru_text.strip():
                continue

            retry_hint = ""
            if wants_retranslate and not wants_translate:
                from gui.quality_checker import QualityChecker as QC
                retry_hint = QC.build_retry_hint(report.issues)

            ai_tasks.append((row_idx, ru_text, uk_text, retry_hint))

        if not ai_tasks:
            if changed:
                self._save(bf, uk_path)
            return changed, auto_fixed, ai_translated, ai_errors

        self._log(f"  Sending {len(ai_tasks)} string(s) to Ollama…")

        def _translate_task(args):
            row_idx, ru_text, _uk_text, retry_hint = args
            if self._stop:
                return row_idx, None, "stopped"
            # Check cache first
            import hashlib
            cache_key = hashlib.sha256(
                f"{ru_text}\x00{self.model}\x00{self.source_lang}\x00{self.target_lang}".encode()
            ).hexdigest()
            cached = cache.get(cache_key)
            if cached:
                return row_idx, cached, "cache"

            # Apply term protection
            protected = ru_text
            token_map = {}
            if term_protector:
                try:
                    protected, token_map = term_protector.protect(ru_text)
                except Exception:
                    protected = ru_text
                    token_map = {}

            try:
                result = _call_ollama(
                    session=session,
                    api_url=self.api_url,
                    model=self.model,
                    text=protected,
                    source_lang=self.source_lang,
                    target_lang=self.target_lang,
                    retry_hint=retry_hint,
                )
                if result is None:
                    return row_idx, None, "empty"

                # Restore protected terms
                if token_map and term_protector:
                    try:
                        result = term_protector.restore(result, token_map)
                    except Exception:
                        pass

                # Cache the result
                cache.put(cache_key, result)
                return row_idx, result, "ok"
            except Exception as exc:
                return row_idx, None, str(exc)

        ok_count = 0
        err_count = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            futures: List[Future] = [pool.submit(_translate_task, t) for t in ai_tasks]
            for fut in as_completed(futures):
                if self._stop:
                    pool.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    row_idx, result, status = fut.result()
                except Exception as exc:
                    self._log(f"  Future error: {exc}")
                    err_count += 1
                    continue

                if result is None:
                    err_count += 1
                    if status not in ("stopped", "empty"):
                        self._log(f"  Translation error (row {row_idx}): {status[:80]}")
                    continue

                # Write result back to the binary object
                try:
                    bf.strings[row_idx].set_string(result, "utf-8")
                    rows[row_idx]["translated"] = result
                    ok_count += 1
                    changed = True
                except Exception as exc:
                    self._log(f"  Write error (row {row_idx}): {exc}")
                    err_count += 1

        ai_translated += ok_count
        ai_errors += err_count
        self._log(f"  Translated {ok_count} string(s) ({err_count} error(s)).")

        if changed:
            self._save(bf, uk_path)

        return changed, auto_fixed, ai_translated, ai_errors

    def _save(self, bf, path: str):
        """Save the modified BethesdaStringFile back to disk."""
        try:
            bf.save(path)
            self._log(f"  Saved → {os.path.basename(path)}")
        except Exception as exc:
            self._log(f"  Save error: {exc}")
            logger.exception("Save failed for %s", path)

    def _log(self, msg: str):
        logger.info(msg)
        self.log_message.emit(msg)


# ── Dialog ─────────────────────────────────────────────────────────────────────

class BatchTranslateDialog(QDialog):
    """
    Dialog for batch quality-fix + AI translation of a folder of binary string files.
    """

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Batch Translate Folder"))
        self.setMinimumWidth(720)
        self._worker: Optional[BatchTranslateWorker] = None
        self._settings = settings
        self._setup_ui()

    # ── UI setup ───────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ── Folder inputs ──────────────────────────────────────────────────────
        folder_grp = QGroupBox(self.tr("Folders"))
        fg = QVBoxLayout(folder_grp)

        fg.addLayout(self._make_folder_row(
            self.tr("Translated files (UK):"),
            "lbl_uk", "edit_uk", "btn_uk",
            tooltip=self.tr("Folder containing the _uk.strings / _uk.dlstrings / _uk.ilstrings files to process"),
        ))
        fg.addLayout(self._make_folder_row(
            self.tr("Source files (RU, optional):"),
            "lbl_ru", "edit_ru", "btn_ru",
            tooltip=self.tr(
                "Folder containing the corresponding _ru.strings source files.\n"
                "Required for AI translation — without it only auto-fix runs."
            ),
        ))

        # Pre-populate from last known paths if settings available
        if self._settings:
            # defaults from settings if paths saved, else empty
            pass

        root.addWidget(folder_grp)

        # ── Options ────────────────────────────────────────────────────────────
        opt_grp = QGroupBox(self.tr("What to fix"))
        og = QVBoxLayout(opt_grp)
        self.chk_autofix = QCheckBox(self.tr("Auto-fix mechanical issues (Russian chars, missing tags, whitespace)"))
        self.chk_autofix.setChecked(True)
        self.chk_untranslated = QCheckBox(self.tr("AI translate untranslated strings (same as Russian source)"))
        self.chk_untranslated.setChecked(True)
        self.chk_leaks = QCheckBox(self.tr("AI retranslate strings with Russian word leakage"))
        self.chk_leaks.setChecked(True)
        og.addWidget(self.chk_autofix)
        og.addWidget(self.chk_untranslated)
        og.addWidget(self.chk_leaks)
        root.addWidget(opt_grp)

        # ── Ollama settings ────────────────────────────────────────────────────
        ollama_grp = QGroupBox(self.tr("Ollama"))
        olg = QHBoxLayout(ollama_grp)

        olg.addWidget(QLabel(self.tr("URL:")))
        self.edit_url = QLineEdit()
        url_val = "http://localhost:11434"
        if self._settings:
            url_val = getattr(self._settings, "ollama_url", url_val)
        self.edit_url.setText(url_val)
        olg.addWidget(self.edit_url, stretch=2)

        olg.addWidget(QLabel(self.tr("Model:")))
        self.edit_model = QLineEdit()
        model_val = "translategemma3-st"
        if self._settings:
            model_val = getattr(self._settings, "ollama_model", model_val)
        self.edit_model.setText(model_val)
        olg.addWidget(self.edit_model, stretch=1)

        olg.addWidget(QLabel(self.tr("Workers:")))
        self.spin_workers = QSpinBox()
        self.spin_workers.setRange(1, 32)
        workers_val = 10
        if self._settings:
            workers_val = getattr(self._settings, "max_workers", workers_val)
        self.spin_workers.setValue(workers_val)
        self.spin_workers.setFixedWidth(60)
        olg.addWidget(self.spin_workers)

        root.addWidget(ollama_grp)

        # ── Progress ───────────────────────────────────────────────────────────
        self.lbl_progress = QLabel(self.tr("Ready."))
        root.addWidget(self.lbl_progress)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        root.addWidget(self.progress_bar)

        # ── Log ────────────────────────────────────────────────────────────────
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(220)
        self.log_text.setPlaceholderText(self.tr("Processing log will appear here…"))
        root.addWidget(self.log_text)

        # ── Button bar ─────────────────────────────────────────────────────────
        btn_bar = QHBoxLayout()

        self.btn_start = QPushButton(self.tr("Start"))
        self.btn_start.setDefault(True)
        self.btn_start.clicked.connect(self._start)
        btn_bar.addWidget(self.btn_start)

        self.btn_stop = QPushButton(self.tr("Stop"))
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)
        btn_bar.addWidget(self.btn_stop)

        btn_bar.addStretch()

        self.btn_close = QPushButton(self.tr("Close"))
        self.btn_close.clicked.connect(self.accept)
        btn_bar.addWidget(self.btn_close)

        root.addLayout(btn_bar)

    def _make_folder_row(
        self,
        label: str,
        lbl_attr: str,
        edit_attr: str,
        btn_attr: str,
        tooltip: str = "",
    ) -> QHBoxLayout:
        row = QHBoxLayout()
        lbl = QLabel(label)
        lbl.setFixedWidth(190)
        edit = QLineEdit()
        edit.setToolTip(tooltip)
        edit.setPlaceholderText(self.tr("Browse or type folder path…"))
        btn = QPushButton(self.tr("Browse…"))
        btn.setFixedWidth(90)

        def _browse(_checked=False, _edit=edit):
            path = QFileDialog.getExistingDirectory(self, self.tr("Select folder"), _edit.text() or "")
            if path:
                _edit.setText(path)

        btn.clicked.connect(_browse)

        setattr(self, lbl_attr, lbl)
        setattr(self, edit_attr, edit)
        setattr(self, btn_attr, btn)

        row.addWidget(lbl)
        row.addWidget(edit)
        row.addWidget(btn)
        return row

    # ── Start / stop ───────────────────────────────────────────────────────────

    @Slot()
    def _start(self):
        uk_folder = self.edit_uk.text().strip()
        if not uk_folder or not os.path.isdir(uk_folder):
            QMessageBox.warning(
                self,
                self.tr("Missing folder"),
                self.tr("Please select a valid folder containing the translated binary files."),
            )
            return

        if not self.chk_autofix.isChecked() and not self.chk_untranslated.isChecked() and not self.chk_leaks.isChecked():
            QMessageBox.warning(
                self,
                self.tr("Nothing to do"),
                self.tr("Please select at least one fix option."),
            )
            return

        terms_file = ""
        if self._settings:
            terms_file = getattr(self._settings, "protected_terms_file", "")

        self.log_text.clear()
        self.progress_bar.setValue(0)
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_close.setEnabled(False)

        self._worker = BatchTranslateWorker(
            uk_folder=uk_folder,
            ru_folder=self.edit_ru.text().strip(),
            source_lang="ru",
            target_lang="uk",
            api_url=self.edit_url.text().strip() or "http://localhost:11434",
            model=self.edit_model.text().strip() or "translategemma3-st",
            max_workers=self.spin_workers.value(),
            do_autofix=self.chk_autofix.isChecked(),
            do_translate_untranslated=self.chk_untranslated.isChecked(),
            do_retranslate_leaks=self.chk_leaks.isChecked(),
            terms_file=terms_file,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.log_message.connect(self._on_log)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    @Slot()
    def _stop(self):
        if self._worker:
            self._worker.stop()
        self.btn_stop.setEnabled(False)

    # ── Worker signal slots ────────────────────────────────────────────────────

    @Slot(int, int, str)
    def _on_progress(self, done: int, total: int, filename: str):
        pct = int(done / total * 100) if total > 0 else 0
        self.progress_bar.setValue(pct)
        self.lbl_progress.setText(
            self.tr("Processing {done}/{total}: {fn}").format(
                done=done, total=total, fn=filename
            )
        )

    @Slot(str)
    def _on_log(self, msg: str):
        self.log_text.appendPlainText(msg)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    @Slot(dict)
    def _on_finished(self, summary: dict):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_close.setEnabled(True)
        self.progress_bar.setValue(100)

        elapsed = summary.get("elapsed_sec", 0)
        mins, secs = divmod(int(elapsed), 60)
        elapsed_str = f"{mins}m {secs}s" if mins else f"{secs}s"

        msg = (
            f"\n{'─' * 60}\n"
            f"Done in {elapsed_str}.\n"
            f"Files processed : {summary.get('files_total', 0)}\n"
            f"Files changed   : {summary.get('files_changed', 0)}\n"
            f"Auto-fixed      : {summary.get('auto_fixed', 0)} string(s)\n"
            f"AI translated   : {summary.get('ai_translated', 0)} string(s)\n"
            f"AI errors       : {summary.get('ai_errors', 0)} string(s)\n"
        )
        self.log_text.appendPlainText(msg)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())

        self.lbl_progress.setText(
            self.tr(
                "Finished — {changed} file(s) changed, "
                "{ai} string(s) AI-translated"
            ).format(
                changed=summary.get("files_changed", 0),
                ai=summary.get("ai_translated", 0),
            )
        )
