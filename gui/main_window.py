"""
Main window for Bethesda Strings AI Translator
FIXED: All syntax errors, threading issues, and term protection
"""

import logging
import re
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QItemSelectionModel,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QSystemTrayIcon,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWhatsThis,
    QWidget,
)

from bethesda_strings import BethesdaStringFile, EncodingConverter, XMLHandler
from bethesda_strings.ba2_handler import BA2File
from bethesda_strings.esp_handler import EspFile
from gui.app_settings import (
    AppSettings,
    get_cache_dir,
    get_config_dir,
    get_config_path,
    load_settings,
    save_settings,
)
from gui.crash_recovery import CrashRecoveryDialog, CrashRecoveryManager
from gui.desktop_notify import send_notification
from gui.file_dialog_helper import get_open_filename, get_save_filename
from gui.keyboard_manager import ActionEntry, KeyboardManager
from gui.macro_recorder import MacroRecorder
from gui.claude_client import is_claude_model, estimate_batch_cost
from gui.ollama_worker import OllamaWorker, TranslationRequest
from gui.settings_dialog import SettingsDialog
from gui.dialogue_tree_dialog import DialogueTreeDialog
from gui.translation_memory import TranslationMemory
from gui.string_table import StringTableModel, StringTableView
from gui.term_protector import ProtectedTerm, TermProtector
from gui.translation_cache import TranslationCache

logger = logging.getLogger(__name__)

_VALID_DROP_EXTS = frozenset({
    ".strings", ".dlstrings", ".ilstrings",
    ".esp", ".esm", ".esl",
    ".ba2",
})


def _valid_drop_paths(mime) -> list:
    """Return local file paths from mime data that match supported extensions."""
    return [
        p
        for url in mime.urls()
        if url.isLocalFile()
        for p in [url.toLocalFile()]
        if Path(p).suffix.lower() in _VALID_DROP_EXTS
    ]


def _format_eta(seconds: float) -> str:
    """Format a duration in seconds as a human-readable string."""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 60:
        return f"{m}m {sec:02d}s"
    h, m2 = divmod(m, 60)
    return f"{h}h {m2:02d}m"


class _WelcomeWidget(QWidget):
    """Shown when no file is loaded. Supports drag & drop and open button."""

    open_requested = Signal()
    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._setup()

    def _setup(self):
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignCenter)
        outer.setContentsMargins(24, 24, 24, 24)

        card = QFrame()
        card.setObjectName("WelcomeCard")
        self._card = card
        self._card_default_style = (
            "QFrame#WelcomeCard {"
            "  border: 2px dashed rgba(99,102,241,0.35);"
            "  border-radius: 18px;"
            "  background: rgba(99,102,241,0.04);"
            "}"
        )
        card.setStyleSheet(self._card_default_style)
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        card_layout = QVBoxLayout(card)
        card_layout.setAlignment(Qt.AlignCenter)
        card_layout.setSpacing(14)
        card_layout.setContentsMargins(48, 56, 48, 56)

        # App icon
        icon_path = Path(__file__).parent.parent / "resources" / "app_icon_64.png"
        if icon_path.exists():
            from PySide6.QtGui import QPixmap
            lbl_icon = QLabel()
            pm = QPixmap(str(icon_path)).scaled(
                88, 88, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            lbl_icon.setPixmap(pm)
            lbl_icon.setAlignment(Qt.AlignCenter)
            card_layout.addWidget(lbl_icon)

        # Title
        lbl_title = QLabel(self.tr("Bethesda Strings AI Translator"))
        lbl_title.setAlignment(Qt.AlignCenter)
        lbl_title.setStyleSheet(
            "font-size: 22px; font-weight: 700; letter-spacing: 0.5px;"
        )
        card_layout.addWidget(lbl_title)

        # Subtitle
        lbl_sub = QLabel(self.tr("Open a string file or plugin to begin"))
        lbl_sub.setAlignment(Qt.AlignCenter)
        lbl_sub.setStyleSheet("font-size: 13px; opacity: 0.6;")
        card_layout.addWidget(lbl_sub)

        card_layout.addSpacing(12)

        # Primary open button
        btn = QPushButton(self.tr("Open File"))
        btn.setProperty("primary", True)
        btn.setFixedHeight(46)
        btn.setMinimumWidth(220)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(
            "QPushButton { font-size: 14px; font-weight: 600;"
            "  border-radius: 10px; padding: 0 28px; }"
        )
        btn.clicked.connect(self.open_requested)
        card_layout.addWidget(btn, alignment=Qt.AlignCenter)

        # Shortcut hint
        lbl_key = QLabel("Ctrl+O")
        lbl_key.setAlignment(Qt.AlignCenter)
        lbl_key.setStyleSheet(
            "font-size: 11px; opacity: 0.4;"
            "border: 1px solid currentColor; border-radius: 4px;"
            "padding: 1px 6px; letter-spacing: 0.5px;"
        )
        card_layout.addWidget(lbl_key, alignment=Qt.AlignCenter)

        card_layout.addSpacing(6)

        # Drop hint
        lbl_drop = QLabel(self.tr("or drag & drop files here"))
        lbl_drop.setAlignment(Qt.AlignCenter)
        lbl_drop.setStyleSheet("font-size: 12px; opacity: 0.45;")
        card_layout.addWidget(lbl_drop)

        # Supported formats
        lbl_fmt = QLabel(".strings  ·  .dlstrings  ·  .ilstrings  ·  .esp  ·  .esm  ·  .esl  ·  .ba2")
        lbl_fmt.setAlignment(Qt.AlignCenter)
        lbl_fmt.setStyleSheet("font-size: 11px; opacity: 0.28; letter-spacing: 0.5px;")
        card_layout.addWidget(lbl_fmt)

        outer.addWidget(card)

        # Start idle pulse after the widget is shown
        from gui.micro_animations import start_card_pulse
        QTimer.singleShot(400, lambda: start_card_pulse(card))

    def dragEnterEvent(self, event):
        try:
            from gui.micro_animations import stop_card_pulse
            urls = event.mimeData().urls()
            valid = [
                u.toLocalFile() for u in urls
                if u.isLocalFile()
                and Path(u.toLocalFile()).suffix.lower() in _VALID_DROP_EXTS
            ]
            if valid:
                event.acceptProposedAction()
                stop_card_pulse(self._card)
                self._card.setStyleSheet(
                    "QFrame#WelcomeCard {"
                    "  border: 2px dashed #10b981;"
                    "  border-radius: 18px;"
                    "  background: rgba(16,185,129,0.08);"
                    "}"
                )
            else:
                event.ignore()
        except Exception:
            pass

    def dragLeaveEvent(self, event):
        from gui.micro_animations import start_card_pulse
        self._card.setStyleSheet(self._card_default_style)
        start_card_pulse(self._card)

    def dropEvent(self, event):
        self._card.setStyleSheet(self._card_default_style)
        try:
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path and Path(path).suffix.lower() in _VALID_DROP_EXTS:
                    self.file_dropped.emit(path)
                    break
        except Exception:
            pass


class _DropOverlay(QWidget):
    """Full-window overlay shown while a supported file is being dragged in.

    Parented to MainWindow and sized to cover it completely via resizeEvent.
    WA_TransparentForMouseEvents lets drag events pass through to the window.
    """

    _STYLE_VALID = (
        "background: rgba(16,185,129,0.10);",
        "#10b981",
    )
    _STYLE_INVALID = (
        "background: rgba(239,68,68,0.10);",
        "#ef4444",
    )

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAcceptDrops(False)
        self.hide()
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.setContentsMargins(48, 48, 48, 48)

        self._box = QFrame()
        self._box.setObjectName("DropZoneBox")
        box_lay = QVBoxLayout(self._box)
        box_lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box_lay.setSpacing(14)
        box_lay.setContentsMargins(72, 44, 72, 44)

        self._icon_lbl = QLabel()
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box_lay.addWidget(self._icon_lbl)

        self._headline = QLabel()
        self._headline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._headline.setWordWrap(True)
        box_lay.addWidget(self._headline)

        self._sub_lbl = QLabel(
            ".strings  ·  .dlstrings  ·  .ilstrings  ·  .esp  ·  .esm  ·  .esl  ·  .ba2"
        )
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box_lay.addWidget(self._sub_lbl)

        outer.addWidget(self._box)

    # ── Public API ─────────────────────────────────────────────────────────────

    def show_valid(self, paths: list) -> None:
        n = len(paths)
        name = Path(paths[0]).name
        self._headline.setText(
            f"Drop to open  {name}" if n == 1 else f"Drop to open  {n} file(s)"
        )
        self._apply_style(valid=True)
        self.raise_()
        self.show()
        from gui.micro_animations import fade_in_overlay
        fade_in_overlay(self)

    def show_invalid(self) -> None:
        self._headline.setText("Unsupported file type")
        self._apply_style(valid=False)
        self.raise_()
        self.show()
        from gui.micro_animations import fade_in_overlay
        fade_in_overlay(self)

    # ── Internals ──────────────────────────────────────────────────────────────

    def _apply_style(self, valid: bool) -> None:
        bg_style, color = self._STYLE_VALID if valid else self._STYLE_INVALID
        self.setStyleSheet(bg_style)
        self._box.setStyleSheet(
            f"QFrame#DropZoneBox {{"
            f"  border: 3px dashed {color};"
            f"  border-radius: 18px;"
            f"  background: rgba(15,23,42,0.88);"
            f"}}"
        )
        icon = "⬇" if valid else "✕"
        for w, size, bold in [
            (self._icon_lbl,  "44px", False),
            (self._headline,  "20px", True),
            (self._sub_lbl,   "12px", False),
        ]:
            weight = "700" if bold else "400"
            w.setStyleSheet(
                f"font-size:{size}; font-weight:{weight};"
                f"color:{color}; background:transparent;"
            )
        self._icon_lbl.setText(icon)


class MainWindow(QMainWindow):
    """Main application window for Bethesda Strings AI Translator."""

    file_loaded = Signal(str)
    translation_complete = Signal(int, int)
    translation_requested = Signal(list)  # NEW: For thread-safe translation

    # Languages that ship with Starfield (Localization.ba2) + Russian/Ukrainian
    # for xTranslator-style workflows.  Order: English source first, then
    # alphabetical by display name.  Locale codes match Starfield's file suffixes.
    SUPPORTED_LANGUAGES = [
        ("English",              "en"),
        ("German",               "de"),
        ("Spanish",              "es"),
        ("French",               "fr"),
        ("Italian",              "it"),
        ("Japanese",             "ja"),
        ("Polish",               "pl"),
        ("Portuguese (Brazil)",  "ptbr"),
        ("Chinese (Simplified)", "zhhans"),
        ("Russian",              "ru"),
        ("Ukrainian",            "uk"),
    ]

    def __init__(self, settings: Optional[AppSettings] = None, parent=None, theme_manager=None):
        """Initialize the main window.

        Args:
            settings: Pre-loaded AppSettings. If None, loads from config.
            parent: Parent widget.
            theme_manager: ThemeManager instance for theme switching.
        """
        super().__init__(parent)
        self.setWindowTitle(self.tr("Bethesda Strings AI Translator"))
        self.setMinimumSize(1200, 700)

        # State variables
        self.current_file = None
        self.current_path = None
        # Session tracking (WorkSession + baseline set of already-translated IDs)
        self._current_session = None          # Optional[WorkSession]
        self._session_baseline: set = set()   # string IDs translated before this session
        self._last_profile_loaded_path = None  # path for which profile tints are currently applied
        self._focus_overlay = None            # FocusModeOverlay when active
        self._current_ba2: Optional[BA2File] = None   # open BA2 archive (if any)
        self._current_ba2_entry: Optional[str] = None  # internal path of the loaded strings file
        self._dialogue_tree_dlg: Optional[DialogueTreeDialog] = None
        self.settings: AppSettings = (
            settings if settings is not None else load_settings()
        )
        self.theme_manager = theme_manager

        # Initialize term protector with auto-loaded game terms
        base_dir = Path(__file__).parent.parent
        game_terms_file = base_dir / "game_terms_only.txt"
        hq_terms_file = base_dir / "protected_terms_starfield_hq.txt"
        custom_terms_file = self.settings.protected_terms_file

        custom_path = Path(custom_terms_file) if custom_terms_file else None
        if not (custom_path and custom_path.exists()):
            custom_path = None

        # TermProtector.__init__ already calls load_custom_terms(custom_path) internally,
        # so we must NOT call it again afterwards (that caused a double-load bug).
        self.term_protector = TermProtector(
            game_terms_file=game_terms_file if game_terms_file.exists() else None,
            custom_terms_file=custom_path,
        )

        # Load the comprehensive HQ terms file as a built-in default.
        # This is separate from custom_path (user-specified) and is always applied
        # when the file is present alongside the application.
        if hq_terms_file.exists():
            self.term_protector.load_custom_terms(hq_terms_file)
            logger.info(f"Loaded built-in HQ terms from {hq_terms_file.name}")

        stats = self.term_protector.get_statistics()
        logger.info(f"Term protector initialized: {stats}")

        # Translation cache
        cache_path: Optional[Path] = None
        if self.settings.enable_cache:
            cache_path = get_cache_dir() / "translation_cache.json"
            # One-time migration: move cache from old config-dir location to SSD
            old_cache = get_config_dir() / "translation_cache.json"
            if old_cache.exists() and old_cache != cache_path and not cache_path.exists():
                try:
                    import shutil as _shutil
                    _shutil.move(str(old_cache), str(cache_path))
                    logger.info("Migrated translation cache to %s", cache_path)
                except Exception as _e:
                    logger.warning("Could not migrate translation cache: %s", _e)
        self.translation_cache = TranslationCache(
            cache_path=cache_path,
            encrypt=self.settings.encrypt_cache,
        )

        # Audit log
        from gui.audit_log import get_audit_log
        self._audit_log = get_audit_log()
        self._audit_log.configure(
            path=get_config_dir() / "audit.jsonl",
            enabled=self.settings.audit_logging,
        )

        self._translation_stopping = False

        # Glossary manager
        self._glossary_manager = None
        if self.settings.enable_glossary:
            from gui.glossary import GlossaryManager
            self._glossary_manager = GlossaryManager(get_config_dir())

        # Lore RAG manager
        self._lore_rag_manager = None
        self._lore_db = None
        if self.settings.enable_lore_rag:
            self._init_lore_rag()

        # Character profile manager + per-file assignments
        from bethesda_strings.character_profiles import ProfileManager, ProfileAssignments
        self._profile_manager = ProfileManager(get_config_dir())
        self._profile_assignments = ProfileAssignments(get_config_dir())

        # Pre-translation complexity estimator
        self._pre_estimator = None
        self._pending_est_items: list = []
        self._pending_est_results: dict = {}
        self._pending_est_offset: int = 0
        if self.settings.enable_pre_translation_estimate:
            from gui.pre_translation_estimator import PreTranslationEstimator
            self._pre_estimator = PreTranslationEstimator(
                source_lang=self.settings.default_source_lang,
                weights_path=get_config_dir() / "pre_est_weights.json",
            )

        # Translation workers
        self.ollama_thread = None
        self.ollama_worker = None
        self._is_translating_txt = False
        self._txt_translation_data = []
        self._translatable_items = []
        self._txt_target_path = None

        self._init_translation_worker()

        # Keyboard shortcut registry
        self.keyboard_manager = KeyboardManager()
        self.keyboard_manager.load_custom_shortcuts(self.settings.custom_shortcuts)

        # Vim macro recorder (shared across macro dialog opens)
        self.macro_recorder = MacroRecorder()

        # Setup UI and signals
        self._setup_ui()
        self._connect_signals()
        self._register_actions()
        self.keyboard_manager.apply_all_custom_shortcuts()
        self._update_ui_state()

        self._tray_icon = self._create_tray_icon()
        self._setup_whats_this()
        QTimer.singleShot(500, self._show_first_run_tips)

        # Crash recovery
        self._recovery_manager = CrashRecoveryManager(get_config_dir())
        self._recovery_timer = QTimer(self)
        self._recovery_timer.setInterval(5 * 60 * 1000)  # 5 minutes
        self._recovery_timer.timeout.connect(self._auto_save_recovery)
        self._recovery_timer.start()
        if self._recovery_manager.has_snapshot():
            QTimer.singleShot(300, self._check_for_crash_recovery)

        # Session store
        from gui.session_manager import SessionStore
        self._session_store = SessionStore(get_config_dir() / "sessions")

        # System theme auto-follow (Qt 6.5+)
        try:
            _app = QApplication.instance()
            if _app is not None:
                _app.styleHints().colorSchemeChanged.connect(
                    self._on_system_color_scheme_changed
                )
        except Exception:
            pass

        self._audit_log.app_start("0.1.0")

    # ── Crash recovery ─────────────────────────────────────────────────────────

    @Slot()
    def _auto_save_recovery(self) -> None:
        """Write a recovery snapshot of all translated strings."""
        if not self.current_path or not self.table_model._data:
            return
        translations = [
            {"id": row["id"], "translated": row["translated"], "status": row["status"]}
            for row in self.table_model._data
            if row.get("status") == "translated" and row.get("translated")
        ]
        if not translations:
            return
        self._recovery_manager.save_snapshot(
            source_path=str(self.current_path),
            file_type="esp" if isinstance(self.current_file, EspFile) else "strings",
            encoding=self.table_model._encoding,
            source_lang=self.combo_source_lang.currentData() or self.settings.default_source_lang,
            target_lang=self.combo_target_lang.currentData() or self.settings.default_target_lang,
            translations=translations,
        )
        logger.info("Auto-saved recovery snapshot: %d string(s)", len(translations))

    @Slot()
    def _check_for_crash_recovery(self) -> None:
        """Show restore dialog if a leftover recovery snapshot exists."""
        snapshot = self._recovery_manager.load_snapshot()
        if not snapshot:
            return
        dlg = CrashRecoveryDialog(snapshot, self)
        if dlg.exec() == QDialog.Accepted:
            self._restore_from_snapshot(snapshot)
        else:
            self._recovery_manager.clear()

    def _restore_from_snapshot(self, snapshot: dict) -> None:
        """Open the source file and apply saved translations."""
        source_path = snapshot.get("source_path", "")
        if not source_path or not Path(source_path).exists():
            self._recovery_manager.clear()
            return
        self._open_file_path(source_path)
        if not self.table_model._data:
            self._recovery_manager.clear()
            return
        id_map = {row["id"]: i for i, row in enumerate(self.table_model._data)}
        batch = []
        for entry in snapshot.get("translations", []):
            sid = entry.get("id")
            text = entry.get("translated", "")
            if sid is not None and text and sid in id_map:
                batch.append((id_map[sid], text))
        if batch:
            self.table_model.set_translated_text_batch(batch)
            self.statusBar().showMessage(
                self.tr("Restored {n} translation(s) from crash recovery snapshot.").format(
                    n=len(batch)
                ),
                6000,
            )
            logger.info("Crash recovery: restored %d translation(s)", len(batch))
        self._recovery_manager.clear()

    def _create_tray_icon(self) -> QSystemTrayIcon:
        """Create and return a system tray icon used for push notifications."""
        tray = QSystemTrayIcon(self)
        icon_path = Path(__file__).parent.parent / "resources" / "app_icon_64.png"
        tray.setIcon(QIcon(str(icon_path)) if icon_path.exists() else self.windowIcon())
        tray.setToolTip(self.tr("Bethesda Strings AI Translator"))

        menu = QMenu()
        show_action = QAction(self.tr("Show"), self)
        show_action.triggered.connect(self._restore_window)
        menu.addAction(show_action)
        quit_action = QAction(self.tr("Quit"), self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)

        if QSystemTrayIcon.isSystemTrayAvailable():
            tray.show()
        return tray

    def _restore_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    @Slot(QSystemTrayIcon.ActivationReason)
    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._restore_window()

    def _init_translation_worker(self):
        """Initialize the translation worker (Ollama or Claude depending on model)."""
        self._cleanup_workers()
        enable_protection = self.settings.enable_term_protection
        model = self.settings.ollama_model

        self.ollama_thread = QThread()

        if is_claude_model(model):
            from gui.claude_client import get_api_key
            from gui.claude_translation_worker import ClaudeTranslationWorker
            api_key = get_api_key() or ""
            self.ollama_worker = ClaudeTranslationWorker(
                api_key=api_key,
                model=model,
                source_lang=self.settings.default_source_lang,
                target_lang=self.settings.default_target_lang,
                max_workers=min(self.settings.max_workers, 5),
                term_protector=self.term_protector if enable_protection else None,
                translation_cache=self.translation_cache if self.settings.enable_cache else None,
                protect_named_entities=self.settings.protect_named_entities,
            )
            self.ollama_worker.glossary_manager = self._glossary_manager
            self.ollama_worker.lore_rag_manager = self._lore_rag_manager
            self.ollama_worker.profile_manager = self._profile_manager
            self.ollama_worker.profile_assignments = self._profile_assignments
            self.ollama_worker.skipped_types = list(self.settings.skip_string_types)
            logger.info("Translation worker initialized (Claude: %s)", model)
        else:
            self.ollama_worker = OllamaWorker(
                base_url=self.settings.ollama_url,
                model=model,
                enable_term_protection=enable_protection,
                term_protector=self.term_protector if enable_protection else None,
                translation_cache=self.translation_cache if self.settings.enable_cache else None,
                max_workers=self.settings.max_workers,
                ollama_num_thread=self.settings.ollama_num_thread,
                ollama_num_predict=self.settings.ollama_num_predict,
                ollama_num_ctx=self.settings.ollama_num_ctx,
                long_string_threshold=self.settings.long_string_threshold,
                long_string_action=self.settings.long_string_action,
                protect_named_entities=self.settings.protect_named_entities,
            )
            self.ollama_worker.glossary_manager = self._glossary_manager
            self.ollama_worker.lore_rag_manager = self._lore_rag_manager
            self.ollama_worker.profile_manager = self._profile_manager
            self.ollama_worker.profile_assignments = self._profile_assignments
            self.ollama_worker.skipped_types = list(self.settings.skip_string_types)
            self.ollama_worker.tm_fuzzy_max_score = self.settings.tm_fuzzy_max_score
            logger.info("Translation worker initialized (Ollama: %s)", model)

        self.ollama_worker.moveToThread(self.ollama_thread)
        self.ollama_thread.start()

    def _cleanup_workers(self):
        """Clean up existing translation workers."""
        if self.ollama_worker:
            self.ollama_worker.stop()
        if self.ollama_thread and self.ollama_thread.isRunning():
            self.ollama_thread.quit()
            if not self.ollama_thread.wait(1000):
                self.ollama_thread.terminate()
        self.ollama_thread = None
        self.ollama_worker = None

    def _setup_ui(self):
        """Initialize user interface."""
        self._create_menus()
        self._populate_recent_sessions()
        self._create_toolbar()

        central_widget = QWidget()
        central_widget.setObjectName("MainCentralWidget")
        central_widget.setAttribute(Qt.WA_StyledBackground, True)
        central_widget.setStyleSheet("QWidget#MainCentralWidget { background: transparent; }")
        self.setCentralWidget(central_widget)
        self.setAcceptDrops(True)
        self._drop_overlay = _DropOverlay(self)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Header bar ────────────────────────────────────────────────
        header_frame = QFrame()
        header_frame.setObjectName("HeaderBar")
        header_frame.setStyleSheet(
            "QFrame#HeaderBar { border-bottom: 1px solid rgba(71,85,105,0.5); }"
        )
        header_layout = QVBoxLayout(header_frame)
        header_layout.setContentsMargins(10, 4, 10, 4)
        header_layout.setSpacing(2)

        # File info row
        info_bar = QHBoxLayout()
        info_bar.setSpacing(8)

        self.lbl_file_info = QLabel(self.tr("No file loaded"))
        self.lbl_file_info.setStyleSheet("font-weight: 600;")
        info_bar.addWidget(self.lbl_file_info)

        def _vsep():
            f = QFrame()
            f.setFrameShape(QFrame.VLine)
            f.setFixedWidth(1)
            f.setStyleSheet("background: rgba(71,85,105,0.5); border: none;")
            return f

        info_bar.addWidget(_vsep())
        self.lbl_encoding = QLabel(self.tr("Encoding: —"))
        self.lbl_encoding.setStyleSheet("font-size: 12px; opacity: 0.7;")
        info_bar.addWidget(self.lbl_encoding)
        self.btn_encoding_change = QPushButton(self.tr("Change…"))
        self.btn_encoding_change.setFlat(True)
        self.btn_encoding_change.setEnabled(False)
        self.btn_encoding_change.setToolTip(
            self.tr("Override the auto-detected file encoding and re-decode all strings")
        )
        self.btn_encoding_change.setStyleSheet(
            "QPushButton { font-size: 11px; padding: 0 4px; opacity: 0.7; }"
            "QPushButton:hover { text-decoration: underline; }"
        )
        self.btn_encoding_change.clicked.connect(self._override_encoding)
        info_bar.addWidget(self.btn_encoding_change)

        info_bar.addWidget(_vsep())
        self.lbl_string_count = QLabel(self.tr("Strings: 0"))
        self.lbl_string_count.setStyleSheet("font-size: 12px; opacity: 0.7;")
        info_bar.addWidget(self.lbl_string_count)

        info_bar.addStretch()
        header_layout.addLayout(info_bar)

        # Language + quality row
        lang_layout = QHBoxLayout()
        lang_layout.setSpacing(6)

        def _lbl(text):
            l = QLabel(text)
            l.setStyleSheet("font-size: 12px; opacity: 0.65;")
            return l

        lang_layout.addWidget(_lbl(self.tr("Source:")))
        self.combo_source_lang = QComboBox()
        self.combo_source_lang.setMinimumWidth(145)
        for display_name, lang_code in self.SUPPORTED_LANGUAGES:
            self.combo_source_lang.addItem(self.tr(display_name), lang_code)
        self.combo_source_lang.setCurrentIndex(
            self.combo_source_lang.findData(self.settings.default_source_lang)
        )
        lang_layout.addWidget(self.combo_source_lang)

        arrow = QLabel("→")
        arrow.setStyleSheet("font-size: 15px; opacity: 0.4; padding: 0 2px;")
        lang_layout.addWidget(arrow)

        lang_layout.addWidget(_lbl(self.tr("Target:")))
        self.combo_target_lang = QComboBox()
        self.combo_target_lang.setMinimumWidth(145)
        for display_name, lang_code in self.SUPPORTED_LANGUAGES:
            self.combo_target_lang.addItem(self.tr(display_name), lang_code)
        self.combo_target_lang.setCurrentIndex(
            self.combo_target_lang.findData(self.settings.default_target_lang)
        )
        lang_layout.addWidget(self.combo_target_lang)

        lang_layout.addWidget(_vsep())

        lang_layout.addWidget(_lbl(self.tr("Quality:")))
        self.spin_quality = QSpinBox()
        self.spin_quality.setRange(AppSettings._QUALITY_MIN, AppSettings._QUALITY_MAX)
        self.spin_quality.setValue(self.settings.quality_level)
        self.spin_quality.setSuffix("/10")
        self.spin_quality.setFixedWidth(72)
        self.spin_quality.setToolTip(
            self.tr("Quality 7-10 recommended")
        )
        lang_layout.addWidget(self.spin_quality)
        lang_layout.addStretch()
        header_layout.addLayout(lang_layout)

        main_layout.addWidget(header_frame)

        # ── Content stack: welcome page / string table ─────────────
        self._content_stack = QStackedWidget()

        # Page 0 — welcome
        self._welcome = _WelcomeWidget()
        self._welcome.open_requested.connect(self.open_file)
        self._welcome.file_dropped.connect(self._open_file_path)
        self._content_stack.addWidget(self._welcome)

        # Page 1 — string table
        self.table_view = StringTableView()
        self.table_view.setObjectName("MainStringTableView")
        self.table_view.setFrameShape(QTableView.NoFrame)
        self.table_view.viewport().setAutoFillBackground(False)
        self.table_view.setStyleSheet(
            """
            QTableView#MainStringTableView {
                background: transparent;
                border: none;
            }
            QTableView#MainStringTableView::item {
                background: transparent;
            }
            QTableView#MainStringTableView QTableCornerButton::section {
                background: transparent;
                border: none;
            }
            """
        )
        self.table_model = StringTableModel()
        self.table_model.set_color_blind_mode(self.settings.color_blind_mode)
        self.table_view.setModel(self.table_model)

        # Live stats: refresh on any data or layout change
        self.table_model.dataChanged.connect(
            lambda *_: self._stats_refresh_timer.start()
        )
        self.table_model.layoutChanged.connect(self._refresh_stats)
        # Session progress tracking: record any newly-translated string ID
        self.table_model.dataChanged.connect(self._session_track_datachanged)

        # Replace the default delegate with one that has a completion source.
        from gui.string_table import StringItemDelegate
        self._string_delegate = StringItemDelegate(
            self.table_view,
            completion_source=self._build_completion_list,
        )
        self.table_view.setItemDelegate(self._string_delegate)

        self._content_stack.addWidget(self.table_view)

        main_layout.addWidget(self._content_stack)

        # Progress bar (smooth animated)
        from gui.micro_animations import SmoothProgressBar
        self.progress_bar = SmoothProgressBar()
        self.progress_bar.setVisible(False)
        self.lbl_progress = QLabel("")
        progress_layout = QHBoxLayout()
        progress_layout.addWidget(self.lbl_progress)
        progress_layout.addWidget(self.progress_bar)
        main_layout.addLayout(progress_layout)

        # Status bar
        status_bar = QStatusBar()
        status_bar.setObjectName("MainStatusBar")
        status_bar.setStyleSheet(
            """
            QStatusBar#MainStatusBar {
                border-top: 0px;
                background: transparent;
            }
            QStatusBar#MainStatusBar::item {
                border: none;
            }
            """
        )
        self.setStatusBar(status_bar)

        # ── Permanent stats widgets (right side of status bar) ─────────────
        _stat_sep = QLabel("  ")
        status_bar.addPermanentWidget(_stat_sep)

        self._stat_lbl = QLabel()
        self._stat_lbl.setObjectName("StatCountsLabel")
        self._stat_lbl.setStyleSheet(
            "font-size: 11px; padding: 0 6px;"
        )
        self._stat_lbl.setToolTip(self.tr(
            "Total strings · translated · remaining\n"
            "Updates live as translations complete."
        ))
        status_bar.addPermanentWidget(self._stat_lbl)

        self._eta_lbl = QLabel()
        self._eta_lbl.setObjectName("StatEtaLabel")
        self._eta_lbl.setStyleSheet(
            "font-size: 11px; font-weight: 600; color: #f59e0b; padding: 0 8px;"
        )
        self._eta_lbl.setToolTip(self.tr("Estimated time remaining for current translation batch"))
        self._eta_lbl.setVisible(False)
        status_bar.addPermanentWidget(self._eta_lbl)

        # Debounce timer so rapid dataChanged signals don't thrash the count loop
        self._stats_refresh_timer = QTimer(self)
        self._stats_refresh_timer.setSingleShot(True)
        self._stats_refresh_timer.setInterval(250)
        self._stats_refresh_timer.timeout.connect(self._refresh_stats)

        # ETA tracking state
        self._eta_start_time: float = 0.0
        self._eta_batch_total: int = 0

        self.statusBar().showMessage(self.tr("Ready"))

        # Glossary suggest dock (hidden until a file is open)
        self._glossary_dock = QDockWidget(self.tr("Glossary Suggestions"), self)
        self._glossary_dock.setObjectName("GlossaryDock")
        self._glossary_dock.setAllowedAreas(
            Qt.BottomDockWidgetArea | Qt.RightDockWidgetArea
        )
        dock_inner = QWidget()
        dock_layout = QVBoxLayout(dock_inner)
        dock_layout.setContentsMargins(4, 4, 4, 4)
        dock_layout.setSpacing(4)
        self._glossary_src_label = QLabel(self.tr("Select a string to see glossary hints."))
        self._glossary_src_label.setWordWrap(True)
        dock_layout.addWidget(self._glossary_src_label)
        self._glossary_list = QListWidget()
        self._glossary_list.setToolTip(
            self.tr("Double-click to copy the target term to clipboard.")
        )
        self._glossary_list.itemDoubleClicked.connect(self._on_glossary_item_double_clicked)
        dock_layout.addWidget(self._glossary_list, stretch=1)
        self._glossary_dock.setWidget(dock_inner)
        self.addDockWidget(Qt.BottomDockWidgetArea, self._glossary_dock)
        self._glossary_dock.hide()

        # ── Claude AI Assistant dock ──────────────────────────────────────────
        from gui.claude_chat_panel import ClaudeChatPanel
        self._claude_panel = ClaudeChatPanel(self)
        self.addDockWidget(Qt.RightDockWidgetArea, self._claude_panel)
        self._claude_panel.hide()
        self._claude_panel.apply_translation.connect(self._apply_claude_translation)

        # ── Audio / TTS Preview dock ──────────────────────────────────────────
        from gui.audio_preview_panel import AudioPreviewPanel
        self._audio_panel = AudioPreviewPanel(self)
        self.addDockWidget(Qt.BottomDockWidgetArea, self._audio_panel)
        self._audio_panel.setVisible(self.settings.enable_audio_preview)
        self._apply_audio_settings()

        # ── Visual Context Preview dock ───────────────────────────────────────
        from gui.visual_context_preview import VisualContextPreview
        self._visual_preview = VisualContextPreview(self)
        self.addDockWidget(Qt.BottomDockWidgetArea, self._visual_preview)
        self._visual_preview.hide()

        # ── Translation Editor Pane dock (hidden by default) ──────────────────
        from gui.translation_editor_pane import TranslationEditorPane
        self._editor_pane = TranslationEditorPane(self)
        self.addDockWidget(Qt.RightDockWidgetArea, self._editor_pane)
        self._editor_pane.hide()
        self._editor_pane.translation_approved.connect(self._on_editor_pane_approved)

        # ── Detached table window reference (None until user opens it) ────────
        self._detached_table: Optional["DetachedTableWindow"] = None  # noqa: F821  # pyright: ignore[reportUndefinedVariable]

        # ── Dock / window state persistence ───────────────────────────────────
        self._restore_window_state()

    def _create_menus(self):
        """Create menu bar."""
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu(self.tr("&File"))
        open_action = QAction(self.tr("&Open..."), self, shortcut=QKeySequence("Ctrl+O"))
        open_action.setIcon(QIcon.fromTheme("document-open"))
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)

        self._recent_menu = file_menu.addMenu(self.tr("Open &Recent"))
        self._recent_menu.setIcon(QIcon.fromTheme("document-open-recent"))
        self._rebuild_recent_menu()

        self.save_action = QAction(self.tr("&Save"), self, shortcut=QKeySequence("Ctrl+S"))
        self.save_action.setIcon(QIcon.fromTheme("document-save"))
        self.save_action.triggered.connect(self.save_file)
        self.save_action.setEnabled(False)
        file_menu.addAction(self.save_action)

        self.save_as_action = QAction(
            self.tr("Save &As..."), self, shortcut=QKeySequence("Ctrl+Shift+S")
        )
        self.save_as_action.setIcon(QIcon.fromTheme("document-save-as"))
        self.save_as_action.triggered.connect(self.save_file_as)
        self.save_as_action.setEnabled(False)
        file_menu.addAction(self.save_as_action)

        file_menu.addSeparator()
        nexusmods_action = QAction(self.tr("Upload to &NexusMods…"), self)
        nexusmods_action.setIcon(QIcon.fromTheme("network-transmit"))
        nexusmods_action.triggered.connect(self._open_nexusmods_upload)
        file_menu.addAction(nexusmods_action)

        nexusmods_browse_action = QAction(self.tr("&Browse NexusMods for Translations…"), self)
        nexusmods_browse_action.setIcon(QIcon.fromTheme("network-receive"))
        nexusmods_browse_action.triggered.connect(self._open_nexusmods_browser)
        file_menu.addAction(nexusmods_browse_action)

        weblate_action = QAction(self.tr("&Weblate Community Sync…"), self)
        weblate_action.setIcon(QIcon.fromTheme("network-server"))
        weblate_action.triggered.connect(self._open_weblate_sync)
        file_menu.addAction(weblate_action)

        file_menu.addSeparator()
        exit_action = QAction(self.tr("E&xit"), self, shortcut=QKeySequence("Ctrl+Q"))
        exit_action.setIcon(QIcon.fromTheme("application-exit"))
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Edit menu
        edit_menu = menubar.addMenu(self.tr("&Edit"))
        self.search_action = QAction(
            self.tr("&Advanced Search..."), self, shortcut=QKeySequence("Ctrl+F")
        )
        self.search_action.setIcon(QIcon.fromTheme("edit-find"))
        self.search_action.triggered.connect(self.open_advanced_search)
        self.search_action.setEnabled(False)
        edit_menu.addAction(self.search_action)

        edit_menu.addSeparator()

        self.fill_from_original_action = QAction(
            self.tr("Copy &Original → Translated"), self, shortcut=QKeySequence("Ctrl+Shift+V")
        )
        self.fill_from_original_action.setIcon(QIcon.fromTheme("edit-copy"))
        self.fill_from_original_action.triggered.connect(
            lambda: self.table_view._fill_translated_from_source()
        )
        self.fill_from_original_action.setEnabled(False)
        edit_menu.addAction(self.fill_from_original_action)

        # Translation menu
        trans_menu = menubar.addMenu(self.tr("&Translation"))
        self.translate_selected_action = QAction(
            self.tr("Translate &Selected"), self, shortcut=QKeySequence("Ctrl+T")
        )
        self.translate_selected_action.setIcon(QIcon.fromTheme("edit-translate"))
        self.translate_selected_action.triggered.connect(self.translate_selected)
        self.translate_selected_action.setEnabled(False)
        trans_menu.addAction(self.translate_selected_action)

        self.translate_all_action = QAction(
            self.tr("Translate &All"), self, shortcut=QKeySequence("Ctrl+Shift+A")
        )
        self.translate_all_action.setIcon(QIcon.fromTheme("media-playback-start"))
        self.translate_all_action.triggered.connect(self.translate_all)
        self.translate_all_action.setEnabled(False)
        trans_menu.addAction(self.translate_all_action)

        trans_menu.addSeparator()
        self.stop_translation_action = QAction(
            self.tr("Stop Translation"), self, shortcut=QKeySequence("Escape")
        )
        self.stop_translation_action.setIcon(QIcon.fromTheme("process-stop"))
        self.stop_translation_action.triggered.connect(self._stop_translation)
        self.stop_translation_action.setEnabled(False)
        trans_menu.addAction(self.stop_translation_action)

        trans_menu.addSeparator()
        self.import_txt_action = QAction(
            self.tr("Import from &TXT..."), self, shortcut=QKeySequence("Ctrl+I")
        )
        self.import_txt_action.setIcon(QIcon.fromTheme("document-import"))
        self.import_txt_action.triggered.connect(self.import_from_txt)
        self.import_txt_action.setEnabled(False)
        trans_menu.addAction(self.import_txt_action)

        self.export_txt_action = QAction(
            self.tr("Export to &TXT..."), self, shortcut=QKeySequence("Ctrl+E")
        )
        self.export_txt_action.setIcon(QIcon.fromTheme("document-export"))
        self.export_txt_action.triggered.connect(self.export_to_txt)
        self.export_txt_action.setEnabled(False)
        trans_menu.addAction(self.export_txt_action)

        trans_menu.addSeparator()
        self.import_xml_action = QAction(self.tr("Import from &XML (SST)..."), self)
        self.import_xml_action.setIcon(QIcon.fromTheme("document-import"))
        self.import_xml_action.triggered.connect(self.import_from_xml)
        self.import_xml_action.setEnabled(False)
        trans_menu.addAction(self.import_xml_action)

        self.export_xml_action = QAction(self.tr("Export to &XML (SST)..."), self)
        self.export_xml_action.setIcon(QIcon.fromTheme("document-export"))
        self.export_xml_action.triggered.connect(self.export_to_xml)
        self.export_xml_action.setEnabled(False)
        trans_menu.addAction(self.export_xml_action)

        trans_menu.addSeparator()
        self.compare_action = QAction(
            self.tr("Compare with &File..."), self, shortcut=QKeySequence("Ctrl+D")
        )
        self.compare_action.setIcon(QIcon.fromTheme("view-split-top-bottom"))
        self.compare_action.triggered.connect(self.compare_with_file)
        self.compare_action.setEnabled(False)
        trans_menu.addAction(self.compare_action)

        self.diff_viewer_action = QAction(
            self.tr("String &Diff Viewer..."), self, shortcut=QKeySequence("Ctrl+Shift+D")
        )
        self.diff_viewer_action.setIcon(QIcon.fromTheme("view-split-left-right"))
        self.diff_viewer_action.triggered.connect(self._open_diff_viewer)
        self.diff_viewer_action.setEnabled(False)
        trans_menu.addAction(self.diff_viewer_action)

        self.dialogue_tree_action = QAction(
            self.tr("Dialogue &Tree Visualizer…"), self, shortcut=QKeySequence("Ctrl+Shift+T")
        )
        self.dialogue_tree_action.setIcon(QIcon.fromTheme("view-list-tree"))
        self.dialogue_tree_action.setToolTip(self.tr(
            "Visualise the Quest → Topic → Response dialogue tree from an ESP/ESM file.\n"
            "Shows conversation flow as a node graph so translators can see context."
        ))
        self.dialogue_tree_action.triggered.connect(self._open_dialogue_tree)
        self.dialogue_tree_action.setEnabled(False)
        trans_menu.addAction(self.dialogue_tree_action)

        self.lore_rag_action = QAction(self.tr("Lore &RAG Context…"), self)
        self.lore_rag_action.setIcon(QIcon.fromTheme("document-properties"))
        self.lore_rag_action.setToolTip(self.tr(
            "Manage the local lore database used for Retrieval-Augmented Generation.\n"
            "Download articles from UESP or import a local JSON file to give the AI\n"
            "accurate lore context when translating strings mentioning factions, places,\n"
            "or characters (e.g. House Va'ruun, Akila City, Freestar Collective)."
        ))
        self.lore_rag_action.triggered.connect(self._open_lore_rag_dialog)
        trans_menu.addAction(self.lore_rag_action)

        self.character_profiles_action = QAction(self.tr("&Character Profiles…"), self)
        self.character_profiles_action.setIcon(QIcon.fromTheme("user-identity"))
        self.character_profiles_action.setToolTip(self.tr(
            "Create and manage character personas (Freestar Ranger, SysDef Officer, …).\n"
            "Assign profiles to strings via right-click; the AI will adapt its register,\n"
            "tone, and temperature to match the character's voice."
        ))
        self.character_profiles_action.triggered.connect(self._open_character_profiles)
        trans_menu.addAction(self.character_profiles_action)

        self.font_checker_action = QAction(self.tr("Font &Glyph Checker…"), self)
        self.font_checker_action.setIcon(QIcon.fromTheme("font-x-generic"))
        self.font_checker_action.setToolTip(self.tr(
            "Scan translated strings for characters that will render as missing\n"
            "glyphs (tofu □) in-game due to incomplete font atlas coverage.\n"
            "Supports Scaleform SWF font atlases and TTF/OTF fonts."
        ))
        self.font_checker_action.triggered.connect(self._open_font_checker)
        self.font_checker_action.setEnabled(False)
        trans_menu.addAction(self.font_checker_action)

        self.version_compare_action = QAction(
            self.tr("Compare Game &Versions…"), self
        )
        self.version_compare_action.setIcon(QIcon.fromTheme("view-history"))
        self.version_compare_action.setShortcut(QKeySequence("Ctrl+Alt+V"))
        self.version_compare_action.setToolTip(self.tr(
            "Compare two game-version source files to see what strings were\n"
            "added, removed, or modified, and migrate unchanged translations."
        ))
        self.version_compare_action.triggered.connect(self._compare_game_versions)
        trans_menu.addAction(self.version_compare_action)

        self.batch_compare_action = QAction(
            self.tr("Batch Compare Game &Folders…"), self
        )
        self.batch_compare_action.setIcon(QIcon.fromTheme("folder-sync"))
        self.batch_compare_action.setToolTip(self.tr(
            "Compare all .strings files across two game-version folders\n"
            "and generate a combined migration report."
        ))
        self.batch_compare_action.triggered.connect(self._batch_compare_folders)
        trans_menu.addAction(self.batch_compare_action)

        trans_menu.addSeparator()
        self.translate_interface_action = QAction(
            self.tr("Translate Starfield Interface TXT..."), self
        )
        self.translate_interface_action.setIcon(QIcon.fromTheme("edit-translate"))
        self.translate_interface_action.triggered.connect(self.translate_starfield_txt)
        trans_menu.addAction(self.translate_interface_action)

        trans_menu.addSeparator()
        self.approve_action = QAction(self.tr("&Approve Selected"), self)
        self.approve_action.setIcon(QIcon.fromTheme("dialog-ok-apply"))
        self.approve_action.setShortcut("Ctrl+Return")
        self.approve_action.setToolTip(
            self.tr("Accept the current AI translation and advance to the next row (Ctrl+Enter)")
        )
        self.approve_action.triggered.connect(self._approve_selected)
        self.approve_action.setEnabled(False)
        trans_menu.addAction(self.approve_action)

        self.reject_action = QAction(self.tr("&Reject Selected"), self)
        self.reject_action.setIcon(QIcon.fromTheme("edit-delete"))
        self.reject_action.setShortcut("Ctrl+R")
        self.reject_action.setToolTip(
            self.tr("Clear the translation for selected rows and mark them as pending (Ctrl+R)")
        )
        self.reject_action.triggered.connect(self._reject_selected)
        self.reject_action.setEnabled(False)
        trans_menu.addAction(self.reject_action)

        trans_menu.addSeparator()
        self.next_untranslated_action = QAction(self.tr("&Next Untranslated"), self)
        self.next_untranslated_action.setIcon(QIcon.fromTheme("go-next"))
        self.next_untranslated_action.setShortcut("F7")
        self.next_untranslated_action.setToolTip(
            self.tr("Jump to the next untranslated string (F7)")
        )
        self.next_untranslated_action.triggered.connect(self._next_untranslated)
        self.next_untranslated_action.setEnabled(False)
        trans_menu.addAction(self.next_untranslated_action)

        self.prev_untranslated_action = QAction(self.tr("&Previous Untranslated"), self)
        self.prev_untranslated_action.setIcon(QIcon.fromTheme("go-previous"))
        self.prev_untranslated_action.setShortcut("Shift+F7")
        self.prev_untranslated_action.setToolTip(
            self.tr("Jump to the previous untranslated string (Shift+F7)")
        )
        self.prev_untranslated_action.triggered.connect(self._prev_untranslated)
        self.prev_untranslated_action.setEnabled(False)
        trans_menu.addAction(self.prev_untranslated_action)

        self.batch_translate_action = QAction(self.tr("&Batch Translate Folder…"), self)
        self.batch_translate_action.setIcon(QIcon.fromTheme("folder-sync"))
        self.batch_translate_action.setToolTip(self.tr(
            "Scan a folder of binary string files (.strings/.dlstrings/.ilstrings),\n"
            "auto-fix mechanical issues, and AI-translate untranslated/poor-quality strings."
        ))
        self.batch_translate_action.triggered.connect(self._open_batch_translate_dialog)
        trans_menu.addAction(self.batch_translate_action)

        trans_menu.addSeparator()
        self.quality_check_action = QAction(self.tr("&Quality Check…"), self)
        self.quality_check_action.setIcon(QIcon.fromTheme("dialog-warning"))
        self.quality_check_action.setShortcut("Ctrl+F7")
        self.quality_check_action.setToolTip(self.tr("Run post-translation quality checks (Ctrl+F7)"))
        self.quality_check_action.triggered.connect(self._run_quality_check)
        self.quality_check_action.setEnabled(False)
        trans_menu.addAction(self.quality_check_action)

        self.auto_retranslate_action = QAction(self.tr("Auto-Retranslate &Issues…"), self)
        self.auto_retranslate_action.setIcon(QIcon.fromTheme("view-refresh"))
        self.auto_retranslate_action.setShortcut("Ctrl+Shift+F7")
        self.auto_retranslate_action.setToolTip(
            self.tr(
                "Run quality check and automatically retranslate all strings "
                "with errors or warnings, sending quality feedback to the AI model. (Ctrl+Shift+F7)"
            )
        )
        self.auto_retranslate_action.triggered.connect(self._auto_retranslate_errors)
        self.auto_retranslate_action.setEnabled(False)
        trans_menu.addAction(self.auto_retranslate_action)

        self.macro_action = QAction(self.tr("&Macro Editor… (q)"), self)
        self.macro_action.setIcon(QIcon.fromTheme("media-record"))
        self.macro_action.setShortcut("Ctrl+M")
        self.macro_action.setToolTip(
            self.tr(
                "Open the macro editor to define regex-replace steps and apply\n"
                "them to thousands of strings in one batch. (Ctrl+M or 'q' in table)"
            )
        )
        self.macro_action.triggered.connect(self._open_macro_dialog)
        self.macro_action.setEnabled(False)
        trans_menu.addAction(self.macro_action)

        self.import_quality_action = QAction(
            self.tr("&Import Quality Report…"), self
        )
        self.import_quality_action.setIcon(QIcon.fromTheme("document-import"))
        self.import_quality_action.setToolTip(
            self.tr(
                "Load a previously exported JSON quality report.\n"
                "Row positions are remapped to the current file automatically.\n"
                "Use this to restore quality check results after reloading the app."
            )
        )
        self.import_quality_action.triggered.connect(self._import_quality_report)
        self.import_quality_action.setEnabled(False)
        trans_menu.addAction(self.import_quality_action)

        trans_menu.addSeparator()
        self.export_training_data_action = QAction(
            self.tr("Export &Training Data (JSONL)…"), self
        )
        self.export_training_data_action.setIcon(QIcon.fromTheme("document-export"))
        self.export_training_data_action.setToolTip(self.tr(
            "Export approved translations as a JSONL fine-tuning dataset.\n"
            "Compatible with Unsloth, Axolotl, and LLaMA-Factory.\n"
            "Only rows with status 'translated' are included."
        ))
        self.export_training_data_action.triggered.connect(self._export_training_data)
        self.export_training_data_action.setEnabled(False)
        trans_menu.addAction(self.export_training_data_action)

        trans_menu.addSeparator()
        self.load_memory_action = QAction(
            self.tr("Load Translation &Memory..."), self
        )
        self.load_memory_action.setIcon(QIcon.fromTheme("document-open"))
        self.load_memory_action.triggered.connect(self._load_translation_memory)
        trans_menu.addAction(self.load_memory_action)

        self.export_memory_action = QAction(
            self.tr("Export Translation Memory as TMX..."), self
        )
        self.export_memory_action.setIcon(QIcon.fromTheme("document-export"))
        self.export_memory_action.setToolTip(self.tr(
            "Export the active translation memory (or current file's translations)\n"
            "as a TMX file compatible with OmegaT, SDL Trados, and Memsource."
        ))
        self.export_memory_action.triggered.connect(self._export_translation_memory)
        trans_menu.addAction(self.export_memory_action)

        trans_menu.addSeparator()
        self.discover_terms_action = QAction(
            self.tr("&Discover New Terms…"), self
        )
        self.discover_terms_action.setIcon(QIcon.fromTheme("system-search"))
        self.discover_terms_action.setToolTip(self.tr(
            "Scan the loaded strings for candidate protected terms not yet in the\n"
            "protection list, then review and approve them before adding."
        ))
        self.discover_terms_action.triggered.connect(self._discover_terms)
        self.discover_terms_action.setEnabled(False)
        trans_menu.addAction(self.discover_terms_action)

        self.check_consistency_action = QAction(
            self.tr("&Check Consistency…"), self
        )
        self.check_consistency_action.setIcon(QIcon.fromTheme("emblem-important"))
        self.check_consistency_action.setShortcut("Ctrl+Alt+K")
        self.check_consistency_action.setToolTip(self.tr(
            "Scan all translated strings for the same source text rendered\n"
            "differently and let you pick a canonical translation for each group."
        ))
        self.check_consistency_action.triggered.connect(self._check_consistency)
        self.check_consistency_action.setEnabled(False)
        trans_menu.addAction(self.check_consistency_action)

        self.register_check_action = QAction(
            self.tr("Check &Register (ти/ви)…"), self
        )
        self.register_check_action.setIcon(QIcon.fromTheme("format-text-direction-ltr"))
        self.register_check_action.setShortcut("Ctrl+Alt+R")
        self.register_check_action.setToolTip(self.tr(
            "Detect NPC speakers whose translated lines mix informal (ти) and\n"
            "formal (ви) address when speaking to the player. (Ctrl+Alt+R)"
        ))
        self.register_check_action.triggered.connect(self._check_register)
        self.register_check_action.setEnabled(False)
        trans_menu.addAction(self.register_check_action)

        self.gender_check_action = QAction(
            self.tr("Check &Gender Agreement…"), self
        )
        self.gender_check_action.setIcon(QIcon.fromTheme("format-text-italic"))
        self.gender_check_action.setShortcut("Ctrl+Alt+G")
        self.gender_check_action.setToolTip(self.tr(
            "Scan translated strings for adjective/noun gender agreement\n"
            "errors (Ukrainian grammar). (Ctrl+Alt+G)"
        ))
        self.gender_check_action.triggered.connect(self._check_gender_agreement)
        self.gender_check_action.setEnabled(False)
        trans_menu.addAction(self.gender_check_action)

        # ── Sessions menu ─────────────────────────────────────────────────────
        self._sessions_menu = menubar.addMenu(self.tr("&Sessions"))

        self._session_new_action = QAction(
            self.tr("&New Session…"), self)
        self._session_new_action.setShortcut("Ctrl+Shift+N")
        self._session_new_action.setIcon(QIcon.fromTheme("document-new"))
        self._session_new_action.setToolTip(self.tr(
            "Start a named work session that saves your search filter, "
            "cursor, and per-session translation count. (Ctrl+Shift+N)"
        ))
        self._session_new_action.triggered.connect(self._session_new)
        self._sessions_menu.addAction(self._session_new_action)

        self._session_save_action = QAction(
            self.tr("&Save Session"), self)
        self._session_save_action.setShortcut("Ctrl+Shift+S")
        self._session_save_action.setIcon(QIcon.fromTheme("document-save"))
        self._session_save_action.setEnabled(False)
        self._session_save_action.triggered.connect(self._session_save)
        self._sessions_menu.addAction(self._session_save_action)

        self._session_save_as_action = QAction(
            self.tr("Save Session &As…"), self)
        self._session_save_as_action.setIcon(QIcon.fromTheme("document-save-as"))
        self._session_save_as_action.setEnabled(False)
        self._session_save_as_action.triggered.connect(self._session_save_as)
        self._sessions_menu.addAction(self._session_save_as_action)

        self._sessions_menu.addSeparator()

        self._session_manage_action = QAction(
            self.tr("&Manage Sessions…"), self)
        self._session_manage_action.setIcon(QIcon.fromTheme("view-list-details"))
        self._session_manage_action.triggered.connect(self._open_session_manager)
        self._sessions_menu.addAction(self._session_manage_action)

        self._sessions_menu.addSeparator()
        self._sessions_recent_menu = self._sessions_menu.addMenu(
            self.tr("Recent Sessions"))
        self._sessions_recent_menu.setIcon(QIcon.fromTheme("document-open-recent"))

        # Glossary menu
        glossary_menu = menubar.addMenu(self.tr("&Glossary"))
        self.glossary_editor_action = QAction(self.tr("&Edit Glossary…"), self)
        self.glossary_editor_action.setIcon(QIcon.fromTheme("accessories-dictionary"))
        self.glossary_editor_action.setShortcut("Ctrl+G")
        self.glossary_editor_action.triggered.connect(self._open_glossary_editor)
        glossary_menu.addAction(self.glossary_editor_action)

        self.glossary_suggest_action = QAction(self.tr("&Show Suggestions Panel"), self)
        self.glossary_suggest_action.setIcon(QIcon.fromTheme("view-list-text"))
        self.glossary_suggest_action.setCheckable(True)
        self.glossary_suggest_action.setChecked(False)
        self.glossary_suggest_action.triggered.connect(self._toggle_glossary_dock)
        glossary_menu.addAction(self.glossary_suggest_action)

        glossary_menu.addSeparator()
        self.glossary_quality_action = QAction(
            self.tr("Check &Glossary Compliance…"), self
        )
        self.glossary_quality_action.setIcon(QIcon.fromTheme("emblem-default"))
        self.glossary_quality_action.setEnabled(False)
        self.glossary_quality_action.triggered.connect(self._run_glossary_check)
        glossary_menu.addAction(self.glossary_quality_action)

        # Claude AI menu
        claude_menu = menubar.addMenu(self.tr("&Claude AI"))

        self.claude_panel_action = QAction(self.tr("Show &AI Assistant"), self)
        self.claude_panel_action.setIcon(QIcon.fromTheme("help-contextual"))
        self.claude_panel_action.setShortcut("Ctrl+Shift+C")
        self.claude_panel_action.setCheckable(True)
        self.claude_panel_action.setChecked(False)
        self.claude_panel_action.setToolTip(
            self.tr("Show/hide the Claude AI chat assistant panel (Ctrl+Shift+C)")
        )
        self.claude_panel_action.triggered.connect(self._toggle_claude_panel)
        claude_menu.addAction(self.claude_panel_action)

        claude_menu.addSeparator()

        self.claude_review_action = QAction(self.tr("&Review Current Translation"), self)
        self.claude_review_action.setIcon(QIcon.fromTheme("document-properties"))
        self.claude_review_action.setShortcut("Ctrl+Shift+R")
        self.claude_review_action.setToolTip(
            self.tr(
                "Ask Claude to review the selected string's translation "
                "for quality issues (Ctrl+Shift+R)"
            )
        )
        self.claude_review_action.triggered.connect(self._claude_review_current)
        self.claude_review_action.setEnabled(False)
        claude_menu.addAction(self.claude_review_action)

        self.claude_suggest_action = QAction(self.tr("&Suggest Translation"), self)
        self.claude_suggest_action.setIcon(QIcon.fromTheme("help-hint"))
        self.claude_suggest_action.setShortcut("Ctrl+Shift+T")
        self.claude_suggest_action.setToolTip(
            self.tr(
                "Ask Claude to translate the current string "
                "(result shown in AI Assistant panel) (Ctrl+Shift+T)"
            )
        )
        self.claude_suggest_action.triggered.connect(self._claude_suggest_current)
        self.claude_suggest_action.setEnabled(False)
        claude_menu.addAction(self.claude_suggest_action)

        # View menu — panel toggles
        view_menu = menubar.addMenu(self.tr("&View"))

        self.focus_mode_action = QAction(self.tr("&Zen / Focus Mode"), self)
        self.focus_mode_action.setIcon(QIcon.fromTheme("view-fullscreen"))
        self.focus_mode_action.setShortcut("F11")
        self.focus_mode_action.setCheckable(True)
        self.focus_mode_action.setToolTip(self.tr(
            "Hide all panels and enter a distraction-free single-string editor (F11)"
        ))
        self.focus_mode_action.triggered.connect(self._toggle_focus_mode)
        view_menu.addAction(self.focus_mode_action)

        view_menu.addSeparator()

        self.editor_pane_action = QAction(self.tr("&Editor Pane"), self)
        self.editor_pane_action.setIcon(QIcon.fromTheme("accessories-text-editor"))
        self.editor_pane_action.setShortcut("Ctrl+Shift+E")
        self.editor_pane_action.setCheckable(True)
        self.editor_pane_action.setToolTip(self.tr(
            "Show/hide the Translation Editor pane — a larger editing area "
            "that can be dragged to a second monitor (Ctrl+Shift+E)"
        ))
        self.editor_pane_action.triggered.connect(self._toggle_editor_pane)
        view_menu.addAction(self.editor_pane_action)

        self.detach_table_action = QAction(self.tr("&Pop Out String List"), self)
        self.detach_table_action.setIcon(QIcon.fromTheme("window-new"))
        self.detach_table_action.setShortcut("Ctrl+Shift+L")
        self.detach_table_action.setCheckable(True)
        self.detach_table_action.setToolTip(self.tr(
            "Open the string list in a separate window "
            "— ideal for placing on a second monitor (Ctrl+Shift+L)"
        ))
        self.detach_table_action.triggered.connect(self._toggle_detached_table)
        view_menu.addAction(self.detach_table_action)

        view_menu.addSeparator()

        self.audio_panel_action = QAction(self.tr("&Audio Preview"), self)
        self.audio_panel_action.setIcon(QIcon.fromTheme("media-playback-start"))
        self.audio_panel_action.setShortcut("Ctrl+Shift+A")
        self.audio_panel_action.setCheckable(True)
        self.audio_panel_action.setChecked(self.settings.enable_audio_preview)
        self.audio_panel_action.setToolTip(
            self.tr("Show/hide the Audio Preview panel (Ctrl+Shift+A)")
        )
        self.audio_panel_action.triggered.connect(self._toggle_audio_panel)
        view_menu.addAction(self.audio_panel_action)

        self.visual_preview_action = QAction(self.tr("&Visual Context Preview"), self)
        self.visual_preview_action.setIcon(QIcon.fromTheme("image-x-generic"))
        self.visual_preview_action.setShortcut("Ctrl+Shift+P")
        self.visual_preview_action.setCheckable(True)
        self.visual_preview_action.setChecked(False)
        self.visual_preview_action.setToolTip(
            self.tr(
                "Show/hide the Visual Context Preview panel — renders the current "
                "string in a faithful Bethesda UI box using the actual game fonts "
                "(Ctrl+Shift+P)"
            )
        )
        self.visual_preview_action.triggered.connect(self._toggle_visual_preview)
        view_menu.addAction(self.visual_preview_action)

        # Settings menu
        settings_menu = menubar.addMenu(self.tr("&Settings"))
        self.command_palette_action = QAction(self.tr("&Command Palette…"), self)
        self.command_palette_action.setIcon(QIcon.fromTheme("system-run"))
        self.command_palette_action.setShortcut("Ctrl+K")
        self.command_palette_action.setToolTip(
            self.tr("Open the searchable command palette (Ctrl+K)")
        )
        self.command_palette_action.triggered.connect(self._open_command_palette)
        settings_menu.addAction(self.command_palette_action)

        settings_menu.addSeparator()
        settings_action = QAction(self.tr("&Preferences..."), self, shortcut=QKeySequence("Ctrl+,"))
        settings_action.setIcon(QIcon.fromTheme("preferences-system"))
        settings_action.triggered.connect(self.open_settings)
        settings_menu.addAction(settings_action)

        settings_menu.addSeparator()
        config_file_action = QAction(self.tr("Open &Config File..."), self)
        config_file_action.setIcon(QIcon.fromTheme("text-editor"))
        config_file_action.triggered.connect(self._open_config_file)
        settings_menu.addAction(config_file_action)
        export_settings_action = QAction(self.tr("Export Sett&ings..."), self)
        export_settings_action.setIcon(QIcon.fromTheme("document-export"))
        export_settings_action.triggered.connect(self._export_settings)
        settings_menu.addAction(export_settings_action)
        import_settings_action = QAction(self.tr("Import Sett&ings..."), self)
        import_settings_action.setIcon(QIcon.fromTheme("document-import"))
        import_settings_action.triggered.connect(self._import_settings)
        settings_menu.addAction(import_settings_action)

        # Help menu
        help_menu = menubar.addMenu(self.tr("&Help"))

        whats_this_action = QWhatsThis.createAction(self)
        whats_this_action.setText(self.tr("&What's This?"))
        whats_this_action.setShortcut(QKeySequence("Shift+F1"))
        help_menu.addAction(whats_this_action)

        help_menu.addSeparator()

        shortcuts_action = QAction(self.tr("&Keyboard Shortcuts…"), self)
        shortcuts_action.setIcon(QIcon.fromTheme("input-keyboard"))
        shortcuts_action.setShortcut(QKeySequence("F1"))
        shortcuts_action.triggered.connect(self._show_shortcuts_dialog)
        help_menu.addAction(shortcuts_action)

        help_menu.addSeparator()

        about_action = QAction(self.tr("&About…"), self)
        about_action.setIcon(QIcon.fromTheme("help-about"))
        about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_action)

    def _create_toolbar(self):
        """Create toolbar."""
        toolbar = QToolBar(self.tr("Main Toolbar"))
        toolbar.setObjectName("MainToolBar")
        toolbar.setIconSize(QSize(24, 24))
        self.addToolBar(toolbar)

        toolbar.addAction(
            QIcon.fromTheme("document-open"), self.tr("Open"), self.open_file
        )
        toolbar.addAction(
            QIcon.fromTheme("document-save"), self.tr("Save"), self.save_file
        )
        toolbar.addSeparator()
        toolbar.addAction(
            QIcon.fromTheme("edit-translate"),
            self.tr("Translate"),
            self.translate_selected,
        )
        toolbar.addAction(
            QIcon.fromTheme("process-stop"), self.tr("Stop"), self._stop_translation
        )
        toolbar.addSeparator()
        toolbar.addAction(
            QIcon.fromTheme("edit-find"), self.tr("Search"), self.open_advanced_search
        )
        toolbar.addSeparator()
        toolbar.addAction(
            QIcon.fromTheme("dialog-warning"),
            self.tr("Quality Check"),
            self._run_quality_check,
        )
        toolbar.addSeparator()
        toolbar.addAction(
            QIcon.fromTheme("preferences-system"),
            self.tr("Settings"),
            self.open_settings,
        )
        toolbar.addSeparator()
        toolbar.addAction(self.approve_action)
        toolbar.addAction(self.reject_action)
        toolbar.addSeparator()
        toolbar.addAction(self.next_untranslated_action)
        toolbar.addSeparator()
        toolbar.addAction(self.glossary_editor_action)
        toolbar.addAction(self.claude_panel_action)

    def _connect_signals(self):
        """Connect Qt signals."""
        self.table_view.selectionModel().selectionChanged.connect(
            self._on_selection_changed
        )
        self.table_model.string_manually_corrected.connect(self._on_string_corrected)
        self.table_view.assign_profile_requested.connect(self._open_profile_assign)
        self.table_view.macro_open_requested.connect(self._open_macro_dialog)
        self.table_view.macro_replay_requested.connect(self._replay_macro_on_current)

        # Debounce glossary dock refresh so rapid arrow-key navigation doesn't
        # fire a 20K-entry search on every intermediate row.
        self._glossary_refresh_timer = QTimer(self)
        self._glossary_refresh_timer.setSingleShot(True)
        self._glossary_refresh_timer.setInterval(200)
        self._glossary_refresh_timer.timeout.connect(self._refresh_glossary_dock)

        # 60fps coalescing timer: accumulate translation_ready signals and flush
        # all pending model updates in one batch per frame instead of one per signal.
        self._pending_translation_updates: list = []
        self._update_flush_timer = QTimer(self)
        self._update_flush_timer.setInterval(16)  # ~60 fps
        self._update_flush_timer.timeout.connect(self._flush_translation_updates)

        # Connect to active worker
        self._connect_worker_signals()

    def _connect_worker_signals(self):
        """Connect signals from the Ollama translation worker.

        Uses a flag to prevent duplicate connections when called multiple times.
        """
        # Skip if already connected
        if getattr(self, "_worker_signals_connected", False):
            return

        if self.ollama_worker:
            self.translation_requested.connect(
                self.ollama_worker.translate_batch, Qt.QueuedConnection
            )
            self.ollama_worker.translation_ready.connect(self._on_translation_ready)
            self.ollama_worker.progress.connect(self._on_ollama_progress)
            self.ollama_worker.error.connect(self._on_ollama_error)
            self.ollama_worker.finished.connect(self._on_ollama_finished)
            self._worker_signals_connected = True

    def _disconnect_worker_signals(self):
        """Disconnect all worker signal connections.

        Clears the flag so _connect_worker_signals can reconnect.
        """
        self._worker_signals_connected = False

        # Disconnect all connections from our translation_requested signal
        try:
            self.translation_requested.disconnect()
        except (RuntimeError, SystemError):
            pass

        # Disconnect worker signals (clear all receivers)
        worker = self.ollama_worker
        if worker:
            try:
                worker.translation_ready.disconnect()
            except (RuntimeError, SystemError):
                pass
            try:
                worker.progress.disconnect()
            except (RuntimeError, SystemError):
                pass
            try:
                worker.error.disconnect()
            except (RuntimeError, SystemError):
                pass
            try:
                worker.finished.disconnect()
            except (RuntimeError, SystemError):
                pass

    def _update_ui_state(self):
        """Update UI enabled/disabled state."""
        has_file = self.current_file is not None
        has_selection = self.table_view.selectionModel().hasSelection()

        # Switch between welcome page (0) and table page (1)
        self._content_stack.setCurrentIndex(1 if has_file else 0)

        self.save_action.setEnabled(has_file)
        self.save_as_action.setEnabled(has_file)
        self.translate_selected_action.setEnabled(has_file and has_selection)
        self.translate_all_action.setEnabled(has_file)
        self.import_txt_action.setEnabled(has_file)
        self.export_txt_action.setEnabled(has_file)
        self.import_xml_action.setEnabled(has_file)
        self.export_xml_action.setEnabled(has_file)
        self.search_action.setEnabled(has_file)
        self.fill_from_original_action.setEnabled(has_file and has_selection)
        self.compare_action.setEnabled(has_file)
        if hasattr(self, "diff_viewer_action"):
            self.diff_viewer_action.setEnabled(has_file)
        if hasattr(self, "dialogue_tree_action"):
            self.dialogue_tree_action.setEnabled(has_file)
        if hasattr(self, "quality_check_action"):
            self.quality_check_action.setEnabled(has_file)
        if hasattr(self, "auto_retranslate_action"):
            self.auto_retranslate_action.setEnabled(has_file)
        if hasattr(self, "import_quality_action"):
            self.import_quality_action.setEnabled(has_file)
        if hasattr(self, "export_training_data_action"):
            self.export_training_data_action.setEnabled(has_file)
        if hasattr(self, "discover_terms_action"):
            self.discover_terms_action.setEnabled(has_file and self.term_protector is not None)
        if hasattr(self, "check_consistency_action"):
            self.check_consistency_action.setEnabled(has_file)
        if hasattr(self, "register_check_action"):
            self.register_check_action.setEnabled(has_file)
        if hasattr(self, "gender_check_action"):
            self.gender_check_action.setEnabled(has_file)
        if hasattr(self, "font_checker_action"):
            self.font_checker_action.setEnabled(has_file)
        if hasattr(self, "btn_encoding_change"):
            self.btn_encoding_change.setEnabled(
                has_file and not isinstance(self.current_file, EspFile)
            )
        if hasattr(self, "glossary_quality_action"):
            self.glossary_quality_action.setEnabled(
                has_file and self._glossary_manager is not None
            )
        if hasattr(self, "stop_translation_action"):
            self.stop_translation_action.setEnabled(False)
        if hasattr(self, "approve_action"):
            self.approve_action.setEnabled(has_file and has_selection)
        if hasattr(self, "reject_action"):
            self.reject_action.setEnabled(has_file and has_selection)
        if hasattr(self, "next_untranslated_action"):
            self.next_untranslated_action.setEnabled(has_file)
        if hasattr(self, "prev_untranslated_action"):
            self.prev_untranslated_action.setEnabled(has_file)
        if hasattr(self, "claude_review_action"):
            self.claude_review_action.setEnabled(has_file and has_selection)
        if hasattr(self, "claude_suggest_action"):
            self.claude_suggest_action.setEnabled(has_file and has_selection)
        if hasattr(self, "macro_action"):
            self.macro_action.setEnabled(has_file)

        # Reload profile tints when the open file changes
        if has_file and self.current_path != self._last_profile_loaded_path:
            self._last_profile_loaded_path = self.current_path
            self._reload_profile_tints()
        elif not has_file and self._last_profile_loaded_path is not None:
            self._last_profile_loaded_path = None
            self.table_model.clear_profile_data()

        if hasattr(self, "_current_session"):
            self._update_session_title()

    def _reload_profile_tints(self) -> None:
        """Load per-file profile assignments and apply background tints to the table."""
        if self.current_path:
            self._profile_assignments.load(self.current_path)
        id_to_row = {row["id"]: i for i, row in enumerate(self.table_model._data)}
        profile_map = {}
        for sid, pid in self._profile_assignments.all().items():
            row_idx = id_to_row.get(sid)
            if row_idx is not None:
                p = self._profile_manager.get(pid)
                if p:
                    profile_map[row_idx] = p
        self.table_model.set_profile_data(profile_map)

    def _open_character_profiles(self) -> None:
        """Open the Character Profile editor dialog."""
        from gui.profile_editor_dialog import ProfileEditorDialog
        dlg = ProfileEditorDialog(manager=self._profile_manager, parent=self)
        dlg.exec()

    def _open_profile_assign(self, row_indices: list) -> None:
        """Open the profile picker for the given source-model row indices."""
        if not row_indices:
            return
        from gui.profile_assign_dialog import ProfileAssignDialog
        # Determine which profile (if any) all selected rows share
        rows = self.table_model._data
        pids = {
            self._profile_assignments.get(rows[i]["id"])
            for i in row_indices
            if i < len(rows)
        }
        current_pid = next(iter(pids)) if len(pids) == 1 else None

        dlg = ProfileAssignDialog(
            manager=self._profile_manager,
            row_count=len(row_indices),
            current_profile_id=current_pid,
            parent=self,
        )
        if dlg.exec() and dlg.was_accepted:
            string_ids = [rows[i]["id"] for i in row_indices if i < len(rows)]
            self._profile_assignments.set_many(string_ids, dlg.accepted_profile_id)
            self._reload_profile_tints()
            _p = self._profile_manager.get(dlg.accepted_profile_id) if dlg.accepted_profile_id else None
            profile_name = _p.name if _p is not None else self.tr("(none)")
            self.statusBar().showMessage(
                self.tr("Profile '{name}' assigned to {n} string(s)").format(
                    name=profile_name, n=len(string_ids)
                ),
                4000,
            )

    @Slot()
    def open_advanced_search(self):
        """Open the advanced search dialog."""
        from gui.advanced_search_dialog import AdvancedSearchDialog

        dialog = AdvancedSearchDialog(self)
        dialog.search_results.connect(self._on_search_results)
        dialog.exec()

    def _on_search_results(self, row_indices: list):
        """Handle search results from the dialog."""
        if not row_indices:
            self.statusBar().showMessage("No results found")
            return

        # Select all results in the table
        selection_model = self.table_view.selectionModel()
        selection_model.clearSelection()

        for row_idx in row_indices:
            index = self.table_model.index(row_idx, 0)
            selection_model.select(
                index, QItemSelectionModel.Select | QItemSelectionModel.Rows
            )

        # Scroll to first result
        if row_indices:
            self.table_view.scrollTo(self.table_model.index(row_indices[0], 0))

        self.statusBar().showMessage(f"Found {len(row_indices)} result(s)")

    def _extract_potential_terms(self, text: str) -> list:
        """Extract potential company/faction names from text."""
        potential_terms = []

        # Pattern 1: "[Word] Employee/Worker/Staff"
        pattern1 = re.compile(r"\b([A-Z][a-z]+) (?:Employee|Worker|Staff|Dialogue)")
        matches = pattern1.findall(text)
        potential_terms.extend(matches)

        # Pattern 2: "[Word] Corporation/Industries/Technologies"
        pattern2 = re.compile(
            r"\b([A-Z][a-z]+) (?:Corporation|Industries|Technologies|Systems)"
        )
        matches = pattern2.findall(text)
        potential_terms.extend(matches)

        # Filter out common words
        common_words = {
            "The",
            "And",
            "For",
            "Are",
            "But",
            "Not",
            "You",
            "All",
            "Can",
            "Her",
            "Was",
            "One",
            "Our",
            "Out",
            "Day",
            "Get",
            "Has",
            "Him",
            "His",
            "How",
            "Man",
            "New",
            "Now",
            "Old",
            "See",
            "Two",
            "Way",
            "Who",
            "Boy",
            "Did",
            "Its",
            "Let",
            "Put",
            "Say",
            "She",
            "Too",
            "Use",
            "May",
            "Yes",
        }
        potential_terms = [t for t in potential_terms if t not in common_words]

        return list(set(potential_terms))

    def _show_term_protection_dialog(self, terms: list) -> list:
        """Show dialog to confirm adding detected terms."""
        if not terms:
            return []

        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QComboBox,
            QDialog,
            QHBoxLayout,
            QLabel,
            QListWidget,
            QListWidgetItem,
            QPushButton,
            QVBoxLayout,
        )

        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Add Protected Terms"))
        dialog.setMinimumWidth(500)

        layout = QVBoxLayout(dialog)

        info = QLabel(
            self.tr(
                "Detected potential company/faction names. Select and add to protection list:"
            )
        )
        layout.addWidget(info)

        list_widget = QListWidget()
        list_widget.setSelectionMode(QListWidget.MultiSelection)

        for term in terms:
            item = QListWidgetItem(term)
            item.setCheckState(Qt.Checked)
            list_widget.addItem(item)

        layout.addWidget(list_widget)

        category_layout = QHBoxLayout()
        category_layout.addWidget(QLabel(self.tr("Category:")))
        combo_category = QComboBox()
        combo_category.addItems(
            ["company", "faction", "location", "character", "item", "custom"]
        )
        combo_category.setCurrentText("company")
        category_layout.addWidget(combo_category)
        category_layout.addStretch()
        layout.addLayout(category_layout)

        btn_layout = QHBoxLayout()
        btn_add = QPushButton(self.tr("Add Selected"))
        btn_add.setProperty("primary", True)
        btn_add.setStyleSheet("padding: 8px 16px;")
        btn_add.clicked.connect(dialog.accept)
        btn_layout.addWidget(btn_add)

        btn_skip = QPushButton(self.tr("Skip"))
        btn_skip.clicked.connect(dialog.reject)
        btn_layout.addWidget(btn_skip)

        layout.addLayout(btn_layout)

        if dialog.exec() == QDialog.Accepted:
            category = combo_category.currentText()
            selected_terms = []
            for i in range(list_widget.count()):
                item = list_widget.item(i)
                if item.checkState() == Qt.Checked:
                    selected_terms.append(
                        ProtectedTerm(
                            term=item.text(), category=category, case_sensitive=True
                        )
                    )
            return selected_terms

        return []

    @Slot()
    def _rebuild_recent_menu(self) -> None:
        """Repopulate the Open Recent submenu from settings.recent_files."""
        self._recent_menu.clear()
        paths = [p for p in self.settings.recent_files if Path(p).exists()]
        if paths != self.settings.recent_files:
            self.settings.recent_files = paths

        if not paths:
            placeholder = QAction(self.tr("(empty)"), self)
            placeholder.setEnabled(False)
            self._recent_menu.addAction(placeholder)
        else:
            for i, path in enumerate(paths):
                p = Path(path)
                label = f"&{i + 1}. {p.name}" if i < 9 else f"{i + 1}. {p.name}"
                action = QAction(label, self)
                action.setToolTip(path)
                action.setStatusTip(path)
                action.triggered.connect(
                    lambda checked=False, fp=path: self._open_file_path(fp)
                )
                self._recent_menu.addAction(action)

        self._recent_menu.addSeparator()
        clear_action = QAction(self.tr("Clear Recent Files"), self)
        clear_action.setEnabled(bool(paths))
        clear_action.triggered.connect(self._clear_recent_files)
        self._recent_menu.addAction(clear_action)

    def _add_to_recent(self, file_path: str) -> None:
        """Prepend path to recent files list, deduplicate, cap at 10, persist."""
        recent = [p for p in self.settings.recent_files if p != file_path]
        recent.insert(0, file_path)
        self.settings.recent_files = recent[:10]
        save_settings(self.settings)
        self._rebuild_recent_menu()

    def _clear_recent_files(self) -> None:
        self.settings.recent_files = []
        save_settings(self.settings)
        self._rebuild_recent_menu()

    def open_file(self):
        """Open Bethesda string file or ESP/ESM plugin."""
        file_path, _ = get_open_filename(
            self,
            self.tr("Open File"),
            "",
            self.tr(
                "All Supported Files (*.strings *.dlstrings *.ilstrings *.esp *.esm *.esl *.ba2 *.STRINGS *.DLSTRINGS *.ILSTRINGS *.ESP *.ESM *.ESL *.BA2);;"
                "String Files (*.strings *.dlstrings *.ilstrings);;"
                "Plugin Files (*.esp *.esm *.esl);;"
                "BA2 Archives (*.ba2 *.BA2);;"
                "All Files (*)"
            ),
        )
        if not file_path:
            return

        self._open_file_path(file_path)

    def _open_file_path(self, file_path: str) -> None:
        """Open any supported file by path (used by drag & drop and welcome screen)."""
        ext = Path(file_path).suffix.lower()
        if ext in (".esp", ".esm", ".esl"):
            self._open_esp_file(file_path)
        elif ext == ".ba2":
            self._open_ba2_file(file_path)
        else:
            self._open_strings_file(file_path)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_drop_overlay"):
            self._drop_overlay.setGeometry(self.rect())

    def dragEnterEvent(self, event) -> None:
        try:
            if not event.mimeData().hasUrls():
                event.ignore()
                return
            valid = _valid_drop_paths(event.mimeData())
            if valid:
                event.acceptProposedAction()
                self._drop_overlay.show_valid(valid)
            else:
                event.ignore()
        except Exception as exc:
            logger.error("dragEnterEvent: %s", exc, exc_info=True)

    def dragMoveEvent(self, event) -> None:
        # Must keep accepting so the drop cursor stays active mid-move.
        if _valid_drop_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._drop_overlay.hide()

    def dropEvent(self, event) -> None:
        self._drop_overlay.hide()
        try:
            paths = _valid_drop_paths(event.mimeData())
            if not paths:
                return
            event.acceptProposedAction()
            if len(paths) == 1:
                self._open_file_path(paths[0])
            else:
                self._open_file_path(paths[0])
                self.statusBar().showMessage(
                    self.tr(
                        "{n} files dropped — opened {name}. "
                        "Open additional files one at a time."
                    ).format(n=len(paths), name=Path(paths[0]).name),
                    6000,
                )
        except Exception as exc:
            logger.error("dropEvent: %s", exc, exc_info=True)

    def _open_strings_file(self, file_path: str):
        """Load a .strings / .dlstrings / .ilstrings file."""
        self._close_current_ba2()
        try:
            self.statusBar().showMessage(
                self.tr("Loading {filename}...").format(filename=Path(file_path).name)
            )
            self.current_file = BethesdaStringFile(file_path)
            self.current_path = Path(file_path)

            target_lang = self.combo_target_lang.currentData()

            self.lbl_file_info.setText(f"📄 {self.current_path.name}")
            self.lbl_string_count.setText(
                self.tr("Strings: {count}").format(count=len(self.current_file))
            )

            # Use auto-detected encoding (file.encoding is set by BethesdaStringFile)
            self.table_model.load_from_bethesda_file(
                self.current_file, locale=target_lang
            )
            self._update_encoding_label()

            self.file_loaded.emit(file_path)
            self._add_to_recent(file_path)
            self.statusBar().showMessage(
                self.tr("Loaded {count} strings from {name} ({enc})").format(
                    count=len(self.current_file),
                    name=self.current_path.name,
                    enc=self.current_file.encoding,
                )
            )
            # Update detached table window title to reflect the new file
            if self._detached_table is not None:
                self._detached_table.setWindowTitle(
                    self.tr("String List") + f" — {self.current_path.name}"
                )
            if self._glossary_manager:
                self._glossary_manager.load_project_glossary(self.current_path)
            self._start_pre_estimation()
            self._offer_triplet_load(self.current_path)
            self._audit_log.file_opened(
                file_path, self.current_path.suffix.lower(), len(self.current_file)
            )

        except Exception as e:
            logger.error(f"Failed to load: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to load:\n{error}").format(error=e),
            )
            self.current_file = None
        finally:
            self._update_ui_state()

    def _offer_triplet_load(self, loaded_path: Path) -> None:
        """If sibling .strings/.dlstrings/.ilstrings files exist, offer to load them."""
        stem = loaded_path.stem
        folder = loaded_path.parent
        loaded_ext = loaded_path.suffix.lower()
        triplet_exts = [".strings", ".dlstrings", ".ilstrings"]
        siblings = [
            folder / (stem + ext)
            for ext in triplet_exts
            if ext != loaded_ext and (folder / (stem + ext)).is_file()
        ]
        if not siblings:
            return
        names = ", ".join(s.name for s in siblings)
        reply = QMessageBox.question(
            self,
            self.tr("Load Companion Files"),
            self.tr(
                "Found companion string file(s):\n{names}\n\n"
                "Load them together with {loaded} for a complete dictionary?"
            ).format(names=names, loaded=loaded_path.name),
            QMessageBox.Yes | QMessageBox.No,  # type: ignore[attr-defined]
            QMessageBox.Yes,  # type: ignore[attr-defined]
        )
        if reply == QMessageBox.Yes:  # type: ignore[attr-defined]
            assert isinstance(self.current_file, BethesdaStringFile)
            existing_ids = {s.id for s in self.current_file.strings}
            total_added = 0
            for sib in siblings:
                try:
                    extra = BethesdaStringFile(str(sib))
                    added = 0
                    for string_obj in extra.strings:
                        if string_obj.id not in existing_ids:
                            self.current_file.strings.append(string_obj)
                            existing_ids.add(string_obj.id)
                            added += 1
                    self.current_file._invalidate_index()  # pyright: ignore[reportPrivateUsage]
                    total_added += added
                    logger.info(
                        "Merged %d strings from %s into %s",
                        added, sib.name, loaded_path.name,
                    )
                except Exception as e:
                    logger.warning("Failed to merge %s: %s", sib.name, e)
            # Reload table with merged data
            if total_added:
                self.table_model.load_from_bethesda_file(
                    self.current_file,
                    locale=self.combo_target_lang.currentData(),
                )
                self.lbl_string_count.setText(
                    self.tr("Strings: {count}").format(count=len(self.current_file))
                )

    def _open_esp_file(self, file_path: str):
        """Load an ESP/ESM/ESL plugin file."""
        self._close_current_ba2()
        try:
            p = Path(file_path)
            self.statusBar().showMessage(
                self.tr("Loading {filename}...").format(filename=p.name)
            )
            esp = EspFile()
            target_lang = self.combo_target_lang.currentData()
            encoding, _ = EncodingConverter.get_encodings_for_locale(target_lang)
            esp.load(p, encoding)

            if esp.is_localized:
                QMessageBox.information(
                    self,
                    self.tr("Localized Plugin"),
                    self.tr(
                        "{name} is a localized plugin.\n"
                        "Its text is stored in companion .strings/.dlstrings/.ilstrings files.\n"
                        "Open those files instead to translate them."
                    ).format(name=p.name),
                )
                return

            self.current_file = esp
            self.current_path = p

            self.lbl_file_info.setText(f"📄 {p.name}")
            self.lbl_encoding.setText(
                self.tr("Encoding: {encoding}").format(encoding=encoding)
            )
            self.lbl_string_count.setText(
                self.tr("Strings: {count}").format(count=len(esp.strings))
            )

            self.table_model.load_from_esp_file(esp, encoding, target_lang)

            self.file_loaded.emit(file_path)
            self._add_to_recent(file_path)
            self.statusBar().showMessage(
                self.tr("Loaded {count} strings from {name}").format(
                    count=len(esp.strings), name=p.name
                )
            )
            if self._glossary_manager:
                self._glossary_manager.load_project_glossary(self.current_path)
            self._start_pre_estimation()
            self._audit_log.file_opened(file_path, p.suffix.lower(), len(esp.strings))

        except Exception as e:
            logger.error(f"Failed to load ESP: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to load plugin:\n{error}").format(error=e),
            )
            self.current_file = None
        finally:
            self._update_ui_state()

    def _close_current_ba2(self) -> None:
        """Close the currently open BA2 archive and clear BA2 state."""
        if self._current_ba2 is not None:
            try:
                self._current_ba2.close()
            except Exception:
                pass
            self._current_ba2 = None
            self._current_ba2_entry = None

    def _open_ba2_file(self, file_path: str) -> None:
        """Open a .ba2 archive and load one of its strings files for editing."""
        from gui.ba2_picker_dialog import BA2PickerDialog
        p = Path(file_path)
        try:
            self.statusBar().showMessage(
                self.tr("Opening archive {filename}...").format(filename=p.name)
            )
            ba2 = BA2File(file_path)
            strings_files = ba2.list_strings_files()
        except Exception as e:
            logger.error("Failed to open BA2 %s: %s", file_path, e, exc_info=True)
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to open archive:\n{error}").format(error=e),
            )
            return

        if not strings_files:
            ba2.close()
            QMessageBox.information(
                self,
                self.tr("No Strings Found"),
                self.tr(
                    "{name} does not contain any .strings / .dlstrings / .ilstrings files."
                ).format(name=p.name),
            )
            return

        if len(strings_files) == 1:
            entry_name = strings_files[0]
        else:
            dlg = BA2PickerDialog(p.name, strings_files, parent=self)
            if dlg.exec() != BA2PickerDialog.DialogCode.Accepted:
                ba2.close()
                return
            entry_name = dlg.selected_entry()
            if entry_name is None:
                ba2.close()
                return

        try:
            raw = ba2.extract(entry_name)
            ext = Path(entry_name.replace("\\", "/")).suffix.lstrip(".")
            string_file = BethesdaStringFile(file_extension=ext, buffer=raw)
        except Exception as e:
            logger.error("Failed to extract %s from %s: %s", entry_name, p.name, e, exc_info=True)
            ba2.close()
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to extract strings file from archive:\n{error}").format(error=e),
            )
            return

        self._close_current_ba2()
        self._current_ba2 = ba2
        self._current_ba2_entry = entry_name

        self.current_file = string_file
        self.current_path = p  # track by .ba2 path

        entry_display = Path(entry_name.replace("\\", "/")).name
        self.lbl_file_info.setText(f"📦 {p.name}  /  {entry_display}")
        self._update_encoding_label()
        self.lbl_string_count.setText(
            self.tr("Strings: {count}").format(count=len(string_file))
        )

        target_lang = self.combo_target_lang.currentData()
        self.table_model.load_from_bethesda_file(string_file, locale=target_lang)

        self.file_loaded.emit(file_path)
        self._add_to_recent(file_path)
        self.statusBar().showMessage(
            self.tr("Loaded {count} strings from {entry} (in {archive})").format(
                count=len(string_file),
                entry=entry_display,
                archive=p.name,
            )
        )
        if self._glossary_manager:
            self._glossary_manager.load_project_glossary(p)
        self._start_pre_estimation()
        self._audit_log.file_opened(file_path, ".ba2", len(string_file))
        self._update_ui_state()

    @Slot()
    def save_file(self):
        """Save current file."""
        if not self.current_path:
            return self.save_file_as()

        try:
            if isinstance(self.current_file, EspFile):
                target_lang = self.combo_target_lang.currentData()
                encoding, _ = EncodingConverter.get_encodings_for_locale(target_lang)
                self.table_model.apply_changes_to_esp_file(self.current_file, encoding)
                self.current_file.save(self.current_path, encoding)
                _count = len(self.current_file.strings)
            elif self._current_ba2 is not None:
                assert isinstance(self.current_file, BethesdaStringFile)
                assert self._current_ba2_entry is not None
                self.table_model.apply_changes_to_file(self.current_file)
                raw = self.current_file.get_bytes()
                self._current_ba2.save_with_replacement(
                    self.current_path,
                    {self._current_ba2_entry: raw},
                )
                _count = len(self.current_file)
            else:
                assert isinstance(self.current_file, BethesdaStringFile)
                self.table_model.apply_changes_to_file(self.current_file)
                self.current_file.save(str(self.current_path))
                _count = len(self.current_file)
            self.statusBar().showMessage(self.tr("Saved successfully ✓"))
            from gui.micro_animations import show_toast
            show_toast(self, self.tr("Saved ✓  {name}").format(
                name=self.current_path.name), kind="success", timeout_ms=2500)
            self._audit_log.file_saved(
                str(self.current_path), self.current_path.suffix.lower(), _count
            )
        except Exception as e:
            logger.error(f"Save failed: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to save:\n{error}").format(error=e),
            )

    @Slot()
    def save_file_as(self):
        """Save file to new location."""
        if not self.current_file:
            return

        is_esp = isinstance(self.current_file, EspFile)
        is_ba2 = self._current_ba2 is not None

        if is_esp:
            default_name = (
                f"{self.current_path.stem}_translated{self.current_path.suffix}"
                if self.current_path else "output.esp"
            )
            file_filter = self.tr("Plugin Files (*.esp *.esm *.esl);;All Files (*)")
        elif is_ba2:
            default_name = (
                f"{self.current_path.stem}_translated{self.current_path.suffix}"
                if self.current_path else "output.ba2"
            )
            file_filter = self.tr("BA2 Archives (*.ba2 *.BA2);;All Files (*)")
        else:
            default_name = (
                f"{self.current_path.stem}_translated{self.current_path.suffix}"
                if self.current_path else "output.strings"
            )
            file_filter = self.tr(
                "Bethesda String Files (*.strings *.dlstrings *.ilstrings *.STRINGS *.DLSTRINGS *.ILSTRINGS);;All Files (*)"
            )

        file_path, _ = get_save_filename(
            self,
            self.tr("Save As"),
            str(Path.home() / default_name),
            file_filter,
        )
        if not file_path:
            return

        try:
            if is_esp:
                assert isinstance(self.current_file, EspFile)
                target_lang = self.combo_target_lang.currentData()
                encoding, _ = EncodingConverter.get_encodings_for_locale(target_lang)
                self.table_model.apply_changes_to_esp_file(self.current_file, encoding)
                self.current_file.save(Path(file_path), encoding)
                _count2 = len(self.current_file.strings)
            elif is_ba2:
                assert isinstance(self.current_file, BethesdaStringFile)
                assert self._current_ba2 is not None
                assert self._current_ba2_entry is not None
                self.table_model.apply_changes_to_file(self.current_file)
                raw = self.current_file.get_bytes()
                self._current_ba2.save_with_replacement(
                    file_path,
                    {self._current_ba2_entry: raw},
                )
                _count2 = len(self.current_file)
            else:
                assert isinstance(self.current_file, BethesdaStringFile)
                self.table_model.apply_changes_to_file(self.current_file)
                self.current_file.save(file_path)
                _count2 = len(self.current_file)
            self.statusBar().showMessage(
                self.tr("Saved to {filename}").format(filename=Path(file_path).name)
            )
            self._audit_log.file_saved(
                file_path, Path(file_path).suffix.lower(), _count2
            )
        except Exception as e:
            logger.error(f"Save failed: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to save:\n{error}").format(error=e),
            )

    @Slot()
    def translate_selected(self):
        """Translate selected strings with auto-term detection."""
        if not self.current_file:
            return

        indices = [idx.row() for idx in self.table_view.selectionModel().selectedRows()]
        if not indices:
            QMessageBox.information(
                self, self.tr("No Selection"), self.tr("Select strings first.")
            )
            return

        # Auto-detect potential terms in selected strings
        potential_terms = []
        for idx in indices:
            row = self.table_model.get_row_data(idx)
            original = row.get("original", "")
            detected = self._extract_potential_terms(original)
            potential_terms.extend(detected)

        # Filter out already protected terms
        potential_terms = [
            term
            for term in potential_terms
            if term not in self.term_protector.protected_terms
        ]

        # If we found potential terms, ask user to confirm
        if potential_terms and self.settings.enable_term_protection:
            new_terms = self._show_term_protection_dialog(potential_terms)
            for term in new_terms:
                self.term_protector.add_protected_term(term)
                # Also update worker
                if (
                    self.ollama_worker is not None
                    and self.ollama_worker.term_protector
                ):
                    self.ollama_worker.term_protector.add_protected_term(term)

            logger.info(f"Added {len(new_terms)} new protected terms")
            if new_terms:
                self.statusBar().showMessage(
                    self.tr("Added {count} protected terms").format(
                        count=len(new_terms)
                    )
                )

        self._start_translation(indices)

    @Slot()
    def translate_all(self):
        """Translate all strings."""
        if not self.current_file:
            return

        indices = list(range(self.table_model.rowCount()))
        self._start_translation(indices)

    def _start_translation(self, indices):
        """Start translation batch."""
        self._translation_stopping = False
        source_lang = self.combo_source_lang.currentData()
        target_lang = self.combo_target_lang.currentData()
        quality = self.spin_quality.value()

        if source_lang == target_lang:
            QMessageBox.warning(
                self,
                self.tr("Same Language"),
                self.tr("Source and target languages are identical."),
            )
            return

        # Create translation requests for Ollama worker
        requests = []
        skipped_empty = 0
        for idx in indices:
            row = self.table_model.get_row_data(idx)
            if row.get("translated"):
                continue
            # Skip rows with no original text (nothing to translate)
            if not row["original"] or not row["original"].strip():
                skipped_empty += 1
                continue

            # Disable English protection if source is English
            protect_english = self.settings.protect_english_text
            if source_lang == "en":
                protect_english = False

            requests.append(
                TranslationRequest(
                    index=idx,
                    original_text=row["original"],
                    string_id=row["id"],
                    source_lang=source_lang,
                    target_lang=target_lang,
                    context=row.get("context", ""),
                    quality_level=quality,
                    locale_hint=self._get_locale_code(target_lang),
                    protected_terms_enabled=self.settings.enable_term_protection,
                    protect_english_text=protect_english,
                    context_note=row.get("context_note", ""),
                    # glossary_snippet computed on the worker thread to keep UI responsive
                )
            )

        if skipped_empty:
            logger.info(f"Skipped {skipped_empty} empty strings")

        if not requests:
            QMessageBox.information(
                self,
                self.tr("Nothing to Translate"),
                self.tr("All selected strings are already translated."),
            )
            return

        # Pre-flight cost estimate for Claude backend
        if is_claude_model(self.settings.ollama_model):
            if not self._claude_preflight_check(requests):
                return

        # Show progress UI BEFORE emitting signal
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, len(requests))
        self.progress_bar.setValue(0)
        self.lbl_progress.setText(
            self.tr("Translating {current}/{total}...").format(
                current=0, total=len(requests)
            )
        )
        self._set_ui_enabled(False)
        self._pending_translation_updates.clear()
        self._update_flush_timer.start()

        self._eta_start_time = time.monotonic()
        self._eta_batch_total = len(requests)
        self._audit_log.translation_start(
            model=self.settings.ollama_model,
            count=len(requests),
            source_lang=source_lang,
            target_lang=target_lang,
        )
        # CRITICAL FIX: Emit signal instead of direct method call
        self.translation_requested.emit(requests)

    def _claude_preflight_check(self, requests: list) -> bool:
        """
        Show a token-cost estimate dialog before a Claude batch translation.
        Returns True if the user wants to proceed, False to cancel.
        """
        from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QDialogButtonBox, QLabel, QFrame
        model = self.settings.ollama_model
        est = estimate_batch_cost(model, requests)

        def _fmt_tokens(n: float) -> str:
            if n >= 1_000_000:
                return f"~{n / 1_000_000:.2f}M"
            if n >= 1_000:
                return f"~{n / 1_000:.1f}K"
            return str(n)

        def _fmt_cost(usd: float) -> str:
            if usd < 0.01:
                return "< $0.01"
            return f"${usd:.3f}"

        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr("Pre-flight Cost Estimate"))
        dlg.setMinimumWidth(380)
        root = QVBoxLayout(dlg)

        title = QLabel(self.tr("<b>Claude API — estimated cost for this batch</b>"))
        title.setWordWrap(True)
        root.addWidget(title)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.addRow(self.tr("Model:"), QLabel(model))
        form.addRow(self.tr("Strings to translate:"), QLabel(str(len(requests))))
        root.addLayout(form)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep2)

        form2 = QFormLayout()
        form2.setLabelAlignment(Qt.AlignRight)
        form2.addRow(self.tr("Est. input tokens:"),  QLabel(_fmt_tokens(est["input_tokens"])))
        form2.addRow(self.tr("Est. output tokens:"), QLabel(_fmt_tokens(est["output_tokens"])))
        root.addLayout(form2)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.HLine)
        sep3.setFrameShadow(QFrame.Sunken)
        root.addWidget(sep3)

        form3 = QFormLayout()
        form3.setLabelAlignment(Qt.AlignRight)
        cost_lbl = QLabel(
            f"<b>{_fmt_cost(est['cost_with_cache'])}</b>"
            f"  <span style='color:#6b7280;font-size:.9em'>"
            f"(without cache: {_fmt_cost(est['cost_without_cache'])})</span>"
        )
        cost_lbl.setTextFormat(Qt.RichText)
        form3.addRow(self.tr("Est. cost (USD):"), cost_lbl)
        form3.addRow(
            self.tr("Cache savings:"),
            QLabel(self.tr("~{pct:.0f}% via prompt caching").format(pct=est["cache_savings_pct"])),
        )
        root.addLayout(form3)

        note = QLabel(self.tr(
            "<i>Estimates use ~3.5 chars/token. Actual cost depends on "
            "prompt caching state and output length.</i>"
        ))
        note.setWordWrap(True)
        note.setTextFormat(Qt.RichText)
        note.setStyleSheet("color: #6b7280; font-size: 0.85em;")
        root.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        btn_go = buttons.addButton(self.tr("Translate"), QDialogButtonBox.AcceptRole)
        btn_go.setDefault(True)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        root.addWidget(buttons)

        return dlg.exec() == QDialog.Accepted

    @Slot()
    def translate_starfield_txt(self):
        """Translate Starfield interface TXT file (e.g. translate_en.txt)."""
        file_path, _ = get_open_filename(
            self,
            self.tr("Open Starfield Interface TXT"),
            "",
            self.tr("Text Files (*.txt *.TXT);;All Files (*)"),
        )
        if not file_path:
            return

        # Auto-detect source language from filename
        filename = Path(file_path).name.lower()
        if "translate_ru" in filename:
            idx = self.combo_source_lang.findData("ru")
            if idx >= 0:
                self.combo_source_lang.setCurrentIndex(idx)
        elif "translate_en" in filename:
            idx = self.combo_source_lang.findData("en")
            if idx >= 0:
                self.combo_source_lang.setCurrentIndex(idx)

        # Determine default output path
        path = Path(file_path)
        default_output = path.parent / f"{path.stem}_uk.txt"

        target_path, _ = get_save_filename(
            self,
            self.tr("Save Translated TXT As"),
            str(default_output),
            self.tr("Text Files (*.txt *.TXT);;All Files (*)"),
        )
        if not target_path:
            return

        self._translation_stopping = False
        try:
            # Read input file (UTF-16 with BOM is typical for these files)
            # Try utf-16 first, fallback to utf-8
            try:
                with open(file_path, "r", encoding="utf-16") as f:
                    lines = f.readlines()
            except UnicodeError:
                with open(file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

            requests = []
            self._txt_translation_data = []  # Store [is_translatable, key_or_line, clean_text, translated_text]
            self._translatable_items = []

            source_lang = self.combo_source_lang.currentData()
            target_lang = self.combo_target_lang.currentData()
            quality = self.spin_quality.value()

            for line in lines:
                # Bethesda TXT format: $ID\tText
                if line.startswith("$") and "\t" in line:
                    parts = line.split("\t", 1)
                    key = parts[0]
                    text = parts[1] if len(parts) > 1 else ""

                    # text still has line endings
                    clean_text = text.strip("\r\n")

                    if clean_text:
                        # Disable English protection if source is English
                        protect_english = self.settings.protect_english_text
                        if source_lang == "en":
                            protect_english = False

                        req_index = len(requests)
                        requests.append(
                            TranslationRequest(
                                index=req_index,
                                original_text=clean_text,
                                string_id=req_index,
                                source_lang=source_lang,
                                target_lang=target_lang,
                                quality_level=quality,
                                locale_hint=self._get_locale_code(target_lang),
                                protected_terms_enabled=self.settings.enable_term_protection,
                                protect_english_text=protect_english,
                            )
                        )
                        item = [True, key, clean_text, ""]
                        self._txt_translation_data.append(item)
                        self._translatable_items.append(item)
                    else:
                        self._txt_translation_data.append([False, line, "", ""])
                else:
                    self._txt_translation_data.append([False, line, "", ""])

            if not requests:
                QMessageBox.information(
                    self,
                    self.tr("Nothing to Translate"),
                    self.tr("No translatable lines found in the TXT file."),
                )
                return

            self._is_translating_txt = True
            self._txt_target_path = target_path

            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, len(requests))
            self.progress_bar.setValue(0)
            self.lbl_progress.setText(
                self.tr("Translating TXT {current}/{total}...").format(
                    current=0, total=len(requests)
                )
            )
            self._set_ui_enabled(False)
            self._eta_start_time = time.monotonic()
            self._eta_batch_total = len(requests)
            self.translation_requested.emit(requests)

        except Exception as e:
            logger.error(f"Failed to read TXT: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to read TXT:\n{error}").format(error=e),
            )

    def _finish_txt_translation(self, successful, failed):
        """Reconstruct and save the translated TXT file."""
        self.progress_bar.setVisible(False)
        self._set_ui_enabled(True)
        self._is_translating_txt = False

        try:
            assert self._txt_target_path is not None
            with open(self._txt_target_path, "w", encoding="utf-16-le") as f:
                # Write BOM manually for utf-16-le
                f.write("\ufeff")
                for item in self._txt_translation_data:
                    if item[0]:  # Translatable
                        key = item[1]
                        translated = (
                            item[3] if item[3] else item[2]
                        )  # Fallback to original
                        f.write(f"{key}\t{translated}\r\n")
                    else:  # Not translatable
                        line = item[1]
                        # Ensure line ends with \r\n if it didn't
                        if not line.endswith("\n"):
                            line += "\r\n"
                        elif line.endswith("\n") and not line.endswith("\r\n"):
                            line = line[:-1] + "\r\n"
                        f.write(line)

            msg = self.tr("TXT Translation Complete: {count} successful").format(
                count=successful
            )
            if failed > 0:
                msg += self.tr(", {count} failed").format(count=failed)
            QMessageBox.information(self, self.tr("Success"), msg)
            self.statusBar().showMessage(msg, 10000)
            send_notification(
                self.tr("Translation complete"),
                msg,
                tray_icon=self._tray_icon,
            )

        except Exception as e:
            logger.error(f"Failed to save translated TXT: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to save translated TXT:\n{error}").format(error=e),
            )

    def _get_locale_code(self, lang_code: str) -> str:
        """Return the locale code for a language.

        The combo boxes now store the locale code directly as item data,
        so this is a pass-through kept for call-site compatibility.
        """
        return lang_code or "en"

    @Slot(int, int)
    def _on_ollama_progress(self, completed: int, total: int):
        """Update progress bar and ETA label."""
        self.progress_bar.set_value_animated(completed)
        self.lbl_progress.setText(
            self.tr("Translating {current}/{total}...").format(
                current=completed, total=total
            )
        )

        # Compute and display ETA
        if self._eta_start_time and completed > 0:
            elapsed = time.monotonic() - self._eta_start_time
            remaining = self._eta_batch_total - completed
            rate = completed / elapsed          # strings per second
            if rate > 0 and remaining > 0:
                eta_str = _format_eta(remaining / rate)
                self._eta_lbl.setText(self.tr("ETA: {t}").format(t=eta_str))
                self._eta_lbl.setVisible(True)
            elif remaining == 0:
                self._eta_lbl.setVisible(False)

        self.statusBar().showMessage(
            self.tr("Translating: {current}/{total}").format(
                current=completed, total=total
            )
        )

    @Slot(int, str, int)
    def _on_translation_ready(self, index: int, translated: str, string_id: int):
        """Buffer translated string; flushed to the model at 60fps by the timer."""
        if self._translation_stopping:
            return
        self._pending_translation_updates.append((index, translated, self._is_translating_txt))

    def _flush_translation_updates(self):
        """Apply all buffered translation results to the model in one batch."""
        if not self._pending_translation_updates:
            return
        updates = self._pending_translation_updates
        self._pending_translation_updates = []

        txt_updates = [(i, t) for i, t, is_txt in updates if is_txt]
        model_updates = [(i, t) for i, t, is_txt in updates if not is_txt]

        for index, translated in txt_updates:
            if 0 <= index < len(self._translatable_items):
                self._translatable_items[index][3] = translated

        if model_updates:
            self.table_model.set_translated_text_batch(model_updates)

    @Slot(str)
    def _on_ollama_error(self, error_msg: str):
        """Handle worker error."""
        self.statusBar().showMessage(
            self.tr("Error: {error}").format(error=error_msg), 5000
        )
        logger.error(f"Ollama error: {error_msg}")

    @Slot(int, int)
    def _on_ollama_finished(self, successful: int, failed: int):
        """Translation batch completed."""
        if self._is_translating_txt:
            self._finish_txt_translation(successful, failed)
            return

        self._update_flush_timer.stop()
        self._flush_translation_updates()  # drain any remaining buffered updates
        self._eta_start_time = 0.0
        self._eta_lbl.setVisible(False)
        self._refresh_stats()
        from gui.micro_animations import flash_progress_bar_success, show_toast
        if failed == 0 and successful > 0:
            flash_progress_bar_success(self.progress_bar)
            show_toast(
                self,
                self.tr("{n} strings translated").format(n=successful),
                kind="success",
            )
        elif failed > 0:
            self.progress_bar.setVisible(False)
            show_toast(
                self,
                self.tr("{ok} translated, {fail} failed").format(
                    ok=successful, fail=failed
                ),
                kind="warning",
            )
        else:
            self.progress_bar.setVisible(False)
        self._set_ui_enabled(True)

        if self.settings.enable_cache and successful > 0:
            self.translation_cache.save()

        self._audit_log.translation_complete(
            total=successful + failed, translated=successful, errors=failed
        )

        msg = self.tr("Complete: {count} successful").format(count=successful)
        if failed > 0:
            msg += self.tr(", {count} failed").format(count=failed)
        self.statusBar().showMessage(msg, 10000)

        if failed > 0:
            QMessageBox.warning(
                self,
                self.tr("Complete"),
                self.tr("{msg}\nCheck log for details.").format(msg=msg),
            )
            send_notification(
                self.tr("Translation complete"),
                msg,
                tray_icon=self._tray_icon,
            )
        else:
            QMessageBox.information(self, self.tr("Success"), msg)
            send_notification(
                self.tr("Translation complete"),
                msg,
                tray_icon=self._tray_icon,
            )

        self.translation_complete.emit(successful, failed)

        # Auto quality check — updates row colours in the table silently
        if successful > 0:
            self._run_quality_check_silent()

    # ── Live stats ────────────────────────────────────────────────────────────

    def _refresh_stats(self) -> None:
        """Recompute Total/Done/Left counts and update the permanent status widget."""
        data = self.table_model._data if hasattr(self, "table_model") else []
        total = len(data)
        if total == 0:
            self._stat_lbl.setText("")
            return
        translated = sum(1 for r in data if r.get("status") == "translated")
        pending = total - translated
        pct = translated / total
        self._stat_lbl.setText(
            self.tr("Total: {total}  ·  Done: {done} ({pct})  ·  Left: {left}").format(
                total=total,
                done=translated,
                pct=f"{pct:.0%}",
                left=pending,
            )
        )

    @Slot()
    def _stop_translation(self):
        """Stop the current translation batch."""
        if self.ollama_worker:
            self._translation_stopping = True
            self.ollama_worker.stop()
            self.statusBar().showMessage(self.tr("Stopping translation..."), 3000)
            logger.info("Translation stop requested by user")

    @Slot()
    def _on_selection_changed(self):
        """Handle selection change."""
        self._update_ui_state()
        # Debounced: fires _refresh_glossary_dock 200ms after the last selection
        # change so rapid arrow-key scrolling doesn't block the main thread.
        self._glossary_refresh_timer.start()
        # Update Claude panel context (only when visible — free if hidden)
        if hasattr(self, "_claude_panel") and self._claude_panel.isVisible():
            self._push_string_to_claude_panel()
        # Update audio preview panel (only when visible — skip if hidden)
        if hasattr(self, "_audio_panel") and self._audio_panel.isVisible():
            self._push_string_to_audio_panel()
        # Update translation editor pane (only when visible)
        if hasattr(self, "_editor_pane") and self._editor_pane.isVisible():
            self._push_string_to_editor_pane()
        # Update visual context preview (only when visible)
        if hasattr(self, "_visual_preview") and self._visual_preview.isVisible():
            self._push_string_to_visual_preview()

    def _push_string_to_audio_panel(self) -> None:
        """Forward the currently selected row data to the Audio Preview panel."""
        row = self._get_current_row()
        self._audio_panel.update_string(row)

    def _get_current_row(self):
        """Return the data dict for the currently selected row, or None."""
        indexes = self.table_view.selectionModel().selectedRows()
        if not indexes:
            return None
        row = indexes[0].row()
        if 0 <= row < len(self.table_model._data):
            return self.table_model._data[row]
        return None

    def _get_current_source_row(self) -> int:
        """Return the source model row for the current selection, or 0."""
        indexes = self.table_view.selectionModel().selectedRows()
        return indexes[0].row() if indexes else 0

    def _toggle_audio_panel(self) -> None:
        visible = self._audio_panel.isVisible()
        self._audio_panel.setVisible(not visible)
        self.audio_panel_action.setChecked(not visible)
        if not visible:
            self._push_string_to_audio_panel()

    def _push_string_to_visual_preview(self) -> None:
        self._visual_preview.update_string(self._get_current_row())

    def _toggle_visual_preview(self) -> None:
        visible = self._visual_preview.isVisible()
        self._visual_preview.setVisible(not visible)
        self.visual_preview_action.setChecked(not visible)
        if not visible:
            self._push_string_to_visual_preview()

    def _apply_audio_settings(self) -> None:
        """Push current audio settings to the preview panel."""
        cache_dir = get_config_dir() / "tts_cache"
        self._audio_panel.apply_settings(
            engine_type=getattr(self.settings, "tts_engine_type", "espeak"),
            voice=getattr(self.settings, "espeak_voice", "uk"),
            piper_binary=getattr(self.settings, "piper_binary", ""),
            piper_model=getattr(self.settings, "piper_model", ""),
            espeak_binary=getattr(self.settings, "espeak_binary", "espeak-ng"),
            espeak_speed=getattr(self.settings, "espeak_speed", 130),
            audio_dir=getattr(self.settings, "audio_dir", ""),
            auto_preview=getattr(self.settings, "tts_auto_preview", False),
            cache_dir=cache_dir,
        )

    # ── Editor pane ───────────────────────────────────────────────────────────

    def _push_string_to_editor_pane(self) -> None:
        row_data = self._get_current_row()
        source_row = self._get_current_source_row()
        self._editor_pane.update_string(row_data, source_row)

    def _toggle_editor_pane(self) -> None:
        visible = self._editor_pane.isVisible()
        self._editor_pane.setVisible(not visible)
        self.editor_pane_action.setChecked(not visible)
        if not visible:
            self._push_string_to_editor_pane()

    @Slot(int, str)
    def _on_editor_pane_approved(self, source_row: int, text: str) -> None:
        """Apply an editor pane translation commit to the model."""
        if 0 <= source_row < len(self.table_model._data):
            self.table_model.set_translated_text(source_row, text)
            self.table_model.string_manually_corrected.emit(
                source_row, self.table_model._data[source_row].get("original", "")
            )

    # ── Detached table window ─────────────────────────────────────────────────

    def _toggle_detached_table(self) -> None:
        if self._detached_table is not None and not self._detached_table.isVisible():
            self._detached_table = None
        if self._detached_table is None:
            self._open_detached_table()
        else:
            self._detached_table.close()

    def _open_detached_table(self) -> None:
        from gui.detached_table_window import DetachedTableWindow
        from PySide6.QtCore import QSettings
        title = self.tr("String List")
        if self.current_path:
            import os
            title += f" — {os.path.basename(self.current_path)}"
        win = DetachedTableWindow(
            table_model=self.table_model,
            selection_model=self.table_view.selectionModel(),
            title=title,
            parent=None,   # top-level window, not child of MainWindow
        )
        win.setAttribute(Qt.WA_DeleteOnClose, True)
        win.destroyed.connect(self._on_detached_table_closed)
        self._detached_table = win
        self.detach_table_action.setChecked(True)
        qs = QSettings("BSE", "BethesdaStringsEditor")
        win.place_and_show(qs)
        win.scroll_to_current()

    @Slot()
    def _on_detached_table_closed(self) -> None:
        self._detached_table = None
        self.detach_table_action.setChecked(False)

    # ── Dock/window state persistence ─────────────────────────────────────────

    def _restore_window_state(self) -> None:
        """Restore main-window geometry and all dock positions."""
        from PySide6.QtCore import QSettings
        qs = QSettings("BSE", "BethesdaStringsEditor")
        geom = qs.value("MainWindow/geometry")
        if geom is not None:
            self.restoreGeometry(geom)
        state = qs.value("MainWindow/windowState")
        if state is not None:
            self.restoreState(state)
            # Sync action checked states with restored dock visibility
            if hasattr(self, "editor_pane_action"):
                self.editor_pane_action.setChecked(self._editor_pane.isVisible())
            if hasattr(self, "audio_panel_action"):
                self.audio_panel_action.setChecked(self._audio_panel.isVisible())
            if hasattr(self, "visual_preview_action"):
                self.visual_preview_action.setChecked(self._visual_preview.isVisible())

    def _save_window_state(self) -> None:
        """Persist main-window geometry and all dock positions."""
        from PySide6.QtCore import QSettings
        qs = QSettings("BSE", "BethesdaStringsEditor")
        qs.setValue("MainWindow/geometry", self.saveGeometry())
        qs.setValue("MainWindow/windowState", self.saveState())
        if self._detached_table is not None:
            self._detached_table.save_geometry_to(qs)

    # ── Focus / Zen mode ──────────────────────────────────────────────────────

    def _toggle_focus_mode(self) -> None:
        if hasattr(self, "_focus_overlay") and self._focus_overlay is not None:
            self._focus_overlay.close()
        else:
            self._enter_focus_mode()

    def _enter_focus_mode(self) -> None:
        from gui.focus_overlay import FocusModeOverlay
        row = self._get_focus_start_row()
        overlay = FocusModeOverlay(self.table_model, row, self)
        overlay.translation_committed.connect(self._on_focus_translation)
        overlay.row_navigated.connect(self._on_focus_row_navigated)
        overlay.destroyed.connect(self._on_focus_overlay_closed)
        overlay.finished.connect(self._on_focus_overlay_closed)
        self._focus_overlay = overlay
        self.focus_mode_action.setChecked(True)
        overlay.show()

    @Slot()
    def _on_focus_overlay_closed(self) -> None:
        self._focus_overlay = None
        self.focus_mode_action.setChecked(False)

    @Slot(int, str)
    def _on_focus_translation(self, source_row: int, text: str) -> None:
        """Apply a translation committed from the focus overlay to the model."""
        if 0 <= source_row < len(self.table_model._data):
            self.table_model.set_translated_text(source_row, text)
            self.table_model.string_manually_corrected.emit(
                source_row, self.table_model._data[source_row].get("original", "")
            )

    @Slot(int)
    def _on_focus_row_navigated(self, source_row: int) -> None:
        """Sync the main table's selection when the overlay navigates."""
        sm = self.table_view.selectionModel()
        if sm is None:
            return
        index = self.table_model.index(source_row, 0)
        if index.isValid():
            sm.select(
                index,
                sm.SelectionFlag.ClearAndSelect | sm.SelectionFlag.Rows,
            )
            self.table_view.scrollTo(index)

    def _get_focus_start_row(self) -> int:
        """Return the model row for the currently selected row, or 0."""
        return self._get_current_source_row()

    # ── Pre-translation estimation ─────────────────────────────────────────────

    def _start_pre_estimation(self) -> None:
        """Begin chunked pre-translation complexity estimation for all pending rows."""
        if self._pre_estimator is None:
            return
        self._pending_est_items = [
            (i, row.get("original", ""))
            for i, row in enumerate(self.table_model._data)
            if row.get("status") == "pending"
        ]
        self._pending_est_results = {}
        self._pending_est_offset = 0
        QTimer.singleShot(80, self._process_est_chunk)

    def _process_est_chunk(self) -> None:
        """Process one chunk of pending rows and schedule the next chunk."""
        if not self._pending_est_items:
            return
        CHUNK = 300
        items = self._pending_est_items
        offset = self._pending_est_offset
        source_lang = self.settings.default_source_lang
        for row_idx, text in items[offset:offset + CHUNK]:
            if self._pre_estimator is not None:
                self._pending_est_results[row_idx] = self._pre_estimator.estimate(
                    text, source_lang
                )
        self._pending_est_offset += CHUNK
        if self._pending_est_offset < len(items):
            QTimer.singleShot(0, self._process_est_chunk)
        else:
            self.table_model.set_pre_est_data(self._pending_est_results)
            self._pending_est_items = []
            self._pending_est_results = {}
            self._pending_est_offset = 0

    @Slot(int, str)
    def _on_string_corrected(self, _row: int, original_text: str) -> None:
        """Forward a user correction signal to the estimator for weight learning."""
        if self._pre_estimator is not None:
            self._pre_estimator.record_correction(
                original_text, self.settings.default_source_lang
            )

    # ── Glossary ───────────────────────────────────────────────────────────────

    def _refresh_glossary_dock(self) -> None:
        """Update the glossary suggestion dock for the currently selected row."""
        if not hasattr(self, "_glossary_list"):
            return
        self._glossary_list.clear()
        if self._glossary_manager is None:
            return

        selected = self.table_view.selectionModel().selectedRows()
        if not selected:
            self._glossary_src_label.setText(
                self.tr("Select a string to see glossary hints.")
            )
            return

        idx = selected[0]
        model = self.table_view.model()
        if hasattr(model, "mapToSource"):
            idx = model.mapToSource(idx)
        row = self.table_model.get_row_data(idx.row())
        source = row.get("original", "")
        translation = row.get("translated", "")

        hits = self._glossary_manager.find_terms_in_text(source)
        if not hits:
            self._glossary_src_label.setText(self.tr("No glossary matches for this string."))
            return

        self._glossary_src_label.setText(
            self.tr("{n} glossary match(es) — double-click to copy target term:").format(
                n=len(hits)
            )
        )
        seen: set = set()
        trans_lower = translation.lower()
        for _s, _e, entry in hits:
            key = entry.source_term.lower()
            if key in seen:
                continue
            seen.add(key)
            present = entry.target_term and entry.target_term.lower() in trans_lower
            icon = "✓" if present else ("⚠" if entry.target_term else "○")
            text = f"{icon}  {entry.source_term}  →  {entry.target_term or '(no translation set)'}"
            if entry.category:
                text += f"  [{entry.category}]"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, entry.target_term)
            if not present and entry.target_term:
                item.setForeground(Qt.darkYellow if not present else Qt.green)
            self._glossary_list.addItem(item)

        if self._glossary_list.count() and self.glossary_suggest_action.isChecked():
            self._glossary_dock.show()

    @Slot()
    def _toggle_glossary_dock(self) -> None:
        if self._glossary_dock.isVisible():
            self._glossary_dock.hide()
            self.glossary_suggest_action.setChecked(False)
        else:
            self._glossary_dock.show()
            self.glossary_suggest_action.setChecked(True)
            self._refresh_glossary_dock()

    @Slot(QListWidgetItem)
    def _on_glossary_item_double_clicked(self, item: QListWidgetItem) -> None:
        """Copy the target term to clipboard on double-click."""
        target = item.data(Qt.UserRole)
        if target:
            QApplication.clipboard().setText(target)
            self.statusBar().showMessage(
                self.tr("Copied \"{term}\" to clipboard.").format(term=target), 3000
            )

    @Slot()
    def _open_glossary_editor(self) -> None:
        if self._glossary_manager is None:
            QMessageBox.information(
                self,
                self.tr("Glossary Disabled"),
                self.tr("Enable the glossary in Settings → Preferences to use this feature."),
            )
            return
        from gui.glossary_editor import GlossaryEditorDialog

        dlg = GlossaryEditorDialog(self._glossary_manager, parent=self)
        dlg.glossary_changed.connect(self._refresh_glossary_dock)
        dlg.exec()

    @Slot()
    def _run_glossary_check(self) -> None:
        """Check all translated strings against the glossary and show a report."""
        if self._glossary_manager is None or not self.current_file:
            return
        from gui.quality_checker import QualityChecker

        checker = QualityChecker()
        issues_by_id: list = []
        for row in self.table_model._data:
            if row.get("status") != "translated":
                continue
            result = checker.check_glossary_compliance(
                row.get("original", ""),
                row.get("translated", ""),
                self._glossary_manager,
            )
            if result:
                issues_by_id.append((row.get("id", 0), result))

        if not issues_by_id:
            QMessageBox.information(
                self,
                self.tr("Glossary Compliance"),
                self.tr("All translated strings comply with the glossary."),
            )
            return

        lines = [f"Found {len(issues_by_id)} string(s) with glossary mismatches:\n"]
        for sid, iss in issues_by_id[:50]:
            for issue in iss:
                lines.append(f"• ID 0x{sid:08X}: {issue.message}")
        if len(issues_by_id) > 50:
            lines.append(f"… and {len(issues_by_id) - 50} more.")

        QMessageBox.warning(
            self,
            self.tr("Glossary Compliance Issues"),
            "\n".join(lines),
        )

    @Slot()
    def _open_diff_viewer(self) -> None:
        """Open the String Diff Viewer for the current file."""
        if not self.current_file:
            return
        from gui.diff_viewer import DiffViewerDialog

        # Resolve initial row from current selection (fall back to 0)
        initial_row = 0
        selection = self.table_view.selectionModel().selectedRows()
        if selection:
            idx = selection[0]
            model = self.table_view.model()
            if hasattr(model, "mapToSource"):
                idx = model.mapToSource(idx)
            initial_row = idx.row()

        rows = list(self.table_model._data)
        comparison_data = dict(self.table_model._diff_data) if self.table_model._diff_data else None

        dlg = DiffViewerDialog(
            rows=rows,
            initial_row=initial_row,
            comparison_data=comparison_data,
            source_lang=self.settings.default_source_lang,
            target_lang=self.settings.default_target_lang,
            parent=self,
        )
        dlg.translation_updated.connect(self.table_model.set_translated_text)
        dlg.exec()

    # ── Encoding display & override ────────────────────────────────────────────

    _COMMON_ENCODINGS = [
        ("utf-8",        "UTF-8 — Modern games (Skyrim, Fallout 4, Starfield)"),
        ("windows-1251", "Windows-1251 — Cyrillic (Russian/Ukrainian legacy games)"),
        ("windows-1252", "Windows-1252 — Western European (Oblivion, early Morrowind)"),
        ("windows-1250", "Windows-1250 — Central European (Polish, Czech)"),
        ("utf-8-sig",    "UTF-8 with BOM"),
    ]

    def _update_encoding_label(self) -> None:
        """Refresh the encoding label from the current file's detected/overridden state."""
        if not self.current_file or isinstance(self.current_file, EspFile):
            self.lbl_encoding.setText(self.tr("Encoding: —"))
            self.btn_encoding_change.setEnabled(False)
            return

        enc, conf, src, method = self.current_file.encoding_info()
        if src == "manual":
            label = self.tr("Encoding: {enc} (manual override)").format(enc=enc)
            tooltip = self.tr("Manually overridden to {enc}").format(enc=enc)
        elif src == "detected":
            label = self.tr("Encoding: {enc} (auto, {conf}%)").format(
                enc=enc, conf=round(conf * 100)
            )
            tooltip = self.tr("Auto-detected: {method}").format(method=method)
        else:
            label = self.tr("Encoding: {enc}").format(enc=enc)
            tooltip = ""

        self.lbl_encoding.setText(label)
        self.lbl_encoding.setToolTip(tooltip)
        self.btn_encoding_change.setEnabled(True)

    @Slot()
    def _override_encoding(self) -> None:
        """Show a dialog to manually override the file encoding and re-decode strings."""
        if not self.current_file or isinstance(self.current_file, EspFile):
            return

        enc, conf, src, method = self.current_file.encoding_info()

        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("Override File Encoding"))
        dialog.setMinimumWidth(480)
        layout = QVBoxLayout(dialog)
        layout.setSpacing(8)

        # Current state info
        info_label = QLabel(
            self.tr(
                "<b>Currently:</b> {enc}<br>"
                "<b>Source:</b> {src}<br>"
                "<b>Method:</b> {method}<br>"
                "<b>Confidence:</b> {conf}%"
            ).format(enc=enc, src=src, method=method, conf=round(conf * 100))
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        from PySide6.QtWidgets import QFrame as _QFrame
        sep = _QFrame()
        sep.setFrameShape(_QFrame.HLine)  # type: ignore[attr-defined]
        layout.addWidget(sep)

        layout.addWidget(QLabel(self.tr("Select encoding to apply:")))

        combo = QComboBox()
        for enc_val, desc in self._COMMON_ENCODINGS:
            combo.addItem(desc, enc_val)
        # Pre-select current encoding
        for i, (enc_val, _) in enumerate(self._COMMON_ENCODINGS):
            if enc_val == enc:
                combo.setCurrentIndex(i)
                break

        layout.addWidget(combo)

        warn = QLabel(
            self.tr(
                "⚠ Changing encoding re-decodes all strings from their raw bytes. "
                "If the file is already UTF-8, choosing CP1251 will produce garbled text."
            )
        )
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #b45309; font-size: 11px;")
        layout.addWidget(warn)

        from PySide6.QtWidgets import QDialogButtonBox
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        layout.addWidget(btns)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_enc = combo.currentData()
        if new_enc == enc and src == "manual":
            return  # No change

        self.current_file.set_encoding(new_enc)
        target_lang = self.combo_target_lang.currentData()
        self.table_model.load_from_bethesda_file(
            self.current_file, encoding=new_enc, locale=target_lang
        )
        self._update_encoding_label()
        self.statusBar().showMessage(
            self.tr("Re-decoded {count} strings as {enc}").format(
                count=len(self.current_file), enc=new_enc
            ),
            5000,
        )
        # Re-run QC with new encoding context
        self._run_quality_check_silent()

    # ── Quality check ──────────────────────────────────────────────────────────

    def _build_quality_map(self):
        """Run QualityChecker over the current table and return (reports, quality_map, checker)."""
        from gui.quality_checker import QualityChecker
        checker = QualityChecker(
            target_encoding=self.table_model._encoding,
            target_language=self.combo_target_lang.currentData(),
            source_language=self.combo_source_lang.currentData(),
        )
        reports = checker.check_all(self.table_model._data)
        quality_map = {r.row_index: r.severity for r in reports if r.severity}
        return reports, quality_map, checker

    def _run_quality_check_silent(self) -> None:
        """Run quality check and update row colours — no dialog."""
        if not self.current_file:
            return
        reports, quality_map, _ = self._build_quality_map()
        self.table_model.set_quality_data(quality_map)
        errors = sum(1 for r in reports if r.severity == "error")
        warnings = sum(1 for r in reports if r.severity == "warning")
        if errors or warnings:
            self.statusBar().showMessage(
                self.tr(
                    "Quality: {errors} error(s), {warnings} warning(s) — "
                    "open Translation → Quality Check for details"
                ).format(errors=errors, warnings=warnings),
                15000,
            )

    @Slot()
    def _open_batch_translate_dialog(self) -> None:
        """Open the Batch Translate Folder dialog."""
        from gui.batch_translate_dialog import BatchTranslateDialog
        dlg = BatchTranslateDialog(parent=self, settings=self.settings)
        dlg.exec()

    # ── Claude AI panel ────────────────────────────────────────────────────────

    @Slot()
    def _toggle_claude_panel(self) -> None:
        if self._claude_panel.isVisible():
            self._claude_panel.hide()
            self.claude_panel_action.setChecked(False)
        else:
            self._claude_panel.show()
            self.claude_panel_action.setChecked(True)
            self._push_string_to_claude_panel()

    def _push_string_to_claude_panel(self) -> None:
        """Send the currently selected string to the Claude chat panel."""
        if not self.current_file:
            return
        rows = self.table_view.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        row = self.table_model.get_row_data(idx)
        self._claude_panel.set_current_string(
            string_id=row.get("id", 0),
            original=row.get("original", ""),
            translation=row.get("translated", ""),
            source_lang=self.combo_source_lang.currentData(),
            target_lang=self.combo_target_lang.currentData(),
        )

    @Slot()
    def _claude_review_current(self) -> None:
        """Open AI panel (if hidden) and trigger a review of the current string."""
        if not self._claude_panel.isVisible():
            self._claude_panel.show()
            self.claude_panel_action.setChecked(True)
        self._push_string_to_claude_panel()
        self._claude_panel._do_review()  # noqa: SLF001

    @Slot()
    def _claude_suggest_current(self) -> None:
        """Open AI panel (if hidden) and ask Claude to suggest a translation."""
        if not self._claude_panel.isVisible():
            self._claude_panel.show()
            self.claude_panel_action.setChecked(True)
        self._push_string_to_claude_panel()
        self._claude_panel._do_suggest()  # noqa: SLF001

    @Slot(str)
    def _apply_claude_translation(self, text: str) -> None:
        """Write a Claude-suggested translation into the selected table row."""
        rows = self.table_view.selectionModel().selectedRows()
        if not rows or not text:
            return
        idx = rows[0].row()
        self.table_model.set_translated_text(idx, text)
        self.statusBar().showMessage(
            self.tr("Claude translation applied to row {row}.").format(row=idx + 1),
            5000,
        )

    @Slot()
    def _run_quality_check(self) -> None:
        """Run quality check and open the results dialog."""
        if not self.current_file:
            return
        from gui.quality_dialog import QualityDialog
        reports, quality_map, checker = self._build_quality_map()

        if getattr(self.settings, "enable_ai_qc", False):
            reports = self._run_ai_qc(reports)
            quality_map = {r.row_index: r.severity for r in reports if r.severity}

        self.table_model.set_quality_data(quality_map)

        if not reports:
            from gui.micro_animations import show_success_badge
            show_success_badge(self, self.tr("Quality check passed — no issues found"))
            return

        dialog = QualityDialog(
            reports,
            table_model=self.table_model,
            checker=checker,
            parent=self,
        )
        dialog.jump_to_row.connect(self._jump_to_row)
        dialog.exec()

        if dialog.pending_retranslations:
            self._retranslate_with_hints(dialog.pending_retranslations)

    def _run_ai_qc(self, reports):
        """Run the AI QC model on all translated rows and merge results into reports."""
        from gui.ai_qc_worker import AiQcWorker
        from gui.quality_checker import QualityReport
        from PySide6.QtCore import QEventLoop
        from PySide6.QtWidgets import QProgressDialog

        rows = self.table_model._data
        items = [
            (i, row.get("id", 0), row.get("original", ""), row.get("translated", ""))
            for i, row in enumerate(rows)
            if row.get("translated", "").strip()
        ]
        if not items:
            return reports

        report_map = {r.row_index: r for r in reports}

        progress_dlg = QProgressDialog(
            self.tr("Running AI quality check ({n} strings)…").format(n=len(items)),
            self.tr("Cancel"),
            0,
            len(items),
            self,
        )
        progress_dlg.setWindowTitle(self.tr("AI Quality Check"))
        progress_dlg.setMinimumDuration(0)
        progress_dlg.setValue(0)

        worker = AiQcWorker(
            items,
            ollama_url=self.settings.ollama_url,
            model=getattr(self.settings, "ai_qc_model", "qcgemma4-st"),
            max_workers=4,
        )
        loop = QEventLoop()

        def _on_result(row_index, issues):
            if row_index in report_map:
                report_map[row_index].issues.extend(issues)
            else:
                row = rows[row_index] if row_index < len(rows) else {}
                new_report = QualityReport(
                    row_index=row_index,
                    string_id=row.get("id", 0),
                    original=row.get("original", ""),
                    translated=row.get("translated", ""),
                    issues=list(issues),
                )
                report_map[row_index] = new_report

        def _on_progress(done, total):
            progress_dlg.setValue(done)
            if progress_dlg.wasCanceled():
                worker.cancel()

        worker.result.connect(_on_result)
        worker.progress.connect(_on_progress)
        worker.finished.connect(loop.quit)
        worker.start()
        loop.exec()
        progress_dlg.close()

        merged = sorted(report_map.values(), key=lambda r: r.row_index)
        return [r for r in merged if r.has_issues]

    def _import_quality_report(self) -> None:
        """Load a saved JSON or CSV quality report and reopen the quality dialog."""
        from gui.file_dialog_helper import get_open_filename
        path, _ = get_open_filename(
            self,
            self.tr("Import Quality Report"),
            "",
            self.tr(
                "Quality Reports (*.json *.csv *);;"
                "JSON Quality Report (*.json);;"
                "CSV Quality Report (*.csv);;"
                "All Files (*)"
            ),
        )
        if not path or not self.current_file:
            return

        from gui.quality_dialog import QualityDialog, load_json, load_csv
        from gui.quality_checker import QualityChecker

        # Detect format: check extension first, then sniff the first line
        import os
        ext = os.path.splitext(path)[1].lower()
        if ext == ".json":
            is_csv = False
        elif ext == ".csv":
            is_csv = True
        else:
            # No extension — sniff by looking at the first non-empty line
            try:
                with open(path, encoding="utf-8-sig", errors="replace") as _f:
                    first = _f.readline().strip()
                is_csv = first.startswith("Severity,") or first.startswith('"Severity"')
            except Exception:
                is_csv = False

        try:
            if is_csv:
                reports, remap_warnings = load_csv(path, self.table_model._data)
            else:
                reports, remap_warnings = load_json(path, self.table_model._data)
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                self,
                self.tr("Import Failed"),
                self.tr("Could not load quality report:\n{error}").format(error=str(exc)),
            )
            return

        if remap_warnings:
            logger.warning(
                "Quality report import: %d string(s) could not be remapped: %s",
                len(remap_warnings), "; ".join(remap_warnings[:5]),
            )

        quality_map = {r.row_index: r.severity for r in reports if r.severity}
        self.table_model.set_quality_data(quality_map)

        checker = QualityChecker(
            target_encoding=self.table_model._encoding,
            target_language=self.combo_target_lang.currentData(),
            source_language=self.combo_source_lang.currentData(),
        )
        dialog = QualityDialog(reports, table_model=self.table_model, checker=checker, parent=self)
        dialog.jump_to_row.connect(self._jump_to_row)

        if remap_warnings:
            self.statusBar().showMessage(
                self.tr(
                    "Quality report imported — {ok} strings matched, {skip} skipped"
                ).format(ok=len(reports), skip=len(remap_warnings)),
                8000,
            )
        else:
            self.statusBar().showMessage(
                self.tr("Quality report imported — {n} strings").format(n=len(reports)),
                5000,
            )

        dialog.exec()
        if dialog.pending_retranslations:
            self._retranslate_with_hints(dialog.pending_retranslations)

    def _export_training_data(self) -> None:
        """Export translated pairs as a JSONL fine-tuning dataset (ShareGPT format)."""
        import json
        from datetime import datetime

        if not self.current_file:
            return

        rows = self.table_model._data
        quality_errors = {
            row_idx
            for row_idx, sev in self.table_model._quality_data.items()
            if sev == "error"
        }

        translated_rows = [
            (i, r) for i, r in enumerate(rows)
            if r.get("status") == "translated" and r.get("translated", "").strip()
        ]

        if not translated_rows:
            QMessageBox.information(
                self,
                self.tr("Export Training Data"),
                self.tr("No translated strings found. Translate some strings first."),
            )
            return

        clean_rows = [(i, r) for i, r in translated_rows if i not in quality_errors]

        source_lang = self.combo_source_lang.currentData() or "en"
        target_lang = self.combo_target_lang.currentData() or "uk"
        system_prompt = TranslationRequest(
            index=0,
            original_text="",
            string_id=0,
            source_lang=source_lang,
            target_lang=target_lang,
        ).to_system_prompt()

        msg = self.tr(
            "Ready to export:\n\n"
            "  • {total} translated strings total\n"
            "  • {clean} without quality errors\n\n"
            "Export which set?"
        ).format(total=len(translated_rows), clean=len(clean_rows))

        box = QMessageBox(self)
        box.setWindowTitle(self.tr("Export Training Data"))
        box.setText(msg)
        btn_clean = box.addButton(
            self.tr("Clean only ({n})").format(n=len(clean_rows)),
            QMessageBox.ActionRole,
        )
        btn_all = box.addButton(
            self.tr("All translated ({n})").format(n=len(translated_rows)),
            QMessageBox.ActionRole,
        )
        box.addButton(QMessageBox.Cancel)
        box.exec()

        clicked = box.clickedButton()
        if clicked is btn_clean:
            export_rows = clean_rows
        elif clicked is btn_all:
            export_rows = translated_rows
        else:
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"training_data_{timestamp}.jsonl"
        save_path, _ = get_save_filename(
            self,
            self.tr("Export Training Data"),
            default_name,
            self.tr("JSONL Dataset (*.jsonl);;All files (*)"),
        )
        if not save_path:
            return

        written = 0
        try:
            with open(save_path, "w", encoding="utf-8") as f:
                for _i, row in export_rows:
                    original = row.get("original", "").strip()
                    translated = row.get("translated", "").strip()
                    if not original or not translated:
                        continue
                    user_turn = f"To {target_lang}:\n{original}"
                    record = {
                        "conversations": [
                            {"from": "system",  "value": system_prompt},
                            {"from": "human",   "value": user_turn},
                            {"from": "gpt",     "value": translated},
                        ]
                    }
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    written += 1
        except Exception as exc:
            QMessageBox.critical(
                self,
                self.tr("Export Failed"),
                self.tr("Could not write file:\n{error}").format(error=str(exc)),
            )
            return

        self.statusBar().showMessage(
            self.tr("Training data exported — {n} examples → {path}").format(
                n=written, path=save_path
            ),
            8000,
        )
        logger.info("Exported %d training examples to %s", written, save_path)

    @Slot()
    def _auto_retranslate_errors(self) -> None:
        """Run QC silently, then queue all error/warning strings for AI retranslation."""
        if not self.current_file:
            return
        from gui.quality_checker import QualityChecker, SEVERITY_ERROR, SEVERITY_WARNING

        reports, quality_map, _ = self._build_quality_map()
        self.table_model.set_quality_data(quality_map)

        retranslation_list = []
        for report in reports:
            if report.severity in (SEVERITY_ERROR, SEVERITY_WARNING):
                hint = QualityChecker.build_retry_hint(report.issues)
                retranslation_list.append((report.row_index, hint))

        if not retranslation_list:
            QMessageBox.information(
                self,
                self.tr("Auto-Retranslate"),
                self.tr("No errors or warnings found — translations look good."),
            )
            return

        n_errors = sum(1 for r in reports if r.severity == SEVERITY_ERROR)
        n_warnings = sum(1 for r in reports if r.severity == SEVERITY_WARNING)
        result = QMessageBox.question(
            self,
            self.tr("Auto-Retranslate Issues"),
            self.tr(
                "Found {n} string(s) with quality issues "
                "({e} error(s), {w} warning(s)).\n\n"
                "Retranslate them all with quality feedback hints?"
            ).format(n=len(retranslation_list), e=n_errors, w=n_warnings),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            self._retranslate_with_hints(retranslation_list)

    def _retranslate_with_hints(self, retranslation_list: list) -> None:
        """Start a targeted retranslation batch with quality-feedback retry hints."""
        if not retranslation_list or not self.ollama_worker:
            return

        source_lang = self.combo_source_lang.currentData()
        target_lang = self.combo_target_lang.currentData()
        quality = self.spin_quality.value()

        protect_english = self.settings.protect_english_text
        if source_lang == "en":
            protect_english = False

        requests = []
        for row_index, retry_hint in retranslation_list:
            if row_index >= len(self.table_model._data):
                continue
            row = self.table_model._data[row_index]
            original = row.get("original", "")
            if not original.strip():
                continue

            glossary_snippet = ""
            if self._glossary_manager:
                glossary_snippet = self._glossary_manager.build_prompt_snippet(original)

            requests.append(
                TranslationRequest(
                    index=row_index,
                    original_text=original,
                    string_id=row.get("id", 0),
                    source_lang=source_lang,
                    target_lang=target_lang,
                    quality_level=quality,
                    protected_terms_enabled=self.settings.enable_term_protection,
                    protect_english_text=protect_english,
                    glossary_snippet=glossary_snippet,
                    retry_hint=retry_hint,
                    model_override="",
                )
            )

        if not requests:
            return

        n = len(requests)
        logger.info(f"Retranslating {n} string(s) with quality feedback hints")
        self.statusBar().showMessage(
            self.tr("Retranslating {n} string(s) with quality feedback…").format(n=n),
            0,
        )
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, n)
        self.progress_bar.setValue(0)
        self.lbl_progress.setText(
            self.tr("Retranslating {current}/{total}…").format(current=0, total=n)
        )
        self._set_ui_enabled(False)
        self._eta_start_time = time.monotonic()
        self._eta_batch_total = len(requests)
        self.translation_requested.emit(requests)

    @Slot(int)
    def _jump_to_row(self, row_index: int) -> None:
        """Select and scroll to a specific row in the string table."""
        index = self.table_model.index(row_index, 0)
        self.table_view.scrollTo(index)
        self.table_view.selectRow(row_index)

    def _set_ui_enabled(self, enabled: bool):
        """Enable/disable UI elements during translation."""
        # Keep table view enabled for scrolling and viewing
        # but disable selection during translation
        self.table_view.setEnabled(True)
        self.table_view.setSelectionMode(
            QTableView.NoSelection if not enabled else QTableView.ExtendedSelection
        )

        # Disable controls that could interfere with translation
        self.combo_source_lang.setEnabled(enabled)
        self.combo_target_lang.setEnabled(enabled)
        self.spin_quality.setEnabled(enabled)
        self.save_action.setEnabled(enabled and self.current_file is not None)
        self.translate_selected_action.setEnabled(False)
        self.translate_all_action.setEnabled(False)
        if hasattr(self, "stop_translation_action"):
            self.stop_translation_action.setEnabled(not enabled)

    # ── Approve / Reject ──────────────────────────────────────────────────────

    @Slot()
    def _approve_selected(self) -> None:
        """Accept the current AI translation and advance to the next row."""
        if not self.current_file:
            return
        rows = [idx.row() for idx in self.table_view.selectionModel().selectedRows()]
        if not rows:
            return
        # Advance selection to row after last selected
        next_row = max(rows) + 1
        count = self.table_model.rowCount()
        if next_row < count:
            self._jump_to_row(next_row)
        elif rows:
            self._jump_to_row(rows[-1])

    @Slot()
    def _reject_selected(self) -> None:
        """Clear translation for selected rows, marking them as pending."""
        if not self.current_file:
            return
        rows = [idx.row() for idx in self.table_view.selectionModel().selectedRows()]
        if not rows:
            return
        for row in rows:
            row_data = self.table_model.get_row_data(row)
            if row_data.get("status") == "translated":
                self.table_model._data[row]["translated"] = ""
                self.table_model._data[row]["status"] = "pending"
        self.table_model.layoutChanged.emit()
        self.statusBar().showMessage(
            self.tr("Rejected {n} translation(s)").format(n=len(rows))
        )

    # ── Navigation ─────────────────────────────────────────────────────────────

    @Slot()
    def _next_untranslated(self) -> None:
        """Jump to the next row with status 'pending'."""
        if not self.current_file:
            return
        current = self.table_view.currentIndex().row()
        data = self.table_model._data
        for i in range(current + 1, len(data)):
            if data[i].get("status") != "translated":
                self._jump_to_row(i)
                return
        # Wrap around from beginning
        for i in range(0, current + 1):
            if data[i].get("status") != "translated":
                self._jump_to_row(i)
                self.statusBar().showMessage(self.tr("Wrapped to first untranslated"))
                return
        self.statusBar().showMessage(self.tr("No untranslated strings remaining"))

    @Slot()
    def _prev_untranslated(self) -> None:
        """Jump to the previous row with status 'pending'."""
        if not self.current_file:
            return
        current = self.table_view.currentIndex().row()
        data = self.table_model._data
        for i in range(current - 1, -1, -1):
            if data[i].get("status") != "translated":
                self._jump_to_row(i)
                return
        # Wrap around from end
        for i in range(len(data) - 1, current - 1, -1):
            if data[i].get("status") != "translated":
                self._jump_to_row(i)
                self.statusBar().showMessage(self.tr("Wrapped to last untranslated"))
                return
        self.statusBar().showMessage(self.tr("No untranslated strings remaining"))

    # ── Command palette ───────────────────────────────────────────────────────

    @Slot()
    def _open_command_palette(self) -> None:
        """Open the Ctrl+K command palette."""
        from gui.command_palette import CommandPaletteDialog
        dialog = CommandPaletteDialog(self.keyboard_manager, parent=self)
        dialog.exec()

    # ── Macro editor ──────────────────────────────────────────────────────────

    @Slot()
    def _open_macro_dialog(self) -> None:
        """Open the macro editor dialog (Ctrl+M / q in table)."""
        from gui.macro_dialog import MacroDialog
        selected = [idx.row() for idx in self.table_view.selectionModel().selectedRows()]
        dlg = MacroDialog(self.macro_recorder, self.table_model, selected, parent=self)
        dlg.exec()

    @Slot()
    def _replay_macro_on_current(self) -> None:
        """Replay the current macro on the focused row only (@ in table)."""
        if not self.current_file or not self.macro_recorder.steps:
            return
        row = self.table_view.currentIndex().row()
        if row < 0:
            return
        modified = self.macro_recorder.replay_on_rows(self.table_model, [row])
        self.table_model.layoutChanged.emit()
        if modified:
            self.statusBar().showMessage(self.tr("Macro applied to row {n}.").format(n=row))
        else:
            self.statusBar().showMessage(self.tr("Macro: no changes on row {n}.").format(n=row))

    # ── Action registration ────────────────────────────────────────────────────

    def _register_actions(self) -> None:
        """Register all main-window actions with the KeyboardManager."""
        km = self.keyboard_manager
        has = lambda: self.current_file is not None
        has_sel = lambda: self.current_file is not None and self.table_view.selectionModel().hasSelection()

        # File
        km.register_qaction("open_file", QAction(self.tr("Open File"), self), "File",
                             description=self.tr("Open a string or plugin file"),
                             keywords=("open", "load", "file"))
        km.register_qaction("save_file", self.save_action, "File",
                             description=self.tr("Save the current file"),
                             enabled_check=has)
        km.register_qaction("save_file_as", self.save_as_action, "File",
                             description=self.tr("Save the current file to a new location"),
                             enabled_check=has)

        # Translation
        km.register_qaction("translate_selected", self.translate_selected_action, "Translation",
                             description=self.tr("Translate the selected strings using AI"),
                             keywords=("ai", "ollama", "translate"),
                             enabled_check=has_sel)
        km.register_qaction("translate_all", self.translate_all_action, "Translation",
                             description=self.tr("Translate all untranslated strings"),
                             keywords=("ai", "all"),
                             enabled_check=has)
        km.register_qaction("approve_selected", self.approve_action, "Translation",
                             description=self.tr("Accept the AI translation and advance to next row"),
                             keywords=("accept", "confirm", "approve"),
                             enabled_check=has_sel)
        km.register_qaction("reject_selected", self.reject_action, "Translation",
                             description=self.tr("Clear the translation and mark as pending"),
                             keywords=("clear", "discard", "reject"),
                             enabled_check=has_sel)
        km.register_qaction("stop_translation", self.stop_translation_action, "Translation",
                             description=self.tr("Stop the in-progress translation batch"))

        # Navigation
        km.register_qaction("next_untranslated", self.next_untranslated_action, "Navigation",
                             description=self.tr("Jump to the next untranslated string"),
                             keywords=("navigate", "jump", "pending"),
                             enabled_check=has)
        km.register_qaction("prev_untranslated", self.prev_untranslated_action, "Navigation",
                             description=self.tr("Jump to the previous untranslated string"),
                             keywords=("navigate", "jump", "pending"),
                             enabled_check=has)

        # Edit
        km.register_qaction("advanced_search", self.search_action, "Edit",
                             description=self.tr("Search strings by ID, text, or status"),
                             keywords=("find", "filter", "search"),
                             enabled_check=has)

        # Quality
        km.register_qaction("quality_check", self.quality_check_action, "Quality",
                             description=self.tr("Run post-translation quality checks"),
                             keywords=("qa", "check", "review"),
                             enabled_check=has)
        km.register_qaction("auto_retranslate", self.auto_retranslate_action, "Quality",
                             description=self.tr("Retranslate all rows with quality errors using feedback hints"),
                             keywords=("fix", "retranslate"),
                             enabled_check=has)

        # Glossary
        km.register_qaction("edit_glossary", self.glossary_editor_action, "Glossary",
                             description=self.tr("Open the glossary editor"))
        km.register_qaction("toggle_glossary_dock", self.glossary_suggest_action, "Glossary",
                             description=self.tr("Show or hide the glossary suggestions panel"))

        # Settings
        km.register_qaction("command_palette", self.command_palette_action, "Settings",
                             description=self.tr("Open the searchable command palette"),
                             keywords=("palette", "commands", "search"))
        km.register_qaction("open_settings", QAction(self.tr("Preferences"), self), "Settings",
                             description=self.tr("Open the Preferences dialog"),
                             keywords=("settings", "preferences", "config"))

        # Import / Export
        km.register_qaction("import_txt", self.import_txt_action, "Import/Export",
                             description=self.tr("Import translations from a TXT file"),
                             enabled_check=has)
        km.register_qaction("export_txt", self.export_txt_action, "Import/Export",
                             description=self.tr("Export translations to a TXT file"),
                             enabled_check=has)
        km.register_qaction("import_xml", self.import_xml_action, "Import/Export",
                             description=self.tr("Import from xTranslator SST XML"),
                             enabled_check=has)
        km.register_qaction("export_xml", self.export_xml_action, "Import/Export",
                             description=self.tr("Export to xTranslator SST XML"),
                             enabled_check=has)

        # Vim navigation info entries (not QAction-backed, handled in StringTableView)
        km.register(ActionEntry(
            id="vim_j", name="Navigate Down (vim j)", description="Move selection down one row",
            default_shortcut="J", callback=lambda: None,
            category="Navigation", keywords=("vim", "down", "j"),
        ))
        km.register(ActionEntry(
            id="vim_k", name="Navigate Up (vim k)", description="Move selection up one row",
            default_shortcut="K", callback=lambda: None,
            category="Navigation", keywords=("vim", "up", "k"),
        ))
        km.register(ActionEntry(
            id="vim_gg", name="Go to First Row (vim gg)", description="Jump to the first string",
            default_shortcut="G, G", callback=lambda: None,
            category="Navigation", keywords=("vim", "top", "first", "gg"),
        ))
        km.register(ActionEntry(
            id="vim_G", name="Go to Last Row (vim G)", description="Jump to the last string",
            default_shortcut="Shift+G", callback=lambda: None,
            category="Navigation", keywords=("vim", "bottom", "last", "G"),
        ))

        # Macro
        km.register_qaction("macro_editor", self.macro_action, "Macro",
                             description=self.tr("Open macro editor for batch regex-replace"),
                             keywords=("macro", "batch", "replace", "regex", "vim"),
                             enabled_check=has)
        km.register(ActionEntry(
            id="macro_replay", name="Replay Macro (@)", description="Replay last macro on the current row",
            default_shortcut="@", callback=self._replay_macro_on_current,
            category="Macro", keywords=("macro", "replay", "vim", "at"),
            enabled_check=has,
        ))

    @Slot()
    def open_settings(self):
        """Open settings dialog."""
        dialog = SettingsDialog(
            self.settings,
            self,
            term_protector=self.term_protector,
            theme_manager=self.theme_manager,
            translation_cache=self.translation_cache,
            keyboard_manager=self.keyboard_manager,
        )
        if dialog.exec() == QDialog.Accepted:
            # Apply settings from dialog
            dialog.apply_to_settings(self.settings)

            # Save immediately (not just on close)
            errors = self.settings.validate()
            if errors:
                QMessageBox.warning(
                    self,
                    "Settings Validation",
                    "Settings have validation issues:\n"
                    + "\n".join(f"• {e}" for e in errors),
                )
                return

            # Apply custom shortcuts from the dialog
            self.keyboard_manager.load_custom_shortcuts(self.settings.custom_shortcuts)
            self.keyboard_manager.apply_all_custom_shortcuts()

            save_settings(self.settings)

            # Propagate audit log config
            self._audit_log.configure(
                path=get_config_dir() / "audit.jsonl",
                enabled=self.settings.audit_logging,
            )

            # Log settings change (key names only, no values)
            self._audit_log.settings_changed(list(vars(self.settings).keys()))

            # Apply theme if it changed
            if self.theme_manager:
                new_theme = self.settings.theme
                if new_theme != self.theme_manager.current_theme:
                    from gui.app_settings import apply_theme

                    apply_theme(QApplication.instance(), new_theme)
                    logger.info(f"Theme changed to: {new_theme}")
                    self.table_model.invalidate_type_cache()

            # Reconfigure cache if enable_cache setting changed
            if (
                self.settings.enable_cache
                and self.translation_cache._cache_path is None
            ):
                self.translation_cache._cache_path = (
                    get_cache_dir() / "translation_cache.json"
                )
                self.translation_cache.load()
            elif not self.settings.enable_cache:
                self.translation_cache._cache_path = None

            # Propagate encryption flag (takes effect on next save)
            if self.translation_cache._encrypt != self.settings.encrypt_cache:
                self.translation_cache._encrypt = self.settings.encrypt_cache
                try:
                    from gui.secret_store import get_store
                    _backend = get_store().backend_name()
                except Exception:
                    _backend = "unknown"
                self._audit_log.cache_encryption_changed(
                    self.settings.encrypt_cache, _backend
                )

            # Update existing worker config
            enable_protection = self.settings.enable_term_protection
            if self.ollama_worker:
                self.ollama_worker.update_config(
                    base_url=self.settings.ollama_url,
                    model=self.settings.ollama_model,
                    enable_term_protection=enable_protection,
                    protect_named_entities=self.settings.protect_named_entities,
                    term_protector=self.term_protector if enable_protection else None,
                    translation_cache=self.translation_cache
                    if self.settings.enable_cache
                    else None,
                    max_workers=self.settings.max_workers,
                    ollama_num_thread=self.settings.ollama_num_thread,
                    ollama_num_predict=self.settings.ollama_num_predict,
                    ollama_num_ctx=self.settings.ollama_num_ctx,
                    long_string_threshold=self.settings.long_string_threshold,
                    long_string_action=self.settings.long_string_action,
                )
                self.ollama_worker.tm_fuzzy_max_score = self.settings.tm_fuzzy_max_score
            # Propagate color-blind mode to the table model immediately
            self.table_model.set_color_blind_mode(self.settings.color_blind_mode)

            # Apply audio preview settings to panel
            self._apply_audio_settings()
            self._audio_panel.setVisible(self.settings.enable_audio_preview)
            self.audio_panel_action.setChecked(self.settings.enable_audio_preview)

            self.statusBar().showMessage("Settings updated")

            # Update UI to reflect new settings
            self._update_ui_state()

    def closeEvent(self, event):
        """Cleanup on window close."""
        try:
            logger.info("Closing application...")

            # Update settings from UI state before saving
            self.settings.default_source_lang = self.combo_source_lang.currentData()
            self.settings.default_target_lang = self.combo_target_lang.currentData()
            self.settings.quality_level = self.spin_quality.value()

            # Close open BA2 archive (file handle)
            self._close_current_ba2()

            # Stop workers
            if self.ollama_worker:
                self.ollama_worker.stop()

            # Wait for threads to finish
            if self.ollama_thread and self.ollama_thread.isRunning():
                self.ollama_thread.quit()
                if not self.ollama_thread.wait(2000):
                    logger.warning("Force terminating Ollama thread")
                    # Explicitly release the HTTP session before terminate() kills the
                    # thread, since terminate() skips Python finalizers (__del__).
                    if self.ollama_worker:
                        self.ollama_worker.close()
                    self.ollama_thread.terminate()

            # Save translation cache to disk
            try:
                if self.settings.enable_cache:
                    self.translation_cache.save()
            except Exception as e:
                logger.warning(f"Failed to save translation cache: {e}")

            # Save settings to disk (both JSON and QSettings)
            try:
                save_settings(self.settings)
                logger.info(f"Settings saved to {get_config_path()}")
            except Exception as e:
                logger.warning(f"Failed to save settings: {e}")

            # Auto-save active session
            try:
                if self._current_session is not None:
                    self._session_capture_state(self._current_session)
                    self._session_store.save(self._current_session)
            except Exception as e:
                logger.warning(f"Failed to auto-save session: {e}")

            # Clean exit — remove any crash recovery snapshot
            try:
                self._recovery_manager.clear()
            except Exception as e:
                logger.warning(f"Failed to clear recovery snapshot: {e}")

            self._audit_log.app_close()
            self._save_window_state()
            logger.info("Application closed")
        except Exception as e:
            logger.error(f"Error during close: {e}", exc_info=True)
        finally:
            event.accept()

    @Slot()
    def export_to_txt(self):
        """Export strings to a text file."""
        if not self.current_file:
            return

        # Ask user for export mode
        mode_dialog = ExportModeDialog(self)
        if mode_dialog.exec() != QDialog.Accepted:
            return

        export_mode = mode_dialog.get_selected_mode()

        # Default filename
        default_name = (
            f"{self.current_path.stem}_translated.txt"
            if self.current_path
            else "output.txt"
        )
        file_path, _ = get_save_filename(
            self,
            self.tr("Export to TXT"),
            str(Path.home() / default_name),
            self.tr("Text Files (*.txt *.TXT);;All Files (*)"),
        )
        if not file_path:
            return

        try:
            self.statusBar().showMessage(
                self.tr("Exporting to {filename}...").format(
                    filename=Path(file_path).name
                )
            )

            # Get target encoding
            target_lang = self.combo_target_lang.currentData()
            encoding, fallback = EncodingConverter.get_encodings_for_locale(target_lang)

            total_count = len(self.table_model._data)

            # Export strings in tab-separated format
            exported_count = 0
            with open(file_path, "w", encoding="utf-8") as f:
                # Write header
                f.write("# Bethesda Strings Export\n")
                f.write(f"# Source: {self.current_path.name if self.current_path else 'unknown'}\n")
                f.write(f"# Total strings: {total_count}\n")
                f.write(f"# Export mode: {export_mode}\n")
                f.write('# Format: 0xID\t"Original"\t"Translated"\n')
                f.write("#" + "=" * 80 + "\n")

                if export_mode == "All":
                    # Export all strings with line numbers
                    for line_num, row_data in enumerate(self.table_model._data, 1):
                        string_id = row_data["id"]
                        original = row_data["original"]
                        translated = row_data.get("translated", "")
                        status = row_data["status"]

                        original_escaped = self._escape_string(original)
                        if translated and status == "translated":
                            translated_escaped = self._escape_string(translated)
                        else:
                            translated_escaped = ""

                        # Write in line-numbered format: {line_num} {hex_id} "{original}" "{translated}"
                        f.write(
                            f'{line_num} 0x{string_id:08X} "{original_escaped}" "{translated_escaped}"\n'
                        )
                        exported_count += 1
                elif export_mode == "Translated only":
                    # Export only translated strings with line numbers
                    line_num = 0
                    for row_data in self.table_model._data:
                        string_id = row_data["id"]
                        original = row_data["original"]
                        translated = row_data.get("translated", "")
                        status = row_data["status"]

                        if not translated or status != "translated":
                            continue

                        line_num += 1

                        original_escaped = self._escape_string(original)
                        translated_escaped = self._escape_string(translated)

                        # Write in line-numbered format: {line_num} {hex_id} "{original}" "{translated}"
                        f.write(
                            f'{line_num} 0x{string_id:08X} "{original_escaped}" "{translated_escaped}"\n'
                        )
                        exported_count += 1

            self.statusBar().showMessage(
                self.tr("Exported {count} strings to {filename} ✓").format(
                    count=exported_count, filename=Path(file_path).name
                )
            )
            QMessageBox.information(
                self,
                self.tr("Export Complete"),
                self.tr("Successfully exported {count} strings to:\n{path}").format(
                    count=exported_count, path=file_path
                ),
            )

        except Exception as e:
            logger.error(f"Export failed: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to export:\n{error}").format(error=e),
            )

    @staticmethod
    def _escape_string(s: str) -> str:
        """Escape special characters for TXT export (reverse of _unescape_string).

        Order matters: backslashes must be escaped first to avoid double-escaping.
        """
        return (
            s.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )

    @staticmethod
    def _unescape_string(s: str) -> str:
        """Unescape special characters for import. Reverse of export escaping.

        Handles: \\\\ → \\, \\n → newline, \\r → CR, \\t → tab, \\" → "
        """
        # Process escape sequences in order (longest first to avoid partial matches)
        # We need to handle \\\\ before \\n etc. to avoid double-processing
        result = []
        i = 0
        while i < len(s):
            if s[i] == "\\" and i + 1 < len(s):
                next_char = s[i + 1]
                if next_char == "\\":
                    result.append("\\")
                    i += 2
                elif next_char == "n":
                    result.append("\n")
                    i += 2
                elif next_char == "r":
                    result.append("\r")
                    i += 2
                elif next_char == "t":
                    result.append("\t")
                    i += 2
                elif next_char == '"':
                    result.append('"')
                    i += 2
                else:
                    # Unknown escape, keep as-is
                    result.append(s[i])
                    i += 1
            else:
                result.append(s[i])
                i += 1
        return "".join(result)

    def _process_import_matches(
        self, matches: list, id_to_row: dict, unescape: bool = True
    ) -> tuple:
        """Process regex match objects for TXT import.

        Each match must have groups: (1) hex ID, (2) original text, (3) translated text.

        Args:
            matches: List of regex match objects.
            id_to_row: Mapping from string_id (int) to table row index.
            unescape: If True, unescape backslash sequences; if False, strip whitespace only.

        Returns:
            Tuple of (imported_count, skipped_count).
        """
        total = len(matches)
        imported_count = 0
        skipped_count = 0

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(0)
        self.lbl_progress.setText(
            self.tr("Importing {current}/{total}...").format(current=0, total=total)
        )
        self._set_ui_enabled(False)

        chunk_size = 1000
        for i, match in enumerate(matches):
            string_id_hex = match.group(1)
            original_text = match.group(2)
            translated_text = match.group(3)

            if unescape:
                original_text = self._unescape_string(original_text)
                translated_text = self._unescape_string(translated_text)
            else:
                original_text = original_text.strip()
                translated_text = translated_text.strip()

            if not translated_text or translated_text.upper() == "[NOT TRANSLATED]":
                skipped_count += 1
                continue

            string_id = int(string_id_hex, 16)
            if string_id in id_to_row:
                self.table_model.set_translated_text(
                    id_to_row[string_id], translated_text
                )
                imported_count += 1

            if (i + 1) % chunk_size == 0 or i == total - 1:
                self.progress_bar.setValue(i + 1)
                self.lbl_progress.setText(
                    self.tr("Importing {current}/{total}...").format(
                        current=i + 1, total=total
                    )
                )
                self.statusBar().showMessage(
                    self.tr("Importing: {current}/{total}").format(
                        current=i + 1, total=total
                    )
                )
                QApplication.processEvents()

        return imported_count, skipped_count

    @Slot()
    def import_from_txt(self):
        """Import translations from a text file."""
        if not self.current_file:
            return

        # Open file dialog
        file_path, _ = get_open_filename(
            self,
            self.tr("Import from TXT"),
            "",
            self.tr("Text Files (*.txt *.TXT);;All Files (*)"),
        )
        if not file_path:
            return

        try:
            self.statusBar().showMessage(
                self.tr("Importing from {filename}...").format(
                    filename=Path(file_path).name
                )
            )

            # Read the TXT file
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Build a lookup dictionary: string_id -> row_idx
            # This makes lookups O(1) instead of O(n)
            id_to_row = {}
            for row_idx in range(self.table_model.rowCount()):
                row_data = self.table_model.get_row_data(row_idx)
                id_to_row[row_data["id"]] = row_idx

            imported_count = 0
            skipped_count = 0

            # Try Format 1: Line-numbered format ({line_num} {hex_id} "{original}" "{translated}")
            line_numbered_pattern = re.compile(
                r'^\d+\s+0x([0-9A-Fa-f]+)\s+"(.*?)"\s+"(.*?)"$', re.MULTILINE
            )

            # Try Format 2: Tab-separated (ID\t"Original"\t"Translated")
            tab_pattern = re.compile(r'0x([0-9A-Fa-f]+)\t"(.+?)"\t"(.+?)"')

            # Try Format 3: Multi-line format ([0xID]\nOriginal\nTranslation\n\n)
            multiline_pattern = re.compile(
                r"\[0x([0-9A-Fa-f]+)\]\n(.+?)\n(.+?)(?:\n\n|$)", re.DOTALL
            )

            # Detect which format to use (priority: line-numbered > tab-separated > multiline)
            if line_numbered_pattern.search(content):
                matches = list(line_numbered_pattern.finditer(content))
                imported_count, skipped_count = self._process_import_matches(
                    matches, id_to_row, unescape=True
                )
            elif tab_pattern.search(content):
                matches = list(tab_pattern.finditer(content))
                imported_count, skipped_count = self._process_import_matches(
                    matches, id_to_row, unescape=True
                )
            else:
                matches = list(multiline_pattern.finditer(content))
                imported_count, skipped_count = self._process_import_matches(
                    matches, id_to_row, unescape=False
                )

            # Hide progress bar
            self.progress_bar.setVisible(False)
            self._set_ui_enabled(True)

            self.statusBar().showMessage(
                self.tr("Imported {count} translations from {filename} ✓").format(
                    count=imported_count, filename=Path(file_path).name
                )
            )

            msg = self.tr(
                "Successfully imported {count} translations from:\n{path}"
            ).format(count=imported_count, path=file_path)
            if skipped_count > 0:
                msg += self.tr("\n\n(Skipped {count} untranslated entries)").format(
                    count=skipped_count
                )

            QMessageBox.information(self, self.tr("Import Complete"), msg)

        except Exception as e:
            self.progress_bar.setVisible(False)
            self._set_ui_enabled(True)
            logger.error(f"Import failed: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to import:\n{error}").format(error=e),
            )

    @Slot()
    def import_from_xml(self):
        """Import translations from an SST XML file."""
        if not self.current_file:
            return

        file_path, _ = get_open_filename(
            self,
            self.tr("Import from XML (SST)"),
            "",
            self.tr("XML Files (*.xml *.sst);;All Files (*)"),
        )
        if not file_path:
            return

        try:
            self.statusBar().showMessage(
                self.tr("Importing from XML {filename}...").format(
                    filename=Path(file_path).name
                )
            )

            sst = XMLHandler.parse_sst_xml(file_path)

            if not sst.count:
                QMessageBox.warning(
                    self,
                    self.tr("No Translations"),
                    self.tr("No valid translations found in the XML file."),
                )
                return

            applied_count = self.table_model.import_translations(
                sst.by_id, sst.by_source
            )

            self.statusBar().showMessage(
                self.tr("Imported {count} translations from XML ✓").format(
                    count=applied_count
                )
            )
            QMessageBox.information(
                self,
                self.tr("Import Complete"),
                self.tr("Successfully imported {count} translations from XML.").format(
                    count=applied_count
                ),
            )

        except Exception as e:
            logger.error(f"XML import failed: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to import XML:\n{error}").format(error=e),
            )

    @Slot()
    def export_to_xml(self):
        """Export translations to an SST XML file."""
        if not self.current_file:
            return

        default_name = (
            f"{self.current_path.stem}.xml" if self.current_path else "export.xml"
        )
        file_path, _ = get_save_filename(
            self,
            self.tr("Export to XML (SST)"),
            str(Path.home() / default_name),
            self.tr("XML Files (*.xml);;All Files (*)"),
        )
        if not file_path:
            return

        try:
            self.statusBar().showMessage(
                self.tr("Exporting to XML {filename}...").format(
                    filename=Path(file_path).name
                )
            )

            # Prepare data for export
            data_to_export = []
            for row in self.table_model._data:
                # We export all entries, but SST format usually includes what's available
                data_to_export.append(
                    {
                        "id": row["id"],
                        "original": row["original"],
                        "translated": row.get("translated", ""),
                    }
                )

            source_lang = self.combo_source_lang.currentData()
            dest_lang = self.combo_target_lang.currentData()

            XMLHandler.write_sst_xml(file_path, data_to_export, source_lang, dest_lang)

            self.statusBar().showMessage(
                self.tr("Exported {count} entries to XML ✓").format(
                    count=len(data_to_export)
                )
            )
            QMessageBox.information(
                self,
                self.tr("Export Complete"),
                self.tr("Successfully exported {count} entries to XML.").format(
                    count=len(data_to_export)
                ),
            )

        except Exception as e:
            logger.error(f"XML export failed: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to export XML:\n{error}").format(error=e),
            )

    @Slot()
    def compare_with_file(self):
        """Compare current translations with another file."""
        if not self.current_file:
            return

        file_path, _ = get_open_filename(
            self,
            "Compare with File",
            "",
            "Supported Files (*.strings *.dlstrings *.ilstrings *.STRINGS *.DLSTRINGS *.ILSTRINGS *.txt *.TXT);;All Files (*)",
        )
        if not file_path:
            return

        try:
            self.statusBar().showMessage(
                f"Loading comparison file {Path(file_path).name}..."
            )
            comp_data = {}

            ext = Path(file_path).suffix.lower()
            if ext in (".strings", ".dlstrings", ".ilstrings"):
                comp_file = BethesdaStringFile(file_path)
                target_lang = self.combo_target_lang.currentData()
                encoding, _ = EncodingConverter.get_encodings_for_locale(target_lang)
                for s in comp_file.strings:
                    try:
                        comp_data[s.id] = s.get_string(encoding)
                    except UnicodeDecodeError:
                        comp_data[s.id] = s.get_string("utf-8", errors="replace")
            elif ext == ".txt":
                with open(file_path, "r", encoding="utf-8") as f:
                    content_text = f.read()

                # Use the same regex as in import_from_txt
                line_numbered_pattern = re.compile(
                    r'^\d+\s+0x([0-9A-Fa-f]+)\s+"(.*?)"\s+"(.*?)"$', re.MULTILINE
                )
                tab_pattern = re.compile(r'0x([0-9A-Fa-f]+)\t"(.+?)"\t"(.+?)"')

                matches = list(line_numbered_pattern.finditer(content_text))
                if not matches:
                    matches = list(tab_pattern.finditer(content_text))

                for match in matches:
                    string_id = int(match.group(1), 16)
                    translated_text = self._unescape_string(match.group(3))
                    comp_data[string_id] = translated_text

            if not comp_data:
                QMessageBox.warning(
                    self,
                    self.tr("Comparison"),
                    self.tr("No string data found in comparison file."),
                )
                return

            self.table_model.set_comparison_data(comp_data)
            self.statusBar().showMessage(
                self.tr("Comparison loaded: {count} strings mapped.").format(
                    count=len(comp_data)
                )
            )
            QMessageBox.information(
                self,
                self.tr("Comparison Loaded"),
                self.tr(
                    "Comparison data from {filename} loaded.\n"
                    "Differences are highlighted in yellow."
                ).format(filename=Path(file_path).name),
            )

        except Exception as e:
            logger.error(f"Comparison failed: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.tr("Error"),
                self.tr("Failed to load comparison file:\n{error}").format(error=e),
            )

    # ─── Config management methods ──────────────────────────────────────

    @Slot()
    @Slot()
    def _load_translation_memory(self):
        """Load a TXT or TMX translation memory and pre-fill the table with known translations."""
        file_path, _ = get_open_filename(
            self,
            self.tr("Load Translation Memory"),
            "",
            self.tr("Translation Memory (*.txt *.tmx);;Text Files (*.txt);;TMX Files (*.tmx);;All Files (*)"),
        )
        if not file_path:
            return

        try:
            memory = TranslationMemory()
            fp = file_path.lower()
            if fp.endswith(".tmx"):
                src = self.settings.default_source_lang[:2].lower()
                tgt = self.settings.default_target_lang[:2].lower()
                loaded = memory.load_tmx(file_path, source_lang=src, target_lang=tgt)
            else:
                loaded = memory.load(file_path, use_original=True)

            if self.ollama_worker:
                self.ollama_worker.translation_memory = memory

            applied = 0
            if self.current_file is not None:
                applied = self.table_model.import_translations(memory.as_id_dict())

            self.statusBar().showMessage(
                self.tr(
                    "Translation memory loaded: {loaded} entries, {applied} applied to current file"
                ).format(loaded=loaded, applied=applied),
                8000,
            )
            logger.info(
                f"Translation memory loaded from {file_path}: {loaded} entries, {applied} applied"
            )
        except Exception as e:
            QMessageBox.warning(
                self,
                self.tr("Load Failed"),
                self.tr("Could not load translation memory:\n{error}").format(error=e),
            )

    @Slot()
    def _export_translation_memory(self):
        """Export the active translation memory to a TMX file."""
        memory: TranslationMemory | None = (
            self.ollama_worker.translation_memory if self.ollama_worker else None
        )
        # If no TM is loaded, build one from the current file's approved translations
        if memory is None or not memory._by_src:
            if not self.table_model._data:
                QMessageBox.information(
                    self,
                    self.tr("Export Translation Memory"),
                    self.tr("No translation memory loaded and no translations in the current file."),
                )
                return
            memory = TranslationMemory()
            for row in self.table_model._data:
                orig = row.get("original", "") or ""
                trans = row.get("translated", "") or ""
                if orig and trans:
                    memory._by_src[orig] = trans

        file_path, _ = get_save_filename(
            self,
            self.tr("Export Translation Memory as TMX"),
            "",
            self.tr("TMX Files (*.tmx);;All Files (*)"),
        )
        if not file_path:
            return
        if not file_path.lower().endswith(".tmx"):
            file_path += ".tmx"

        try:
            src = self.settings.default_source_lang[:2].lower()
            tgt = self.settings.default_target_lang[:2].lower()
            count = memory.export_tmx(file_path, source_lang=src, target_lang=tgt)
            self.statusBar().showMessage(
                self.tr("Exported {n} translation units to {path}").format(
                    n=count, path=file_path
                ),
                6000,
            )
        except Exception as e:
            QMessageBox.warning(
                self,
                self.tr("Export Failed"),
                self.tr("Could not export translation memory:\n{error}").format(error=e),
            )

    def _open_nexusmods_upload(self):
        from gui.nexusmods_upload_dialog import NexusModsUploadDialog
        dlg = NexusModsUploadDialog(
            self,
            settings=self.settings,
            initial_file=self.current_path,
        )
        dlg.exec()

    def _open_nexusmods_browser(self) -> None:
        from gui.nexusmods_browser_dialog import NexusModsBrowserDialog
        api_key = self.settings.nexusmods_api_key or ""
        dlg = NexusModsBrowserDialog(
            api_key=api_key,
            cache_dir=get_cache_dir(),
            parent=self,
        )
        dlg.tm_ready.connect(self._apply_nexus_tm)
        dlg.merge_requested.connect(self._apply_nexus_merge)
        dlg.open_file_requested.connect(lambda p: self._open_file_path(str(p)))
        dlg.exec()

    @Slot(object, str)
    def _apply_nexus_tm(self, tm: TranslationMemory, label: str) -> None:
        if self.ollama_worker:
            self.ollama_worker.translation_memory = tm
        applied = 0
        if self.current_file is not None:
            applied = self.table_model.import_translations(tm.as_id_dict())
        self.statusBar().showMessage(
            self.tr("NexusMods TM loaded ({label}): {n} entries, {applied} applied").format(
                label=label, n=len(tm), applied=applied
            ),
            8000,
        )

    @Slot(object)
    def _apply_nexus_merge(self, tm: TranslationMemory) -> None:
        applied = 0
        if self.current_file is not None:
            applied = self.table_model.import_translations(tm.as_id_dict())
        self.statusBar().showMessage(
            self.tr("NexusMods merge: {applied} translation(s) applied.").format(applied=applied),
            8000,
        )

    def _open_weblate_sync(self):
        from gui.weblate_sync_dialog import WelateSyncDialog
        dlg = WelateSyncDialog(
            table_model=self.table_model,
            settings=self.settings,
            parent=self,
        )
        dlg.exec()

    def _init_lore_rag(self) -> None:
        """Initialise the LoreDB and LoreRAGManager.  Called once at startup."""
        try:
            from bethesda_strings.lore_db import LoreDB
            from gui.lore_rag_manager import LoreRAGManager
            from gui.app_settings import get_config_dir
            db_path = get_config_dir() / "lore.sqlite"
            self._lore_db = LoreDB(db_path)
            self._lore_rag_manager = LoreRAGManager(
                db=self._lore_db,
                max_snippet_chars=self.settings.lore_rag_max_snippet_chars,
            )
            logger.info("Lore RAG initialized: %s", db_path)
        except Exception as exc:
            logger.warning("Lore RAG init failed: %s", exc)
            self._lore_db = None
            self._lore_rag_manager = None

    def _open_lore_rag_dialog(self) -> None:
        """Open the Lore RAG management dialog (modeless)."""
        if self._lore_db is None:
            self._init_lore_rag()
        if self._lore_db is None:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, self.tr("Lore RAG Unavailable"),
                self.tr("Failed to open the lore database. Check the log for details."),
            )
            return
        from gui.lore_rag_dialog import LoreRAGDialog
        dlg = LoreRAGDialog(db=self._lore_db, parent=self)
        dlg.exec()
        # Refresh manager settings after dialog in case data was added
        if self._lore_rag_manager is not None:
            self._lore_rag_manager.enabled = self.settings.enable_lore_rag

    def _open_font_checker(self) -> None:
        """Open the Font & Glyph Checker dialog."""
        from gui.font_checker_dialog import FontCheckerDialog
        rows = list(self.table_model._data)
        dlg = FontCheckerDialog(rows=rows, parent=self)
        dlg.jump_to_row.connect(self._jump_to_row)
        dlg.fix_applied.connect(self._apply_font_fixes)
        dlg.exec()

    def _apply_font_fixes(self, patches: list) -> None:
        """Apply auto-fix patches from the font checker to the table model."""
        if not patches:
            return
        self.table_model.set_translated_text_batch(patches)
        self.statusBar().showMessage(
            self.tr("Font auto-fix applied to {n} string(s)").format(n=len(patches)),
            5000,
        )

    def _open_dialogue_tree(self) -> None:
        dlg = self._dialogue_tree_dlg
        if dlg is None:
            path = self.current_path if isinstance(self.current_file, EspFile) else None
            encoding = self.table_model._encoding or "utf-8"
            dlg = DialogueTreeDialog(path=path, encoding=encoding, parent=self)
            dlg.jump_requested.connect(self._jump_to_esp_field)
            dlg.finished.connect(lambda: setattr(self, "_dialogue_tree_dlg", None))
            self._dialogue_tree_dlg = dlg
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()

    def _jump_to_esp_field(self, form_id: int, field_sig: str) -> None:
        """Navigate the string table to the row for (form_id, field_sig)."""
        if not isinstance(self.current_file, EspFile):
            QMessageBox.information(
                self, self.tr("Not in ESP Mode"),
                self.tr("Open the ESP/ESM file in the main table first."),
            )
            return
        target = f" {field_sig}"
        for i, row in enumerate(self.table_model._data):
            if row.get("id") == form_id and str(row.get("offset", "")).endswith(target):
                self._on_search_results([i])
                self.activateWindow()
                return
        QMessageBox.information(
            self, self.tr("Not Found"),
            self.tr("0x{fid:08X} / {fs} not found in the current file.").format(
                fid=form_id, fs=field_sig,
            ),
        )

    def _open_config_file(self):
        """Open the config file directory in file manager."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        config_path = get_config_path()
        if config_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(config_path.parent)))
        else:
            QMessageBox.information(
                self,
                self.tr("Config File"),
                self.tr(
                    "Config file does not exist yet. Settings will be saved on first use.\n\n"
                    "Config path: {path}"
                ).format(path=config_path),
            )

    @Slot()
    def _export_settings(self):
        """Export settings to a JSON file."""
        file_path, _ = get_save_filename(
            self,
            self.tr("Export Settings"),
            "bethesda-strings-config.json",
            self.tr("JSON Files (*.json *.JSON);;All Files (*)"),
        )
        if not file_path:
            return

        from gui.app_settings import export_settings_json

        if export_settings_json(Path(file_path), self.settings):
            QMessageBox.information(
                self,
                self.tr("Export Successful"),
                self.tr("Settings exported to:\n{path}").format(path=file_path),
            )
        else:
            QMessageBox.critical(
                self, self.tr("Export Failed"), self.tr("Could not export settings.")
            )

    @Slot()
    def _import_settings(self):
        """Import settings from a JSON file."""
        file_path, _ = get_open_filename(
            self,
            self.tr("Import Settings"),
            "",
            self.tr("JSON Files (*.json *.JSON);;All Files (*)"),
        )
        if not file_path:
            return

        from gui.app_settings import import_settings_json

        imported = import_settings_json(Path(file_path))
        if imported is None:
            QMessageBox.critical(
                self,
                self.tr("Import Failed"),
                self.tr("Could not import settings file."),
            )
            return

        # Validate imported settings
        errors = imported.validate()
        if errors:
            reply = QMessageBox.warning(
                self,
                self.tr("Validation Warnings"),
                self.tr("Imported settings have issues:\n")
                + "\n".join(f"• {e}" for e in errors)
                + self.tr("\n\nImport anyway?"),
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        # Apply imported settings
        self.settings = imported
        save_settings(self.settings)

        # Reinitialize worker with new settings
        self._disconnect_worker_signals()
        self._init_translation_worker()
        self._connect_worker_signals()

        # Update UI
        self.combo_source_lang.setCurrentIndex(
            self.combo_source_lang.findData(self.settings.default_source_lang)
        )
        self.combo_target_lang.setCurrentIndex(
            self.combo_target_lang.findData(self.settings.default_target_lang)
        )
        self.spin_quality.setValue(self.settings.quality_level)
        self._update_ui_state()

        # Apply theme if available
        if (
            self.theme_manager
            and self.settings.theme != self.theme_manager.current_theme
        ):
            from gui.app_settings import apply_theme

            apply_theme(QApplication.instance(), self.settings.theme)

        QMessageBox.information(
            self,
            self.tr("Import Successful"),
            self.tr(
                "Settings imported from:\n{path}\n\n"
                "Restart may be required for some changes to take effect."
            ).format(path=file_path),
        )


    # ── System theme auto-follow ──────────────────────────────────────────────

    @Slot()
    def _on_system_color_scheme_changed(self):
        """Re-apply theme when the OS switches light/dark mode.

        Only acts when the user has chosen "Auto (System)".
        """
        from gui.theme_manager import ThemeManager
        if self.settings.theme != ThemeManager.AUTO_THEME:
            return
        from gui.app_settings import apply_theme
        apply_theme(QApplication.instance(), ThemeManager.AUTO_THEME, self.theme_manager)
        logger.info("System color scheme changed — auto theme re-applied")
        self.table_model.invalidate_type_cache()

    # ── Translation auto-complete ─────────────────────────────────────────────

    def _build_completion_list(self) -> list[str]:
        """Build a deduplicated word list for the Translated cell completer.

        Sources (in priority order, longest strings first so the popup is useful):
          1. All approved translated strings from the current file
          2. Glossary target-language terms
          3. Protected terms (proper nouns that should survive unchanged)
        """
        seen: set[str] = set()
        words: list[str] = []

        def _add(text: str):
            t = text.strip()
            if t and t not in seen and len(t) >= 2:
                seen.add(t)
                words.append(t)

        # 1. Existing translated strings
        for row in self.table_model._data:
            translated = row.get("translated", "") or ""
            if translated:
                _add(translated)

        # 2. Glossary target terms
        if self._glossary_manager is not None:
            try:
                for entry in self._glossary_manager.entries():
                    if entry.target_term:
                        _add(entry.target_term)
            except Exception:
                pass

        # 3. Protected terms (proper nouns kept unchanged)
        if self.term_protector is not None:
            for term in self.term_protector.protected_terms:
                _add(term)

        # Sort: full strings first (most useful completions at top), then shorter
        words.sort(key=lambda w: (-len(w), w.lower()))
        return words

    # ── Term discovery ────────────────────────────────────────────────────────

    @Slot()
    def _discover_terms(self):
        """Scan loaded strings for candidate protected terms and let user approve them."""
        from gui.term_discoverer import discover_terms

        rows = list(self.table_model._data)
        if not rows:
            QMessageBox.information(self, self.tr("Discover Terms"), self.tr("No strings loaded."))
            return

        existing = set(self.term_protector.protected_terms.keys()) if self.term_protector else set()
        source_lang = self.combo_source_lang.currentData() or "ru"

        candidates = discover_terms(rows, existing_terms=existing, source_lang=source_lang)
        if not candidates:
            QMessageBox.information(
                self,
                self.tr("Discover Terms"),
                self.tr("No new candidate terms found in the loaded strings."),
            )
            return

        dlg = _TermDiscoveryDialog(candidates, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return

        approved = dlg.approved_terms()
        if not approved:
            return

        for term, category in approved:
            self.term_protector.add_term(term, category, case_sensitive=True)

        # Offer to save to the custom terms file
        custom_path = self.term_protector.custom_terms_file if hasattr(self.term_protector, "custom_terms_file") else None
        if custom_path:
            try:
                self.term_protector.export_terms(custom_path)
                logger.info("Saved %d new terms to %s", len(approved), custom_path)
            except Exception as e:
                logger.warning("Could not auto-save terms: %s", e)

        QMessageBox.information(
            self,
            self.tr("Terms Added"),
            self.tr("{n} term(s) added to the protection list.").format(n=len(approved)),
        )

    # ── Consistency check ─────────────────────────────────────────────────────

    @Slot()
    def _check_consistency(self):
        """Scan translated strings for the same source rendered differently."""
        from gui.consistency_checker import find_inconsistencies
        from gui.consistency_dialog import ConsistencyDialog

        rows = list(self.table_model._data)
        translated_count = sum(
            1 for r in rows if r.get("status") == "translated" and r.get("translated")
        )
        if translated_count == 0:
            QMessageBox.information(
                self,
                self.tr("Consistency Check"),
                self.tr("No translated strings found. Translate some strings first."),
            )
            return

        groups = find_inconsistencies(rows)
        if not groups:
            QMessageBox.information(
                self,
                self.tr("Consistency Check"),
                self.tr(
                    "No inconsistencies found — all translated strings are consistent."
                ),
            )
            return

        dlg = ConsistencyDialog(groups, parent=self)
        dlg.replacements_requested.connect(self._apply_consistency_replacements)
        dlg.exec()

    def _check_register(self) -> None:
        """Detect NPC speakers with mixed ти/ви register in their translated lines."""
        from gui.register_checker import check_register
        from gui.register_dialog import RegisterDialog

        rows = list(self.table_model._data)
        translated_count = sum(
            1 for r in rows
            if r.get("status") in ("translated", "approved") and r.get("translated")
        )
        if translated_count == 0:
            QMessageBox.information(
                self,
                self.tr("Register Check"),
                self.tr("No translated strings found. Translate some strings first."),
            )
            return

        groups = check_register(rows)
        dlg = RegisterDialog(groups, parent=self)
        dlg.jump_to_row.connect(self._jump_to_row)
        dlg.exec()

    def _check_gender_agreement(self) -> None:
        """Scan translated strings for Ukrainian adjective/noun gender mismatches."""
        from gui.gender_checker import check_gender_agreement
        from gui.gender_dialog import GenderDialog

        rows = list(self.table_model._data)
        translated_count = sum(
            1 for r in rows
            if r.get("status") in ("translated", "approved") and r.get("translated")
        )
        if translated_count == 0:
            QMessageBox.information(
                self,
                self.tr("Gender Agreement Check"),
                self.tr("No translated strings found. Translate some strings first."),
            )
            return

        mismatches = check_gender_agreement(rows)
        dlg = GenderDialog(mismatches, parent=self)
        dlg.jump_to_row.connect(self._jump_to_row)
        dlg.exec()

    # ── Translation Sessions ──────────────────────────────────────────────────

    def _populate_recent_sessions(self) -> None:
        """Rebuild the Recent Sessions submenu from the session store."""
        menu = self._sessions_recent_menu
        menu.clear()
        try:
            sessions = self._session_store.list_sessions()
        except Exception:
            return
        recent = sessions[:8]
        if not recent:
            no_act = menu.addAction(self.tr("(no sessions yet)"))
            no_act.setEnabled(False)
            return
        for s in recent:
            name = s.name
            act = menu.addAction(name)
            act.setToolTip(s.file_path)
            act.triggered.connect(
                lambda checked=False, n=name: self._session_resume_by_name(n)
            )

    def _session_new(self) -> None:
        from gui.session_dialog import NewSessionDialog
        from gui.session_manager import WorkSession
        from datetime import datetime

        existing = [s.name for s in self._session_store.list_sessions()]
        suggested = (
            Path(self.current_path).stem if self.current_path else ""
        )
        dlg = NewSessionDialog(existing, suggested_name=suggested, parent=self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return

        now = datetime.now().isoformat(timespec="seconds")
        ft = "esp" if hasattr(self.current_file, "strings") else "strings"
        session = WorkSession(
            name=dlg.session_name,
            created=now,
            modified=now,
            file_path=str(self.current_path) if self.current_path else "",
            file_type=ft,
            current_row=self.table_view.currentIndex().row(),
            scroll_value=self.table_view.verticalScrollBar().value(),
            translated_in_session=[],
            note=dlg.session_note,
        )
        # Capture current search state if an advanced search dialog is open
        # (search state is populated by _session_capture_search, called on save)
        self._current_session = session
        self._session_baseline = {
            row["id"] for row in self.table_model._data
            if row.get("status") in ("translated", "approved")
        }
        self._session_store.save(session)
        self._session_save_action.setEnabled(True)
        self._session_save_as_action.setEnabled(True)
        self._update_session_title()
        self._populate_recent_sessions()
        self.statusBar().showMessage(
            self.tr("Session “{name}” started.").format(name=session.name), 5000
        )

    def _session_save(self) -> None:
        if self._current_session is None:
            self._session_new()
            return
        self._session_capture_state(self._current_session)
        self._session_store.save(self._current_session)
        self._populate_recent_sessions()
        self.statusBar().showMessage(
            self.tr("Session saved: {name}").format(
                name=self._current_session.name), 3000
        )

    def _session_save_as(self) -> None:
        from gui.session_dialog import NewSessionDialog
        from gui.session_manager import WorkSession
        from datetime import datetime

        existing = [s.name for s in self._session_store.list_sessions()]
        current_name = (
            self._current_session.name if self._current_session else ""
        )
        dlg = NewSessionDialog(
            [n for n in existing if n != current_name],
            suggested_name=current_name + " (copy)" if current_name else "",
            parent=self,
        )
        if dlg.exec() != dlg.DialogCode.Accepted:
            return

        now = datetime.now().isoformat(timespec="seconds")
        ft = "esp" if hasattr(self.current_file, "strings") else "strings"
        old = self._current_session
        session = WorkSession(
            name=dlg.session_name,
            created=now,
            modified=now,
            file_path=str(self.current_path) if self.current_path else "",
            file_type=ft,
            current_row=self.table_view.currentIndex().row(),
            scroll_value=self.table_view.verticalScrollBar().value(),
            translated_in_session=list(old.translated_in_session) if old else [],
            note=dlg.session_note,
        )
        self._current_session = session
        self._session_store.save(session)
        self._session_save_action.setEnabled(True)
        self._session_save_as_action.setEnabled(True)
        self._update_session_title()
        self._populate_recent_sessions()

    def _session_resume_by_name(self, name: str) -> None:
        session = self._session_store.load(name)
        if session is None:
            QMessageBox.warning(
                self,
                self.tr("Session Not Found"),
                self.tr("Session “{name}” could not be loaded.").format(name=name),
            )
            return
        self._session_activate(session)

    def _session_activate(self, session) -> None:
        """Restore a session's context (open file if needed, scroll, search)."""
        from pathlib import Path as _Path
        target = _Path(session.file_path) if session.file_path else None

        if target and target.exists() and str(target) != str(self.current_path):
            # Offer to open the session's file
            ans = QMessageBox.question(
                self,
                self.tr("Open Session File?"),
                self.tr(
                    "This session is for:\n{path}\n\n"
                    "Open that file now?"
                ).format(path=session.file_path),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans == QMessageBox.StandardButton.Yes:
                self._open_file_path(session.file_path)
        elif target and not target.exists():
            QMessageBox.warning(
                self,
                self.tr("File Not Found"),
                self.tr(
                    "The session file no longer exists:\n{path}\n\n"
                    "You can still use the session context, but the file "
                    "will need to be opened manually."
                ).format(path=session.file_path),
            )

        self._current_session = session
        self._session_baseline = {
            row["id"] for row in self.table_model._data
            if row.get("status") in ("translated", "approved")
        } - set(session.translated_in_session)

        # Restore search filter
        if not session.search.is_empty():
            self._session_rerun_search(session.search)

        # Restore scroll + cursor (after search so row is visible)
        QTimer.singleShot(100, lambda: self._session_restore_position(session))

        self._session_save_action.setEnabled(True)
        self._session_save_as_action.setEnabled(True)
        self._update_session_title()
        self.statusBar().showMessage(
            self.tr("Session “{name}” resumed — {n} strings translated in session.").format(
                name=session.name, n=session.translated_count
            ),
            6000,
        )

    def _session_restore_position(self, session) -> None:
        self.table_view.verticalScrollBar().setValue(session.scroll_value)
        if 0 <= session.current_row < len(self.table_model._data):
            self._jump_to_row(session.current_row)

    def _session_rerun_search(self, search) -> None:
        """Re-run the saved search filter without opening the dialog."""
        from gui.advanced_search_dialog import AdvancedSearchDialog
        dlg = AdvancedSearchDialog(self)
        dlg.txt_search.setText(search.query)
        dlg.txt_id.setText(search.id_filter)
        for i in range(dlg.combo_column.count()):
            if dlg.combo_column.itemData(i) == search.column:
                dlg.combo_column.setCurrentIndex(i)
                break
        for i in range(dlg.combo_status.count()):
            if dlg.combo_status.itemData(i) == search.status:
                dlg.combo_status.setCurrentIndex(i)
                break
        dlg.chk_regex.setChecked(search.use_regex)
        dlg.chk_case.setChecked(search.case_sensitive)
        dlg.chk_whole_word.setChecked(search.whole_word)
        dlg.search_results.connect(self._on_search_results)
        dlg._do_search()

    def _session_capture_state(self, session) -> None:
        """Write current UI state into *session* (in-place)."""
        session.file_path = str(self.current_path) if self.current_path else ""
        session.file_type = (
            "esp" if hasattr(self.current_file, "strings") else "strings"
        )
        session.current_row  = self.table_view.currentIndex().row()
        session.scroll_value = self.table_view.verticalScrollBar().value()

    def _session_track_datachanged(self, top_left, bottom_right, roles) -> None:
        """Track newly-translated strings into the active session."""
        if self._current_session is None:
            return
        from PySide6.QtCore import Qt as _Qt
        if _Qt.ItemDataRole.DisplayRole not in roles:
            return
        tracked_ids = set(self._current_session.translated_in_session)
        changed = False
        for row in range(top_left.row(), bottom_right.row() + 1):
            if row >= len(self.table_model._data):
                break
            row_data = self.table_model._data[row]
            if row_data.get("status") in ("translated", "approved"):
                sid = row_data.get("id", 0)
                if sid and sid not in self._session_baseline and sid not in tracked_ids:
                    tracked_ids.add(sid)
                    changed = True
        if changed:
            self._current_session.translated_in_session = list(tracked_ids)

    def _open_session_manager(self) -> None:
        from gui.session_dialog import SessionManagerDialog

        sessions = self._session_store.list_sessions()
        current_name = (
            self._current_session.name if self._current_session else None
        )
        dlg = SessionManagerDialog(sessions, current_name, parent=self)
        dlg.resume_requested.connect(self._session_resume_by_name)
        # After dialog closes, sync any renames/deletes to the store
        if dlg.exec():
            # Persist any renames that happened inside the dialog
            for s in dlg._sessions:
                if s.name != self._session_store.load(s.name):
                    self._session_store.save(s)
            # Re-check deletions: any session missing from dlg._sessions was deleted
            current_names = {s.name for s in dlg._sessions}
            for s in sessions:
                if s.name not in current_names:
                    self._session_store.delete(s.name)
            self._populate_recent_sessions()

    def _update_session_title(self) -> None:
        """Append session name to the window title."""
        base = self.tr("Bethesda Strings AI Translator")
        if self.current_path:
            base = f"{Path(self.current_path).name} — {base}"
        if self._current_session:
            base = f"{base}  [{self._current_session.name}]"
        self.setWindowTitle(base)

    def _apply_consistency_replacements(self, replacements: list):
        """Apply a list of (row_indices, canonical_text) from the consistency dialog."""
        updates = []
        for row_indices, canonical in replacements:
            for row_idx in row_indices:
                updates.append((row_idx, canonical))

        if updates:
            self.table_model.set_translated_text_batch(updates)
            self.setWindowModified(True)
            logger.info(
                "Consistency replacements applied: %d rows updated", len(updates)
            )

    # ── Version comparison ────────────────────────────────────────────────────

    @Slot()
    def _compare_game_versions(self) -> None:
        """Open the version-comparison setup dialog, then show the diff."""
        from gui.version_compare_dialog import (
            VersionCompareSetupDialog,
            VersionCompareDialog,
        )
        from bethesda_strings.version_diff import (
            compute_version_diff,
            load_strings_file,
        )

        # Pre-fill "new file" path with the currently loaded file if applicable
        current_path = ""
        if self.current_file and hasattr(self.current_file, "filepath"):
            current_path = str(self.current_file.filepath)

        setup = VersionCompareSetupDialog(
            initial_new_path=current_path, parent=self
        )
        if setup.exec() != QDialog.Accepted:
            return

        enc = setup.encoding
        try:
            self.statusBar().showMessage(self.tr("Loading files for version comparison…"))
            QApplication.processEvents()

            old_strings = load_strings_file(setup.old_path, enc)
            new_strings = load_strings_file(setup.new_path, enc)
            old_translation: Optional[dict] = None
            if setup.translation_path:
                old_translation = load_strings_file(setup.translation_path, enc)
        except Exception as exc:
            logger.error("Version compare load failed: %s", exc, exc_info=True)
            QMessageBox.critical(
                self, self.tr("Load Error"),
                self.tr("Failed to load one or more files:\n{error}").format(error=exc),
            )
            self.statusBar().clearMessage()
            return

        entries = compute_version_diff(old_strings, new_strings, old_translation)

        old_name = Path(setup.old_path).name
        new_name = Path(setup.new_path).name
        trans_name = (
            Path(setup.translation_path).name if setup.translation_path else "—"
        )

        dlg = VersionCompareDialog(
            entries=entries,
            old_label=old_name,
            new_label=new_name,
            translation_label=trans_name,
            parent=self,
        )
        dlg.migrate_requested.connect(self._apply_version_migration)
        self.statusBar().clearMessage()
        dlg.exec()

    def _apply_version_migration(self, migration: dict) -> None:
        """Apply migrated translations from version comparison to the current model."""
        if not migration or not self.table_model:
            return

        # Only update rows that are pending (don't overwrite human work)
        id_to_row = {
            row["id"]: i
            for i, row in enumerate(self.table_model._data)
        }
        updates = []
        for string_id, translation in migration.items():
            row_idx = id_to_row.get(string_id)
            if row_idx is None:
                continue
            row = self.table_model._data[row_idx]
            if row.get("status") == "pending" or not row.get("translated"):
                updates.append((row_idx, translation))

        if updates:
            self.table_model.set_translated_text_batch(updates)
            self.setWindowModified(True)
            self.statusBar().showMessage(
                self.tr("Migrated {n} translation(s) from previous version.").format(
                    n=len(updates)
                ),
                5000,
            )
            logger.info("Version migration applied: %d rows updated", len(updates))

    @Slot()
    def _batch_compare_folders(self) -> None:
        """Open the batch folder-comparison dialog."""
        from gui.version_compare_dialog import VersionBatchDialog
        dlg = VersionBatchDialog(parent=self)
        dlg.exec()

    # ── Help dialogs ──────────────────────────────────────────────────────────

    @Slot()
    def _show_shortcuts_dialog(self):
        """Show keyboard shortcuts reference table."""
        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr("Keyboard Shortcuts"))
        dlg.setMinimumSize(560, 480)
        layout = QVBoxLayout(dlg)

        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels([self.tr("Action"), self.tr("Shortcut"), self.tr("Category")])
        from PySide6.QtWidgets import QHeaderView
        hdr = table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)

        rows = []
        for entry in self.keyboard_manager.all_actions():
            shortcut = self.keyboard_manager.effective_shortcut(entry.id) or self.tr("—")
            rows.append((entry.name, shortcut, entry.category))
        rows.sort(key=lambda r: (r[2], r[0]))

        table.setRowCount(len(rows))
        for i, (name, shortcut, category) in enumerate(rows):
            table.setItem(i, 0, QTableWidgetItem(name))
            table.setItem(i, 1, QTableWidgetItem(shortcut))
            table.setItem(i, 2, QTableWidgetItem(category))

        layout.addWidget(table)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        dlg.exec()

    @Slot()
    def _show_about_dialog(self):
        """Show About dialog."""
        QMessageBox.about(
            self,
            self.tr("About Bethesda Strings AI Translator"),
            self.tr(
                "<b>Bethesda Strings AI Translator</b><br>"
                "AI-assisted localization tool for Starfield and other Bethesda games.<br><br>"
                "Supports .strings / .dlstrings / .ilstrings and ESP/ESM files.<br>"
                "Translation via local Ollama models.<br><br>"
                "<b>Keyboard Shortcuts:</b> F1<br>"
                "<b>What's This?:</b> Shift+F1, then click any widget"
            ),
        )

    def _show_first_run_tips(self):
        """Show a one-time tip dialog for first-time users."""
        if self.settings.tips_shown:
            return
        self.settings.tips_shown = True
        save_settings(self.settings)

        dlg = QDialog(self)
        dlg.setWindowTitle(self.tr("Welcome to Bethesda Strings AI Translator"))
        dlg.setMinimumWidth(500)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(12)

        tips = [
            ("Ctrl+O", self.tr("Open a .strings, .dlstrings, .ilstrings or ESP/ESM file")),
            ("Ctrl+A", self.tr("Translate all untranslated strings with AI")),
            ("F7",     self.tr("Jump to the next untranslated string")),
            ("Ctrl+Enter", self.tr("Approve the selected translation")),
            ("Ctrl+K", self.tr("Open the command palette to find any action")),
            ("F1",     self.tr("Show all keyboard shortcuts")),
            ("Shift+F1", self.tr("Enter What's This? mode — click any widget for help")),
        ]

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setSpacing(6)

        for key, desc in tips:
            row = QHBoxLayout()
            key_label = QLabel(f"<code>{key}</code>")
            key_label.setFixedWidth(120)
            key_label.setTextFormat(Qt.TextFormat.RichText)
            desc_label = QLabel(desc)
            desc_label.setWordWrap(True)
            row.addWidget(key_label)
            row.addWidget(desc_label, 1)
            inner_layout.addLayout(row)

        inner_layout.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(QLabel(f"<b>{self.tr('Quick-start tips:')}</b>"))
        layout.addWidget(scroll)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btns.accepted.connect(dlg.accept)
        layout.addWidget(btns)

        dlg.exec()

    def _setup_whats_this(self):
        """Attach What's This? descriptions to key widgets."""
        widgets_help = [
            (self.combo_source_lang, self.tr(
                "Source language of the text to translate.\n"
                "Set to Russian for Starfield's shipped strings."
            )),
            (self.combo_target_lang, self.tr(
                "Target language for AI translation output.\n"
                "Typically Ukrainian for this project."
            )),
            (self.spin_quality, self.tr(
                "Minimum quality score (1–10). Strings already rated at or above this\n"
                "threshold are skipped when running Translate All."
            )),
            (self.lbl_file_info, self.tr(
                "Currently loaded file path and format.\n"
                "Drag-and-drop a file here to open it."
            )),
        ]
        for widget, text in widgets_help:
            widget.setWhatsThis(text)


class _TermDiscoveryDialog(QDialog):
    """Review and approve candidate protected terms found by the term discoverer."""

    _CATEGORIES = ["game_term", "location", "faction", "character", "item", "skill", "custom"]

    def __init__(self, candidates, parent=None):
        super().__init__(parent)
        from gui.term_discoverer import CandidateTerm
        self._candidates: list[CandidateTerm] = candidates
        self.setWindowTitle(self.tr("Discovered Terms — Review & Approve"))
        self.setMinimumSize(700, 500)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        info = QLabel(self.tr(
            "Candidate terms extracted from the loaded strings.\n"
            "Check the ones to add to the protection list. Edit category as needed.\n"
            "<b>Score</b> = cross-match count × 3 + frequency (higher = stronger signal)."
        ))
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setWordWrap(True)
        layout.addWidget(info)

        # Filter bar
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel(self.tr("Filter:")))
        self._filter_edit = filter_edit = QLineEdit()
        filter_edit.setPlaceholderText(self.tr("type to filter…"))
        filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(filter_edit, 1)

        sel_all_btn = QPushButton(self.tr("Select All"))
        sel_all_btn.clicked.connect(self._select_all)
        sel_none_btn = QPushButton(self.tr("Select None"))
        sel_none_btn.clicked.connect(self._select_none)
        filter_row.addWidget(sel_all_btn)
        filter_row.addWidget(sel_none_btn)
        layout.addLayout(filter_row)

        # Table
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels([
            self.tr("✓"), self.tr("Term"),
            self.tr("Category"), self.tr("Freq"), self.tr("Score"),
        ])
        from PySide6.QtWidgets import QHeaderView
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table)

        self._populate_table(self._candidates)

        count_label = QLabel(self.tr(f"{len(self._candidates)} candidates found"))
        self._count_label = count_label
        layout.addWidget(count_label)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _populate_table(self, candidates):
        from PySide6.QtWidgets import QCheckBox, QComboBox as _QComboBox
        self._table.setRowCount(len(candidates))
        for i, cand in enumerate(candidates):
            # Checkbox column
            chk = QCheckBox()
            chk.setChecked(cand.cross_matches > 0)  # pre-select cross-matched ones
            cell_widget = QWidget()
            cell_layout = QHBoxLayout(cell_widget)
            cell_layout.addWidget(chk)
            cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            self._table.setCellWidget(i, 0, cell_widget)

            # Term (editable via double-click on the term column)
            term_item = QTableWidgetItem(cand.term)
            term_item.setFlags(term_item.flags() | Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(i, 1, term_item)

            # Category combo
            combo = _QComboBox()
            combo.addItems(self._CATEGORIES)
            combo.setCurrentText(cand.category)
            self._table.setCellWidget(i, 2, combo)

            # Frequency
            freq_item = QTableWidgetItem(str(cand.frequency))
            freq_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(i, 3, freq_item)

            # Score
            score_item = QTableWidgetItem(f"{cand.score:.0f}")
            score_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(i, 4, score_item)

        self._table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.AnyKeyPressed
        )

    def _apply_filter(self, text: str):
        text = text.lower()
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 1)
            hidden = bool(text and item and text not in item.text().lower())
            self._table.setRowHidden(row, hidden)

    def _select_all(self):
        for row in range(self._table.rowCount()):
            if not self._table.isRowHidden(row):
                w = self._table.cellWidget(row, 0)
                if w:
                    chk = w.findChild(QCheckBox)
                    if chk:
                        chk.setChecked(True)

    def _select_none(self):
        for row in range(self._table.rowCount()):
            w = self._table.cellWidget(row, 0)
            if w:
                chk = w.findChild(QCheckBox)
                if chk:
                    chk.setChecked(False)

    def approved_terms(self) -> list[tuple[str, str]]:
        """Return list of (term, category) for checked rows."""
        from PySide6.QtWidgets import QComboBox as _QComboBox
        result = []
        for row in range(self._table.rowCount()):
            w = self._table.cellWidget(row, 0)
            if not w:
                continue
            chk = w.findChild(QCheckBox)
            if chk and chk.isChecked():
                term_item = self._table.item(row, 1)
                cat_widget = self._table.cellWidget(row, 2)
                term = term_item.text().strip() if term_item else ""
                category = cat_widget.currentText() if isinstance(cat_widget, _QComboBox) else "custom"
                if term:
                    result.append((term, category))
        return result


class ExportModeDialog(QDialog):
    """Dialog to select export mode."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Export Mode"))
        self.setModal(True)
        self.setMinimumWidth(300)
        self._setup_ui()

    def _setup_ui(self):
        """Set up the UI."""
        layout = QVBoxLayout(self)

        # Label
        label = QLabel(self.tr("Select export mode:"))
        layout.addWidget(label)

        # Radio buttons
        self.radio_all = QRadioButton(self.tr("All strings"))
        self.radio_all.setChecked(True)
        layout.addWidget(self.radio_all)

        self.radio_translated = QRadioButton(self.tr("Translated only"))
        layout.addWidget(self.radio_translated)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        btn_ok = QPushButton(self.tr("OK"))
        btn_ok.clicked.connect(self.accept)
        button_layout.addWidget(btn_ok)

        btn_cancel = QPushButton(self.tr("Cancel"))
        btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(btn_cancel)

        layout.addLayout(button_layout)

    def get_selected_mode(self) -> str:
        """Get the selected export mode."""
        if self.radio_all.isChecked():
            return "All"
        else:
            return "Translated only"
