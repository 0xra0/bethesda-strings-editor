"""Dialog for uploading a file to a NexusMods mod page via the v3 API."""

import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.nexusmods_uploader import NexusModsError, NexusModsUploader

_FILE_CATEGORIES = [
    ("Main",         "main"),
    ("Update",       "update"),
    ("Optional",     "optional"),
    ("Old version",  "old_version"),
    ("Miscellaneous","miscellaneous"),
]


def _read_version_from_pyproject() -> str:
    """Try to read version from pyproject.toml; return empty string on failure."""
    try:
        root = Path(__file__).parent.parent / "pyproject.toml"
        text = root.read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        return m.group(1) if m else ""
    except Exception:
        return ""


def _read_version() -> str:
    try:
        from _version import __version__  # type: ignore[import]
        return __version__
    except ImportError:
        return _read_version_from_pyproject()


class _UploadWorker(QThread):
    progress = Signal(int, int, str)   # bytes_done, bytes_total, message
    succeeded = Signal(str)            # file_uid
    failed = Signal(str)               # error message

    def __init__(
        self,
        api_key: str,
        file_path: Path,
        group_id: str,
        name: str,
        version: str,
        description: str,
        file_category: str,
        archive_existing: bool,
        primary_download: bool,
        allow_manager: bool,
        show_requirements: bool,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._api_key = api_key
        self._file_path = file_path
        self._group_id = group_id
        self._name = name
        self._version = version
        self._description = description
        self._file_category = file_category
        self._archive_existing = archive_existing
        self._primary_download = primary_download
        self._allow_manager = allow_manager
        self._show_requirements = show_requirements

    def run(self) -> None:
        try:
            uploader = NexusModsUploader(self._api_key)
            uid = uploader.upload_file(
                self._file_path,
                self._group_id,
                name=self._name,
                version=self._version,
                description=self._description,
                file_category=self._file_category,
                archive_existing_file=self._archive_existing,
                primary_mod_manager_download=self._primary_download,
                allow_mod_manager_download=self._allow_manager,
                show_requirements_pop_up=self._show_requirements,
                progress=lambda d, t, m: self.progress.emit(d, t, m),
            )
            self.succeeded.emit(uid)
        except NexusModsError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Unexpected error: {exc}")


class NexusModsUploadDialog(QDialog):
    """Upload a file to NexusMods with progress feedback."""

    def __init__(
        self,
        parent=None,
        *,
        settings=None,
        initial_file: Optional[Path] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Upload to NexusMods")
        self.setMinimumWidth(540)
        self._settings = settings
        self._worker: Optional[_UploadWorker] = None

        self._build_ui(initial_file)
        self._load_from_settings()

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self, initial_file: Optional[Path]) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        root.addWidget(self._make_auth_group())
        root.addWidget(self._make_file_group(initial_file))
        root.addWidget(self._make_meta_group())
        root.addWidget(self._make_progress_group())

        buttons = QDialogButtonBox()
        self._upload_btn = buttons.addButton("Upload", QDialogButtonBox.ActionRole)
        self._upload_btn.setDefault(True)
        self._close_btn = buttons.addButton(QDialogButtonBox.Close)
        self._upload_btn.clicked.connect(self._start_upload)
        self._close_btn.clicked.connect(self.reject)
        root.addWidget(buttons)

    def _make_auth_group(self) -> QGroupBox:
        box = QGroupBox("Authentication")
        layout = QFormLayout(box)

        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        self._key_edit = QLineEdit()
        self._key_edit.setEchoMode(QLineEdit.PasswordEchoOnEdit)
        self._key_edit.setPlaceholderText("Paste your NexusMods API key…")
        toggle = QPushButton("Show")
        toggle.setCheckable(True)
        toggle.setFixedWidth(54)
        toggle.toggled.connect(
            lambda on: (
                self._key_edit.setEchoMode(
                    QLineEdit.Normal if on else QLineEdit.PasswordEchoOnEdit
                ),
                toggle.setText("Hide" if on else "Show"),
            )
        )
        h.addWidget(self._key_edit)
        h.addWidget(toggle)
        layout.addRow("API key:", row)

        note = QLabel(
            '<a href="https://www.nexusmods.com/users/myaccount?tab=api">Get your API key</a>'
        )
        note.setOpenExternalLinks(True)
        layout.addRow("", note)
        return box

    def _make_file_group(self, initial_file: Optional[Path]) -> QGroupBox:
        box = QGroupBox("File to upload")
        layout = QFormLayout(box)

        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        self._file_edit = QLineEdit()
        if initial_file:
            self._file_edit.setText(str(initial_file))
        browse = QPushButton("Browse…")
        browse.setFixedWidth(72)
        browse.clicked.connect(self._browse_file)
        h.addWidget(self._file_edit)
        h.addWidget(browse)
        layout.addRow("Path:", row)

        self._group_edit = QLineEdit()
        self._group_edit.setPlaceholderText("e.g. 12345")
        layout.addRow("File group ID:", self._group_edit)

        note = QLabel("Find on your mod page: Files → ⋯ → API Info")
        note.setStyleSheet("color: gray; font-size: 11px;")
        layout.addRow("", note)
        return box

    def _make_meta_group(self) -> QGroupBox:
        box = QGroupBox("Metadata")
        layout = QFormLayout(box)

        self._version_edit = QLineEdit(_read_version())
        layout.addRow("Version:", self._version_edit)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Display name shown on the mod page")
        layout.addRow("Display name:", self._name_edit)

        self._desc_edit = QPlainTextEdit()
        self._desc_edit.setPlaceholderText("Optional description (HTML supported)")
        self._desc_edit.setFixedHeight(72)
        layout.addRow("Description:", self._desc_edit)

        self._cat_combo = QComboBox()
        for label, code in _FILE_CATEGORIES:
            self._cat_combo.addItem(label, code)
        layout.addRow("Category:", self._cat_combo)

        self._archive_chk = QCheckBox("Archive previous version of this file")
        self._archive_chk.setChecked(True)
        layout.addRow("", self._archive_chk)

        self._allow_manager_chk = QCheckBox("Allow mod manager download")
        self._allow_manager_chk.setChecked(True)
        layout.addRow("", self._allow_manager_chk)

        self._primary_chk = QCheckBox("Set as primary mod manager download")
        layout.addRow("", self._primary_chk)

        return box

    def _make_progress_group(self) -> QGroupBox:
        box = QGroupBox("Progress")
        v = QVBoxLayout(box)
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._status_lbl = QLabel("Ready.")
        self._status_lbl.setWordWrap(True)
        v.addWidget(self._progress_bar)
        v.addWidget(self._status_lbl)
        return box

    # ── settings persistence ───────────────────────────────────────────────

    def _load_from_settings(self) -> None:
        if not self._settings:
            return
        if self._settings.nexusmods_api_key:
            self._key_edit.setText(self._settings.nexusmods_api_key)
        if self._settings.nexusmods_file_group_id:
            self._group_edit.setText(self._settings.nexusmods_file_group_id)

    def _save_to_settings(self) -> None:
        if not self._settings:
            return
        self._settings.nexusmods_api_key = self._key_edit.text().strip()
        self._settings.nexusmods_file_group_id = self._group_edit.text().strip()
        try:
            from gui.app_settings import save_settings
            save_settings(self._settings)
        except Exception:
            pass

    # ── slots ──────────────────────────────────────────────────────────────

    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select file to upload",
            str(Path(self._file_edit.text()).parent) if self._file_edit.text() else "",
            "Archives (*.zip *.7z *.rar);;All files (*)",
        )
        if path:
            self._file_edit.setText(path)

    def _start_upload(self) -> None:
        api_key = self._key_edit.text().strip()
        file_path = Path(self._file_edit.text().strip())
        group_id = self._group_edit.text().strip()
        version = self._version_edit.text().strip()
        name = self._name_edit.text().strip() or file_path.name
        description = self._desc_edit.toPlainText().strip()
        file_category = self._cat_combo.currentData()

        # Validate
        errors = []
        if not api_key:
            errors.append("API key is required.")
        if not file_path.name or not file_path.exists():
            errors.append("File to upload does not exist.")
        if not group_id:
            errors.append("File group ID is required.")
        if not version:
            errors.append("Version is required.")
        if errors:
            self._status_lbl.setText("⚠ " + "  ".join(errors))
            return

        self._save_to_settings()
        self._set_uploading(True)

        self._worker = _UploadWorker(
            api_key=api_key,
            file_path=file_path,
            group_id=group_id,
            name=name,
            version=version,
            description=description,
            file_category=file_category,
            archive_existing=self._archive_chk.isChecked(),
            primary_download=self._primary_chk.isChecked(),
            allow_manager=self._allow_manager_chk.isChecked(),
            show_requirements=False,
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, done: int, total: int, message: str) -> None:
        if total > 0:
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(int(done * 100 / total))
        else:
            self._progress_bar.setRange(0, 0)  # indeterminate
        self._status_lbl.setText(message)

    def _on_success(self, file_uid: str) -> None:
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(100)
        self._status_lbl.setText(f"✓ Upload complete. NexusMods file UID: {file_uid}")
        self._set_uploading(False)

    def _on_error(self, message: str) -> None:
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._status_lbl.setText(f"✗ {message}")
        self._set_uploading(False)

    def _set_uploading(self, uploading: bool) -> None:
        self._upload_btn.setEnabled(not uploading)
        self._key_edit.setEnabled(not uploading)
        self._file_edit.setEnabled(not uploading)
        self._group_edit.setEnabled(not uploading)

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait(3000)
        super().closeEvent(event)
