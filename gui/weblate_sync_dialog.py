"""
Weblate Sync Dialog — push/pull game string translations to/from Weblate.

Workflow
────────
Push  →  strings_to_po(table data)  →  WeblateClient.upload_po()
Pull  →  WeblateClient.download_po()  →  po_to_strings()  →  merge into table

All network I/O runs on a background QThread; the UI remains responsive.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from PySide6.QtCore import QMutexLocker, QObject, QThread, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from gui.weblate_client import WeblateClient, po_to_strings, strings_to_po

logger = logging.getLogger(__name__)


# ── Background worker ──────────────────────────────────────────────────────────

class _WeblateWorker(QObject):
    log_line = Signal(str)
    finished = Signal(bool, str, object)  # success, summary, payload

    def __init__(
        self,
        client: WeblateClient,
        project: str,
        component: str,
        language: str,
    ) -> None:
        super().__init__()
        self.client    = client
        self.project   = project
        self.component = component
        self.language  = language
        # Set before calling run():
        self.mode          = ''          # 'push' | 'pull' | 'stats'
        self.table_data: List[dict] = []
        self.overwrite     = False

    @Slot()
    def run(self) -> None:
        try:
            if self.mode == 'push':
                self._push()
            elif self.mode == 'pull':
                self._pull()
            elif self.mode == 'stats':
                self._stats()
        except Exception as exc:
            logger.error('WeblateWorker error: %s', exc, exc_info=True)
            self.finished.emit(False, str(exc), None)

    def _stats(self) -> None:
        self.log_line.emit('Fetching component statistics…')
        stats = self.client.get_component_stats(self.project, self.component)
        self.finished.emit(True, '', stats)

    def _push(self) -> None:
        self.log_line.emit('Converting table to PO format…')
        po = strings_to_po(
            self.table_data,
            source_lang='en',
            target_lang=self.language,
            include_translations=True,
        )
        n = po.count('\nmsgctxt ')
        self.log_line.emit(f'Uploading {n} strings to Weblate…')
        result = self.client.upload_po(
            self.project, self.component, self.language, po,
            overwrite=self.overwrite, method='translate',
        )
        accepted  = result.get('accepted', '?')
        skipped   = result.get('skipped', '?')
        not_found = result.get('not_found', '?')
        self.log_line.emit(
            f'Upload complete — accepted: {accepted}  '
            f'skipped: {skipped}  not found: {not_found}'
        )
        self.finished.emit(True, f'Pushed {accepted} strings.', result)

    def _pull(self) -> None:
        self.log_line.emit('Downloading translated PO from Weblate…')
        po_content = self.client.download_po(self.project, self.component, self.language)
        self.log_line.emit('Parsing translations…')
        translations = po_to_strings(po_content)
        self.log_line.emit(f'Received {len(translations)} translated strings.')
        self.finished.emit(True, f'Pulled {len(translations)} translations.', translations)


# ── Dialog ─────────────────────────────────────────────────────────────────────

class WelateSyncDialog(QDialog):
    """
    Push/pull game string translations between the app and a Weblate component.

    Requires AppSettings fields:
        weblate_url, weblate_api_token, weblate_project, weblate_component

    After a successful pull, call ``apply_pulled()`` to write translations
    into StringTableModel (or let the user click the "Apply" button in the dialog).
    """

    def __init__(self, table_model, settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr('Weblate Sync'))
        self.setMinimumSize(620, 620)
        self._model    = table_model
        self._settings = settings
        self._thread: Optional[QThread] = None
        self._worker: Optional[_WeblateWorker] = None
        self._pulled: Dict[int, str] = {}   # id → translation, filled after pull
        self._setup_ui()
        # Kick off a stats fetch if settings look complete
        if self._settings_ok():
            self._run_worker('stats')

    # ── UI construction ────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ── Connection status ──────────────────────────────────────────────────
        conn_grp = QGroupBox(self.tr('Connection'))
        conn_lay = QFormLayout(conn_grp)

        url   = self._settings.weblate_url       or self.tr('(not configured)')
        proj  = self._settings.weblate_project   or ''
        comp  = self._settings.weblate_component or ''
        slug  = f'{proj} / {comp}' if proj else self.tr('(not configured)')

        conn_lay.addRow(self.tr('Server:'),    QLabel(url))
        conn_lay.addRow(self.tr('Component:'), QLabel(slug))

        self._lbl_status = QLabel(self.tr('—'))
        btn_test = QPushButton(self.tr('Test'))
        btn_test.setFixedWidth(70)
        btn_test.clicked.connect(self._test_connection)
        status_row = QHBoxLayout()
        status_row.addWidget(self._lbl_status, 1)
        status_row.addWidget(btn_test)
        conn_lay.addRow(self.tr('Status:'), status_row)

        root.addWidget(conn_grp)

        # ── Stats ──────────────────────────────────────────────────────────────
        stats_grp = QGroupBox(self.tr('Component Statistics'))
        stats_lay = QVBoxLayout(stats_grp)
        self._lbl_stats = QLabel(self.tr('Fetching…'))
        self._lbl_stats.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._lbl_stats.setWordWrap(True)
        self._lbl_stats.setStyleSheet('color: #374151; font-size: 0.92em;')
        btn_refresh = QPushButton(self.tr('Refresh'))
        btn_refresh.setFixedWidth(80)
        btn_refresh.clicked.connect(lambda: self._run_worker('stats'))
        row = QHBoxLayout()
        row.addWidget(self._lbl_stats, 1)
        row.addWidget(btn_refresh, 0, Qt.AlignTop)
        stats_lay.addLayout(row)
        root.addWidget(stats_grp)

        # ── Push ───────────────────────────────────────────────────────────────
        push_grp = QGroupBox(self.tr('Push  —  Upload strings to Weblate'))
        push_lay = QVBoxLayout(push_grp)

        self._chk_push_overwrite = QCheckBox(
            self.tr('Overwrite existing Weblate translations')
        )
        self._chk_push_overwrite.setToolTip(self.tr(
            'Checked: replace Weblate translations with local ones.\n'
            'Unchecked: only fill in strings not yet translated on Weblate.'
        ))
        push_lay.addWidget(self._chk_push_overwrite)

        self._btn_push = QPushButton(self.tr('⬆  Push to Weblate'))
        self._btn_push.setToolTip(self.tr(
            'Upload all strings from the current file to Weblate.\n'
            'Any local translations are included so community translators\n'
            'see them as a starting point.'
        ))
        self._btn_push.clicked.connect(self._do_push)
        push_lay.addWidget(self._btn_push)
        root.addWidget(push_grp)

        # ── Pull ───────────────────────────────────────────────────────────────
        pull_grp = QGroupBox(self.tr('Pull  —  Download translations from Weblate'))
        pull_lay = QVBoxLayout(pull_grp)

        self._chk_pull_overwrite = QCheckBox(
            self.tr('Overwrite strings already translated locally')
        )
        self._chk_pull_overwrite.setToolTip(self.tr(
            'Checked: replace local translations with Weblate versions.\n'
            'Unchecked: only fill in locally untranslated strings.'
        ))
        pull_lay.addWidget(self._chk_pull_overwrite)

        btn_row = QHBoxLayout()
        self._btn_pull = QPushButton(self.tr('⬇  Pull from Weblate'))
        self._btn_pull.clicked.connect(self._do_pull)
        btn_row.addWidget(self._btn_pull)

        self._btn_apply = QPushButton(self.tr('Apply to Table'))
        self._btn_apply.setToolTip(self.tr(
            'Write the downloaded translations into the string table.\n'
            'Pull first, then click Apply.'
        ))
        self._btn_apply.setEnabled(False)
        self._btn_apply.clicked.connect(self._apply_pulled)
        btn_row.addWidget(self._btn_apply)
        pull_lay.addLayout(btn_row)

        self._lbl_pull_count = QLabel('')
        self._lbl_pull_count.setStyleSheet('color: #059669; font-weight: bold;')
        pull_lay.addWidget(self._lbl_pull_count)
        root.addWidget(pull_grp)

        # ── Log ────────────────────────────────────────────────────────────────
        log_grp = QGroupBox(self.tr('Log'))
        log_lay = QVBoxLayout(log_grp)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFixedHeight(130)
        self._log.setStyleSheet('font-family: monospace; font-size: 0.88em;')
        log_lay.addWidget(self._log)
        root.addWidget(log_grp)

        # ── Progress + close ───────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 0)   # indeterminate
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    # ── Worker lifecycle ───────────────────────────────────────────────────────

    def _make_client(self) -> Optional[WeblateClient]:
        if not self._settings_ok():
            QMessageBox.warning(
                self, self.tr('Not Configured'),
                self.tr('Configure Weblate URL, API token, project, and component\n'
                        'in Settings → Weblate before syncing.'),
            )
            return None
        return WeblateClient(
            self._settings.weblate_url,
            self._settings.weblate_api_token,
        )

    def _settings_ok(self) -> bool:
        return bool(
            self._settings.weblate_url
            and self._settings.weblate_api_token
            and self._settings.weblate_project
            and self._settings.weblate_component
        )

    def _run_worker(self, mode: str, overwrite: bool = False) -> None:
        if self._thread and self._thread.isRunning():
            self._log_append('A sync operation is already in progress.')
            return

        client = self._make_client()
        if client is None:
            return

        self._worker = _WeblateWorker(
            client,
            self._settings.weblate_project,
            self._settings.weblate_component,
            self._settings.default_target_lang or 'uk',
        )
        self._worker.mode       = mode
        self._worker.overwrite  = overwrite
        self._worker.table_data = list(self._model._data) if self._model else []
        self._worker.log_line.connect(self._log_append)
        self._worker.finished.connect(self._on_worker_finished)

        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._thread.start()

        self._set_busy(True)

    def _set_busy(self, busy: bool) -> None:
        self._progress.setVisible(busy)
        self._btn_push.setEnabled(not busy)
        self._btn_pull.setEnabled(not busy)

    @Slot(bool, str, object)
    def _on_worker_finished(self, success: bool, summary: str, payload) -> None:
        self._set_busy(False)
        if self._thread:
            self._thread.quit()
            self._thread.wait()

        mode = self._worker.mode if self._worker else ''

        if not success:
            self._log_append(f'ERROR: {summary}')
            return

        if mode == 'stats':
            self._display_stats(payload or [])
        elif mode == 'push':
            self._log_append(f'✓ {summary}')
        elif mode == 'pull':
            self._pulled = dict(payload) if payload else {}
            self._btn_apply.setEnabled(bool(self._pulled))
            n = len(self._pulled)
            self._lbl_pull_count.setText(
                self.tr('{n} translations ready — click "Apply to Table"').format(n=n)
            )
            self._log_append(f'✓ {summary}')

    # ── Slots ──────────────────────────────────────────────────────────────────

    @Slot()
    def _test_connection(self) -> None:
        client = self._make_client()
        if client is None:
            return
        self._lbl_status.setText(self.tr('Testing…'))
        ok, msg = client.test_connection()
        color = '#059669' if ok else '#dc2626'
        self._lbl_status.setText(
            f'<span style="color:{color}">{msg}</span>'
        )
        self._lbl_status.setTextFormat(Qt.RichText)

    @Slot()
    def _do_push(self) -> None:
        overwrite = self._chk_push_overwrite.isChecked()
        n_total = sum(1 for r in (self._model._data if self._model else []) if r.get('original'))
        reply = QMessageBox.question(
            self, self.tr('Push to Weblate'),
            self.tr(
                'Upload {n} strings to Weblate?\n\n'
                'Community translators will see these strings on the web interface.'
            ).format(n=n_total),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self._log_append('— Push started —')
        self._run_worker('push', overwrite=overwrite)

    @Slot()
    def _do_pull(self) -> None:
        self._pulled.clear()
        self._btn_apply.setEnabled(False)
        self._lbl_pull_count.setText('')
        self._log_append('— Pull started —')
        self._run_worker('pull')

    @Slot()
    def _apply_pulled(self) -> None:
        if not self._pulled:
            return
        if not self._model:
            return

        overwrite_local = self._chk_pull_overwrite.isChecked()
        applied = 0
        for row_idx, row in enumerate(self._model._data):
            sid = row.get('id', 0)
            if sid not in self._pulled:
                continue
            already_translated = bool(row.get('translated')) and row.get('status') == 'translated'
            if already_translated and not overwrite_local:
                continue
            self._model.set_translated_text(row_idx, self._pulled[sid])
            applied += 1

        msg = self.tr('{applied} translation(s) applied to the table.').format(applied=applied)
        self._log_append(f'✓ {msg}')
        self._lbl_pull_count.setText(msg)
        self._btn_apply.setEnabled(False)
        QMessageBox.information(self, self.tr('Weblate Pull'), msg)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _display_stats(self, stats: list) -> None:
        if not stats:
            self._lbl_stats.setText(self.tr('No statistics available.'))
            return
        lines: List[str] = []
        for entry in sorted(stats, key=lambda x: x.get('code', '')):
            lang  = entry.get('language', {}).get('name', entry.get('code', '?'))
            total = entry.get('total', 0)
            trans = entry.get('translated', 0)
            pct   = entry.get('translated_percent', 0.0)
            bar   = '█' * int(pct / 10) + '░' * (10 - int(pct / 10))
            lines.append(f'{lang:<20} {bar}  {trans}/{total}  ({pct:.0f}%)')
        self._lbl_stats.setText('\n'.join(lines) if lines else '—')
        self._log_append(f'Stats refreshed — {len(stats)} language(s).')

    def _log_append(self, text: str) -> None:
        self._log.append(text)
        self._log.verticalScrollBar().setValue(
            self._log.verticalScrollBar().maximum()
        )

    def closeEvent(self, event) -> None:
        if self._thread and self._thread.isRunning():
            event.ignore()
            QMessageBox.information(
                self, self.tr('Sync in progress'),
                self.tr('Please wait for the current operation to finish.')
            )
            return
        super().closeEvent(event)
