"""
Settings/Preferences dialog with term protection settings
"""
from pathlib import Path
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QComboBox, QSpinBox, QCheckBox,
    QPushButton, QDialogButtonBox, QGroupBox, QLabel,
    QMessageBox, QApplication, QSlider, QWidget, QScrollArea, QFrame,
    QFileDialog,
)
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QKeySequenceEdit
from typing import TYPE_CHECKING, Optional
from gui.app_settings import (
    AppSettings,
    get_config_dir, get_config_dir_override, set_config_dir_override,
    get_cache_dir, get_cache_dir_override, set_cache_dir_override,
)
from gui.file_dialog_helper import get_open_filename

if TYPE_CHECKING:
    from gui.term_protector import TermProtector


class SettingsDialog(QDialog):
    """Dialog for configuring Ollama and term protection settings."""
    SUPPORTED_LANGUAGES = [
        'English', 'Russian', 'Ukrainian', 'Korean',
    ]

    # Default Ollama model suggestions shown before the user refreshes from the server
    _DEFAULT_OLLAMA_MODELS = [
        'translategemma3-st',
        'translategemma3-st-2',
        'gemma4-opus48-st',
    ]


    def __init__(self, settings: AppSettings, parent=None, term_protector: Optional["TermProtector"] = None,
                 theme_manager=None, translation_cache=None, keyboard_manager=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Preferences"))
        self.setMinimumWidth(600)
        self.setMinimumHeight(480)
        self._settings = settings  # AppSettings instance (mutable, modified in-place)
        self._term_protector = term_protector
        self._theme_manager = theme_manager
        self._translation_cache = translation_cache
        self._keyboard_manager = keyboard_manager
        self._shortcut_editors: dict = {}  # action_id → QKeySequenceEdit
        self._original_theme: str = settings.theme  # restore on cancel
        self._dirty: bool = False
        self._setup_ui()
        self._fit_to_screen()
        self._setup_dirty_tracking()

    def _fit_to_screen(self):
        """Size dialog to screen and keep it centered/usable on 1080p."""
        screen = self.screen() or QApplication.primaryScreen()
        if not screen:
            self.resize(900, 760)
            return

        avail = screen.availableGeometry()
        target_w = min(1000, max(600, int(avail.width() * 0.7)))
        target_h = min(900, max(480, int(avail.height() * 0.85)))
        self.resize(target_w, target_h)

    def _setup_ui(self):
        root_layout = QVBoxLayout(self)
        self.setObjectName("SettingsDialog")
        scroll = QScrollArea(self)
        scroll.setObjectName("SettingsScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setStyleSheet(
            """
            QScrollArea#SettingsScrollArea {
                background: transparent;
                border: none;
            }
            QScrollArea#SettingsScrollArea > QWidget > QWidget {
                background: transparent;
            }
            """
        )

        content = QWidget(scroll)
        content.setObjectName("SettingsDialogContent")
        content.setAttribute(Qt.WA_StyledBackground, True)
        content.setStyleSheet("QWidget#SettingsDialogContent { background: transparent; }")
        layout = QVBoxLayout(content)

        # Ollama Configuration
        self.ollama_group = QGroupBox(self.tr("Ollama AI Settings"))
        ollama_layout = QFormLayout()

        self.ollama_url = QLineEdit(self._settings.ollama_url)
        self.ollama_url.setPlaceholderText("http://localhost:11434")
        ollama_layout.addRow(self.tr("API URL:"), self.ollama_url)

        self.ollama_model = QComboBox()
        self.ollama_model.setEditable(True)
        self.ollama_model.addItems(self._default_ollama_model_list())
        current_model = self._settings.ollama_model
        idx = self.ollama_model.findText(current_model)
        if idx >= 0:
            self.ollama_model.setCurrentIndex(idx)
        else:
            self.ollama_model.setCurrentText(current_model)
        self.ollama_model.setToolTip(
            self.tr(
                "Type any Ollama model name or pick from the list.\n"
                "Click 'Refresh' to load all installed models from the server."
            )
        )
        model_row = QHBoxLayout()
        model_row.addWidget(self.ollama_model, stretch=1)
        self.btn_refresh_models = QPushButton(self.tr("Refresh"))
        self.btn_refresh_models.setToolTip(self.tr("Fetch installed models from the Ollama server"))
        self.btn_refresh_models.clicked.connect(self._refresh_ollama_models)
        model_row.addWidget(self.btn_refresh_models)
        ollama_layout.addRow(self.tr("Model:"), model_row)

        self.spin_num_predict = QSpinBox()
        self.spin_num_predict.setRange(64, 8192)
        self.spin_num_predict.setValue(self._settings.ollama_num_predict)
        self.spin_num_predict.setToolTip(self.tr("Maximum number of tokens to generate (num_predict)"))
        ollama_layout.addRow(self.tr("Token Limit:"), self.spin_num_predict)

        self.spin_num_ctx = QSpinBox()
        self.spin_num_ctx.setRange(512, 32768)
        self.spin_num_ctx.setValue(self._settings.ollama_num_ctx)
        self.spin_num_ctx.setToolTip(self.tr("Context window size in tokens (num_ctx). Increasing this uses more VRAM."))
        ollama_layout.addRow(self.tr("Context Limit:"), self.spin_num_ctx)

        self.spin_num_thread = QSpinBox()
        self.spin_num_thread.setRange(0, 64)
        self.spin_num_thread.setValue(self._settings.ollama_num_thread)
        self.spin_num_thread.setSpecialValueText(self.tr("Auto"))
        self.spin_num_thread.setToolTip(
            self.tr("CPU threads passed to Ollama per request (0 = auto). "
                    "Tune this to match your CPU core count for best performance.")
        )
        ollama_layout.addRow(self.tr("Ollama CPU threads:"), self.spin_num_thread)

        self.ollama_group.setLayout(ollama_layout)
        layout.addWidget(self.ollama_group)

        # Test connection button
        test_group = QGroupBox(self.tr("Connection Test"))
        test_layout = QHBoxLayout()
        self.btn_test = QPushButton(self.tr("Test Connection"))
        self.btn_test.clicked.connect(self._test_connection)
        self.lbl_connection = QLabel(self.tr("● Not tested"))
        self.lbl_connection.setStyleSheet("color: gray;")
        test_layout.addWidget(self.btn_test)
        test_layout.addWidget(self.lbl_connection)
        test_layout.addStretch()
        test_group.setLayout(test_layout)
        layout.addWidget(test_group)

        # Term Protection Settings
        protection_group = QGroupBox(self.tr("Game Term Protection"))
        protection_layout = QVBoxLayout()

        self.chk_enable_protection = QCheckBox(self.tr("Enable automatic term protection"))
        self.chk_enable_protection.setChecked(self._settings.enable_term_protection)
        self.chk_enable_protection.setToolTip(self.tr("Protect game-specific terms, IDs, and names from translation"))
        protection_layout.addWidget(self.chk_enable_protection)

        self.chk_protect_english_text = QCheckBox(self.tr("Protect English text from translation"))
        self.chk_protect_english_text.setChecked(self._settings.protect_english_text)
        self.chk_protect_english_text.setToolTip(
            self.tr("When translating from non-English source (e.g. Russian) to Ukrainian, keep English words/phrases unchanged.\n"
                    "Useful for preserving names, titles, and terminology that should remain in English.\n"
                    "Note: This is automatically disabled when English is the source language.")
        )
        protection_layout.addWidget(self.chk_protect_english_text)

        self.chk_protect_named_entities = QCheckBox(
            self.tr("Protect proper nouns and lore terms (faction/company/ship/character names, resources, UI terms, loaded term file)")
        )
        self.chk_protect_named_entities.setChecked(
            getattr(self._settings, "protect_named_entities", False)
        )
        self.chk_protect_named_entities.setToolTip(
            self.tr("When enabled, faction names (Freestar Collective, UC…), company names, ship names, character names,\n"
                    "creature/resource names, UI abbreviations (HUD, GPS…), and terms loaded from the custom terms file\n"
                    "are replaced with placeholder tokens so the AI cannot modify them.\n\n"
                    "When disabled (default), the AI is free to translate these names — useful when you want\n"
                    "localised faction/location names (e.g. «Об'єднані колонії» instead of «United Colonies»).")
        )
        protection_layout.addWidget(self.chk_protect_named_entities)

        # Protected terms file
        terms_file_layout = QHBoxLayout()
        self.lbl_terms_file = QLabel(self.tr("Custom terms file:"))
        self.terms_file_path = QLineEdit(self._settings.protected_terms_file)
        self.terms_file_path.setPlaceholderText(self.tr("Path to custom protected terms file"))
        self.terms_file_path.setMinimumWidth(300)
        terms_file_layout.addWidget(self.lbl_terms_file)
        terms_file_layout.addWidget(self.terms_file_path)

        self.btn_browse_terms = QPushButton(self.tr("Browse..."))
        self.btn_browse_terms.clicked.connect(self._browse_terms_file)
        terms_file_layout.addWidget(self.btn_browse_terms)

        protection_layout.addLayout(terms_file_layout)

        # View/Edit protected terms
        self.btn_view_terms = QPushButton(self.tr("View/Edit Protected Terms"))
        self.btn_view_terms.clicked.connect(self._view_protected_terms)
        protection_layout.addWidget(self.btn_view_terms)

        # Info
        stats_label = QLabel(self.tr("ℹ️ Format tags, game IDs, XML/alias tokens, and user-added custom terms are always protected regardless of the setting above."))
        stats_label.setWordWrap(True)
        stats_label.setStyleSheet("color: palette(mid); font-style: italic;")
        protection_layout.addWidget(stats_label)

        protection_group.setLayout(protection_layout)
        layout.addWidget(protection_group)

        # Appearance / Theme
        if self._theme_manager:
            theme_group = QGroupBox(self.tr("Appearance"))
            theme_layout = QFormLayout()

            self.combo_theme = QComboBox()
            themes = self._theme_manager.available_themes
            self.combo_theme.addItems(themes)
            # Select current theme
            idx = self.combo_theme.findText(self._settings.theme)
            if idx >= 0:
                self.combo_theme.setCurrentIndex(idx)
            self.combo_theme.setToolTip(self.tr("Choose a built-in or custom theme"))
            theme_layout.addRow(self.tr("Theme:"), self.combo_theme)

            # Theme description
            self.lbl_theme_desc = QLabel(self._theme_manager.get_theme_description(self._settings.theme))
            self.lbl_theme_desc.setWordWrap(True)
            self.lbl_theme_desc.setStyleSheet("color: palette(mid); font-style: italic; font-size: 11px;")
            theme_layout.addRow(self.lbl_theme_desc)

            self.combo_theme.currentTextChanged.connect(self._on_theme_changed)

            # UI Language
            self.combo_ui_lang = QComboBox()
            # (locale_code, display_label, native_name, is_complete)
            _UI_LANGUAGES = [
                ("en",    "English",    "English",       True),
                ("uk_UA", "Ukrainian",  "Українська",    True),
                ("de_DE", "German",     "Deutsch",       False),
                ("es_ES", "Spanish",    "Español",       False),
                ("fr_FR", "French",     "Français",      False),
                ("pl_PL", "Polish",     "Polski",        False),
                ("cs_CZ", "Czech",      "Čeština",       False),
            ]
            for code, en_name, native, complete in _UI_LANGUAGES:
                label = f"{native}  ({en_name})" if not complete else f"{native}  ({en_name}) ✓"
                self.combo_ui_lang.addItem(label, code)
            idx = self.combo_ui_lang.findData(self._settings.ui_language)
            self.combo_ui_lang.setCurrentIndex(max(0, idx))
            self._orig_ui_lang = self._settings.ui_language
            self.combo_ui_lang.currentIndexChanged.connect(self._on_lang_changed)
            lang_note = QLabel(self.tr("✓ = complete translation  ·  others are community work-in-progress"))
            lang_note.setStyleSheet("color: palette(mid); font-style: italic; font-size: 11px;")
            theme_layout.addRow(self.tr("Interface Language:"), self.combo_ui_lang)
            theme_layout.addRow(lang_note)

            # Font size
            self.spin_font_size = QSpinBox()
            self.spin_font_size.setRange(0, 24)
            self.spin_font_size.setSpecialValueText(self.tr("OS default"))
            self.spin_font_size.setSuffix(self.tr(" pt"))
            self.spin_font_size.setValue(self._settings.font_size)
            self.spin_font_size.setToolTip(
                self.tr("Set 0 to follow the OS font size. Changes apply after restart.")
            )
            self.spin_font_size.setAccessibleName(self.tr("Interface font size"))
            theme_layout.addRow(self.tr("Font Size:"), self.spin_font_size)

            # Color-blind mode
            self.chk_color_blind = QCheckBox(self.tr("Color-blind friendly status colors"))
            self.chk_color_blind.setChecked(self._settings.color_blind_mode)
            self.chk_color_blind.setToolTip(
                self.tr(
                    "Replace green/red status indicators with blue/orange.\n"
                    "Improves visibility for deuteranopia (red-green color blindness).\n"
                    "Status symbols (✓ ⚠ ✗) always convey state regardless of color."
                )
            )
            self.chk_color_blind.setAccessibleName(self.tr("Color-blind mode"))
            theme_layout.addRow(self.chk_color_blind)

            # Theme action buttons
            theme_btn_layout = QHBoxLayout()
            self.btn_manage_themes = QPushButton(self.tr("Manage Themes..."))
            self.btn_manage_themes.clicked.connect(self._manage_themes)
            theme_btn_layout.addWidget(self.btn_manage_themes)
            theme_btn_layout.addStretch()
            theme_layout.addRow(theme_btn_layout)

            theme_group.setLayout(theme_layout)
            layout.addWidget(theme_group)

        # Translation Preferences
        trans_group = QGroupBox(self.tr("Translation Preferences"))
        trans_layout = QFormLayout()

        self.combo_source = QComboBox()
        for lang in self.SUPPORTED_LANGUAGES:
            self.combo_source.addItem(self.tr(lang), lang)
        self.combo_source.setCurrentIndex(self.combo_source.findData(self._settings.default_source_lang))
        trans_layout.addRow(self.tr("Default Source:"), self.combo_source)

        self.combo_target = QComboBox()
        for lang in self.SUPPORTED_LANGUAGES:
            self.combo_target.addItem(self.tr(lang), lang)
        self.combo_target.setCurrentIndex(self.combo_target.findData(self._settings.default_target_lang))
        trans_layout.addRow(self.tr("Default Target:"), self.combo_target)

        self.spin_quality = QSpinBox()
        self.spin_quality.setRange(1, 10)
        self.spin_quality.setValue(self._settings.quality_level)
        self.spin_quality.setSuffix("/10")
        trans_layout.addRow(self.tr("Default Quality:"), self.spin_quality)

        self.spin_threshold = QSpinBox()
        self.spin_threshold.setRange(100, 10000)
        self.spin_threshold.setValue(self._settings.long_string_threshold)
        self.spin_threshold.setToolTip(self.tr("Character count threshold for 'long' strings"))
        trans_layout.addRow(self.tr("Long String Threshold:"), self.spin_threshold)

        self.combo_long_action = QComboBox()
        self.combo_long_action.addItems([
            self.tr("Translate"),
            self.tr("Original"),
            self.tr("Skip")
        ])
        # Note: We need to match the setting value, not the translated value
        # But wait, self.SUPPORTED_LANGUAGES are not translated.
        # For long_string_action, it's better to use English keys internally.

        # Let's fix long_string_action handling
        actions = ["Translate", "Original", "Skip"]
        self.combo_long_action.clear()
        for action in actions:
            self.combo_long_action.addItem(self.tr(action), action)

        idx = self.combo_long_action.findData(self._settings.long_string_action)
        if idx >= 0:
            self.combo_long_action.setCurrentIndex(idx)

        self.combo_long_action.setToolTip(
            self.tr("Action to take for strings exceeding the threshold:\n"
                    "- Translate: Proceed with translation (may take long)\n"
                    "- Original: Immediately return original text\n"
                    "- Skip: Leave untranslated and mark as pending")
        )
        trans_layout.addRow(self.tr("Long String Action:"), self.combo_long_action)

        # Skip string types — checkboxes for each content type
        from gui.string_type_detector import StringType
        _skip_types_saved = set(getattr(self._settings, "skip_string_types", []))
        _skip_types_row = QWidget()
        _skip_types_layout = QHBoxLayout(_skip_types_row)
        _skip_types_layout.setContentsMargins(0, 0, 0, 0)
        _skip_types_layout.setSpacing(8)
        self._skip_type_checks: dict = {}
        _skip_labels = {
            StringType.BOOK:     self.tr("Books"),
            StringType.NOTE:     self.tr("Notes"),
            StringType.TERMINAL: self.tr("Terminals"),
            StringType.DIALOGUE: self.tr("Dialogue"),
            StringType.QUEST:    self.tr("Quests"),
            StringType.UI:       self.tr("UI"),
            StringType.SYSTEM:   self.tr("System"),
        }
        for _st, _lbl in _skip_labels.items():
            _chk = QCheckBox(_lbl)
            _chk.setChecked(_st.name in _skip_types_saved)
            _skip_types_layout.addWidget(_chk)
            self._skip_type_checks[_st.name] = _chk
        _skip_types_layout.addStretch()
        _skip_types_row.setToolTip(self.tr(
            "String types to skip during AI batch translation.\n"
            "Skipped strings are left untranslated (marked as pending)."
        ))
        trans_layout.addRow(self.tr("Skip Types:"), _skip_types_row)

        self.chk_auto_save = QCheckBox(self.tr("Auto-save after translation"))
        self.chk_auto_save.setChecked(self._settings.auto_save)
        trans_layout.addRow(self.chk_auto_save)

        trans_group.setLayout(trans_layout)
        layout.addWidget(trans_group)

        # Translation Memory Settings
        tm_group = QGroupBox(self.tr("Translation Memory"))
        tm_layout = QFormLayout()

        # Fuzzy threshold slider — maps score 0.0–5.0 to slider 0–50 (×10)
        # Lower score = stricter match; expose to user as "Min. similarity"
        self._tm_score_to_pct = lambda s: max(0, int(100 - s * 18))  # rough 0–5 → 100–10%
        self._tm_pct_to_score = lambda p: round((100 - p) / 18, 1)

        current_score = getattr(self._settings, "tm_fuzzy_max_score", 3.0)
        current_pct = self._tm_score_to_pct(current_score)

        self.slider_tm_fuzzy = QSlider(Qt.Orientation.Horizontal)
        self.slider_tm_fuzzy.setRange(10, 100)   # 10% … 100% similarity
        self.slider_tm_fuzzy.setSingleStep(5)
        self.slider_tm_fuzzy.setPageStep(10)
        self.slider_tm_fuzzy.setValue(current_pct)

        self._lbl_tm_fuzzy = QLabel(f"{current_pct}%")
        self._lbl_tm_fuzzy.setFixedWidth(36)
        self.slider_tm_fuzzy.valueChanged.connect(
            lambda v: self._lbl_tm_fuzzy.setText(f"{v}%")
        )
        self.slider_tm_fuzzy.setToolTip(self.tr(
            "Minimum similarity required for a fuzzy translation memory match.\n"
            "Higher = stricter (fewer but more accurate matches).\n"
            "100% = exact matches only.  Default: ~46%."
        ))

        slider_row = QHBoxLayout()
        slider_row.addWidget(self.slider_tm_fuzzy, 1)
        slider_row.addWidget(self._lbl_tm_fuzzy)
        tm_layout.addRow(self.tr("Min. fuzzy similarity:"), slider_row)

        tm_group.setLayout(tm_layout)
        layout.addWidget(tm_group)

        # Performance Settings
        perf_group = QGroupBox(self.tr("Performance"))
        perf_layout = QFormLayout()

        self.chk_enable_cache = QCheckBox(self.tr("Enable translation cache"))
        self.chk_enable_cache.setChecked(self._settings.enable_cache)
        self.chk_enable_cache.setToolTip(
            self.tr("Cache completed translations to disk so repeated strings are returned instantly.")
        )
        perf_layout.addRow(self.chk_enable_cache)

        self.btn_clear_cache = QPushButton(self.tr("Clear Cache"))
        self.btn_clear_cache.setToolTip(self.tr("Remove all cached translations from memory and disk"))
        self.btn_clear_cache.clicked.connect(self._clear_cache)
        perf_layout.addRow(self.btn_clear_cache)

        self.spin_max_workers = QSpinBox()
        self.spin_max_workers.setRange(1, 32)
        self.spin_max_workers.setValue(self._settings.max_workers)
        self.spin_max_workers.setToolTip(
            self.tr("Number of parallel translation threads (1–32). "
                    "Higher values increase throughput but may overwhelm Ollama. "
                    "Default: 10.")
        )
        perf_layout.addRow(self.tr("Parallel workers:"), self.spin_max_workers)

        perf_group.setLayout(perf_layout)
        layout.addWidget(perf_group)

        # Storage Settings
        storage_group = QGroupBox(self.tr("Storage"))
        storage_layout = QFormLayout()

        active_dir = get_config_dir()
        self._lbl_active_config_dir = QLabel(str(active_dir))
        self._lbl_active_config_dir.setStyleSheet("color: palette(mid); font-size: 11px;")
        self._lbl_active_config_dir.setWordWrap(True)
        storage_layout.addRow(self.tr("Active config dir:"), self._lbl_active_config_dir)

        override = get_config_dir_override()
        self._config_dir_edit = QLineEdit(str(override) if override else "")
        self._config_dir_edit.setPlaceholderText(self.tr("(default: ~/.config/BethesdaModTools)"))
        self._config_dir_edit.setToolTip(
            self.tr("Override the directory where config.json and other app data are stored.\n"
                    "Leave blank to use the default location.\n"
                    "Takes effect after restarting the application.")
        )
        self._config_dir_edit.textChanged.connect(self._on_config_dir_changed)

        btn_browse_config_dir = QPushButton(self.tr("Browse…"))
        btn_browse_config_dir.clicked.connect(self._browse_config_dir)
        btn_reset_config_dir = QPushButton(self.tr("Reset"))
        btn_reset_config_dir.setToolTip(self.tr("Clear override and use the default config directory"))
        btn_reset_config_dir.clicked.connect(lambda: self._config_dir_edit.clear())

        config_dir_row = QHBoxLayout()
        config_dir_row.addWidget(self._config_dir_edit, 1)
        config_dir_row.addWidget(btn_browse_config_dir)
        config_dir_row.addWidget(btn_reset_config_dir)
        storage_layout.addRow(self.tr("Config directory:"), config_dir_row)

        self._lbl_config_dir_restart = QLabel(
            self.tr("⚠  Restart the application to use the new config directory.")
        )
        self._lbl_config_dir_restart.setStyleSheet(
            "color: #e8a020; font-style: italic; font-size: 11px;"
        )
        self._lbl_config_dir_restart.setVisible(False)
        storage_layout.addRow(self._lbl_config_dir_restart)

        self._orig_config_dir_override = str(override) if override else ""

        # Cache directory row
        active_cache = get_cache_dir()
        self._lbl_active_cache_dir = QLabel(str(active_cache))
        self._lbl_active_cache_dir.setStyleSheet("color: palette(mid); font-size: 11px;")
        self._lbl_active_cache_dir.setWordWrap(True)
        storage_layout.addRow(self.tr("Active cache dir:"), self._lbl_active_cache_dir)

        cache_override = get_cache_dir_override()
        self._cache_dir_edit = QLineEdit(str(cache_override) if cache_override else "")
        self._cache_dir_edit.setPlaceholderText(self.tr("(default: SSD if mounted, else config dir)"))
        self._cache_dir_edit.setToolTip(
            self.tr("Override the directory for the translation cache and other large data files.\n"
                    "Leave blank to auto-select: /mnt/ssd/… when the SSD is mounted, otherwise the config dir.\n"
                    "Takes effect after restarting the application.")
        )
        self._cache_dir_edit.textChanged.connect(self._on_cache_dir_changed)

        btn_browse_cache_dir = QPushButton(self.tr("Browse…"))
        btn_browse_cache_dir.clicked.connect(self._browse_cache_dir)
        btn_reset_cache_dir = QPushButton(self.tr("Reset"))
        btn_reset_cache_dir.setToolTip(self.tr("Clear override and use the default cache directory"))
        btn_reset_cache_dir.clicked.connect(lambda: self._cache_dir_edit.clear())

        cache_dir_row = QHBoxLayout()
        cache_dir_row.addWidget(self._cache_dir_edit, 1)
        cache_dir_row.addWidget(btn_browse_cache_dir)
        cache_dir_row.addWidget(btn_reset_cache_dir)
        storage_layout.addRow(self.tr("Cache directory:"), cache_dir_row)

        self._lbl_cache_dir_restart = QLabel(
            self.tr("⚠  Restart the application to use the new cache directory.")
        )
        self._lbl_cache_dir_restart.setStyleSheet(
            "color: #e8a020; font-style: italic; font-size: 11px;"
        )
        self._lbl_cache_dir_restart.setVisible(False)
        storage_layout.addRow(self._lbl_cache_dir_restart)

        self._orig_cache_dir_override = str(cache_override) if cache_override else ""

        storage_group.setLayout(storage_layout)
        layout.addWidget(storage_group)

        # Security Settings
        sec_group = QGroupBox(self.tr("Security"))
        sec_layout = QFormLayout()

        self.chk_encrypt_cache = QCheckBox(self.tr("Encrypt translation cache"))
        self.chk_encrypt_cache.setChecked(self._settings.encrypt_cache)
        self.chk_encrypt_cache.setToolTip(
            self.tr(
                "Protect the on-disk translation cache with AES-256-GCM encryption.\n"
                "The key is stored in the system keyring or derived from the machine ID.\n"
                "Takes effect on the next cache save."
            )
        )
        sec_layout.addRow(self.chk_encrypt_cache)

        self.chk_audit_log = QCheckBox(self.tr("Enable security audit log"))
        self.chk_audit_log.setChecked(self._settings.audit_logging)
        self.chk_audit_log.setToolTip(
            self.tr(
                "Write a JSON-lines audit log of security-relevant events\n"
                "(file open/save, translation batches, settings changes).\n"
                "No translated text is ever recorded."
            )
        )
        sec_layout.addRow(self.chk_audit_log)

        # Show which keyring backend is active (informational)
        try:
            from gui.secret_store import get_store
            _backend = get_store().backend_name()
        except Exception:
            _backend = self.tr("unavailable")
        lbl_keyring = QLabel(self.tr("Key storage: {backend}").format(backend=_backend))
        lbl_keyring.setStyleSheet("color: palette(mid); font-size: 11px;")
        sec_layout.addRow(lbl_keyring)

        sec_group.setLayout(sec_layout)
        layout.addWidget(sec_group)

        # AI Quality Check
        ai_qc_group = QGroupBox(self.tr("AI Quality Check"))
        ai_qc_layout = QFormLayout()

        self.chk_enable_ai_qc = QCheckBox(self.tr("Enable AI quality check after rule-based QC"))
        self.chk_enable_ai_qc.setChecked(getattr(self._settings, "enable_ai_qc", False))
        self.chk_enable_ai_qc.setToolTip(
            self.tr(
                "Run the fine-tuned qcgemma4-st Ollama model on each translated string\n"
                "after the rule-based quality check. Slower but catches issues the rules miss.\n"
                "Requires the model to be registered: ollama create qcgemma4-st -f Modelfile.qc"
            )
        )
        ai_qc_layout.addRow(self.chk_enable_ai_qc)

        self.ai_qc_model_edit = QLineEdit(getattr(self._settings, "ai_qc_model", "qcgemma4-st"))
        self.ai_qc_model_edit.setToolTip(self.tr("Ollama model name for AI quality checks"))
        ai_qc_layout.addRow(self.tr("AI QC model:"), self.ai_qc_model_edit)

        ai_qc_group.setLayout(ai_qc_layout)
        layout.addWidget(ai_qc_group)

        # Lore RAG
        lore_rag_group = QGroupBox(self.tr("Lore RAG (Context Retrieval)"))
        lore_rag_layout = QFormLayout()

        self.chk_enable_lore_rag = QCheckBox(
            self.tr("Inject lore context into translation prompts")
        )
        self.chk_enable_lore_rag.setChecked(getattr(self._settings, "enable_lore_rag", False))
        self.chk_enable_lore_rag.setToolTip(
            self.tr(
                "When enabled, relevant lore articles (factions, places, characters) are\n"
                "retrieved from the local lore database and prepended to each translation\n"
                "prompt so the AI uses accurate Starfield terminology.\n"
                "Use Translation → Lore RAG Context… to download articles from UESP."
            )
        )
        lore_rag_layout.addRow(self.chk_enable_lore_rag)

        self.lore_rag_max_chars_spin = QSpinBox()
        self.lore_rag_max_chars_spin.setRange(100, 2000)
        self.lore_rag_max_chars_spin.setSingleStep(50)
        self.lore_rag_max_chars_spin.setValue(
            getattr(self._settings, "lore_rag_max_snippet_chars", 480)
        )
        self.lore_rag_max_chars_spin.setToolTip(
            self.tr("Maximum characters of lore context injected per prompt.\n"
                    "Higher values give more context but consume more tokens.")
        )
        lore_rag_layout.addRow(self.tr("Max context chars:"), self.lore_rag_max_chars_spin)

        lore_rag_group.setLayout(lore_rag_layout)
        layout.addWidget(lore_rag_group)

        # NexusMods
        nexus_group = QGroupBox(self.tr("NexusMods"))
        nexus_layout = QFormLayout()

        nexus_key_row = QHBoxLayout()
        self.nexusmods_api_key_edit = QLineEdit(getattr(self._settings, "nexusmods_api_key", ""))
        self.nexusmods_api_key_edit.setPlaceholderText(self.tr("Paste your NexusMods API key here"))
        self.nexusmods_api_key_edit.setEchoMode(QLineEdit.Password)
        self.nexusmods_api_key_edit.setToolTip(self.tr(
            "Personal API key from nexusmods.com → Settings → API Keys.\n"
            "Required for uploading mod files and browsing download links."
        ))
        nexus_key_row.addWidget(self.nexusmods_api_key_edit, stretch=1)
        btn_show_nexus_key = QPushButton(self.tr("Show"))
        btn_show_nexus_key.setMaximumWidth(52)
        btn_show_nexus_key.setCheckable(True)
        btn_show_nexus_key.toggled.connect(
            lambda checked: self.nexusmods_api_key_edit.setEchoMode(
                QLineEdit.Normal if checked else QLineEdit.Password
            )
        )
        nexus_key_row.addWidget(btn_show_nexus_key)
        nexus_layout.addRow(self.tr("API Key:"), nexus_key_row)

        self.nexusmods_file_group_edit = QLineEdit(getattr(self._settings, "nexusmods_file_group_id", ""))
        self.nexusmods_file_group_edit.setPlaceholderText("123456")
        self.nexusmods_file_group_edit.setToolTip(self.tr(
            "Optional: NexusMods file group ID to attach uploaded files to an existing group."
        ))
        nexus_layout.addRow(self.tr("File Group ID:"), self.nexusmods_file_group_edit)

        nexus_cookies_row = QHBoxLayout()
        self.nexusmods_cookies_edit = QLineEdit(getattr(self._settings, "nexusmods_cookies_file", ""))
        self.nexusmods_cookies_edit.setPlaceholderText(self.tr("(auto-detect from Firefox / Chromium)"))
        self.nexusmods_cookies_edit.setToolTip(self.tr(
            "Optional: path to a Cookie-Editor JSON export for free-user NexusMods downloads.\n"
            "Export steps: install the 'Cookie-Editor' browser extension → visit nexusmods.com\n"
            "→ open Cookie-Editor → Export → JSON → save the file → select it here.\n"
            "Leave blank to auto-detect cookies from Firefox or Chromium."
        ))
        nexus_cookies_row.addWidget(self.nexusmods_cookies_edit, stretch=1)
        btn_browse_cookies = QPushButton(self.tr("Browse…"))
        btn_browse_cookies.setMaximumWidth(72)
        btn_browse_cookies.clicked.connect(self._browse_cookies_file)
        nexus_cookies_row.addWidget(btn_browse_cookies)
        nexus_layout.addRow(self.tr("Cookies JSON:"), nexus_cookies_row)

        nexus_group.setLayout(nexus_layout)
        layout.addWidget(nexus_group)

        # Audio / TTS Preview
        audio_group = QGroupBox(self.tr("Audio / TTS Preview"))
        audio_layout = QFormLayout()

        self.chk_enable_audio_preview = QCheckBox(self.tr("Enable Audio Preview panel"))
        self.chk_enable_audio_preview.setChecked(
            getattr(self._settings, "enable_audio_preview", False)
        )
        self.chk_enable_audio_preview.setToolTip(self.tr(
            "Show the Audio Preview dock so you can play the original game audio\n"
            "and synthesize a TTS read-out of your translation for timing comparison."
        ))
        audio_layout.addRow(self.chk_enable_audio_preview)

        self.combo_tts_engine = QComboBox()
        for label, val in [
            (self.tr("eSpeak-NG (built-in)"), "espeak"),
            (self.tr("Piper (neural, external binary)"), "piper"),
            (self.tr("None (duration estimate only)"), "none"),
        ]:
            self.combo_tts_engine.addItem(label, val)
        cur_engine = getattr(self._settings, "tts_engine_type", "espeak")
        idx = self.combo_tts_engine.findData(cur_engine)
        if idx >= 0:
            self.combo_tts_engine.setCurrentIndex(idx)
        audio_layout.addRow(self.tr("TTS engine:"), self.combo_tts_engine)

        self.espeak_voice_edit = QLineEdit(getattr(self._settings, "espeak_voice", "uk"))
        self.espeak_voice_edit.setPlaceholderText("uk")
        self.espeak_voice_edit.setToolTip(self.tr(
            "eSpeak-NG voice code, e.g. uk, ru, de, fr, en-us.\n"
            "Run `espeak-ng --voices` for the full list."
        ))
        audio_layout.addRow(self.tr("eSpeak voice:"), self.espeak_voice_edit)

        self.espeak_speed_spin = QSpinBox()
        self.espeak_speed_spin.setRange(60, 350)
        self.espeak_speed_spin.setValue(getattr(self._settings, "espeak_speed", 130))
        self.espeak_speed_spin.setToolTip(self.tr(
            "eSpeak-NG words-per-minute rate (default 130 — slower than natural\n"
            "speech to better match game dialogue cadence)."
        ))
        audio_layout.addRow(self.tr("eSpeak speed (WPM):"), self.espeak_speed_spin)

        piper_row = QHBoxLayout()
        self.piper_binary_edit = QLineEdit(getattr(self._settings, "piper_binary", ""))
        self.piper_binary_edit.setPlaceholderText("piper")
        self.piper_binary_edit.setToolTip(self.tr("Path to the Piper binary, or just 'piper' if on PATH."))
        piper_row.addWidget(self.piper_binary_edit, stretch=1)
        btn_browse_piper = QPushButton(self.tr("…"))
        btn_browse_piper.setMaximumWidth(28)
        btn_browse_piper.clicked.connect(self._browse_piper_binary)
        piper_row.addWidget(btn_browse_piper)
        audio_layout.addRow(self.tr("Piper binary:"), piper_row)

        piper_model_row = QHBoxLayout()
        self.piper_model_edit = QLineEdit(getattr(self._settings, "piper_model", ""))
        self.piper_model_edit.setPlaceholderText(self.tr("path/to/model.onnx"))
        self.piper_model_edit.setToolTip(self.tr("Path to the Piper .onnx voice model file."))
        piper_model_row.addWidget(self.piper_model_edit, stretch=1)
        btn_browse_model = QPushButton(self.tr("…"))
        btn_browse_model.setMaximumWidth(28)
        btn_browse_model.clicked.connect(self._browse_piper_model)
        piper_model_row.addWidget(btn_browse_model)
        audio_layout.addRow(self.tr("Piper model:"), piper_model_row)

        audio_dir_row = QHBoxLayout()
        self.audio_dir_edit = QLineEdit(getattr(self._settings, "audio_dir", ""))
        self.audio_dir_edit.setPlaceholderText(self.tr("Root dir of extracted game audio files"))
        self.audio_dir_edit.setToolTip(self.tr(
            "Directory containing extracted Starfield/Fallout/Skyrim audio files.\n"
            "The panel will try to auto-locate files by form ID from the filename."
        ))
        audio_dir_row.addWidget(self.audio_dir_edit, stretch=1)
        btn_browse_audio = QPushButton(self.tr("…"))
        btn_browse_audio.setMaximumWidth(28)
        btn_browse_audio.clicked.connect(self._browse_audio_dir)
        audio_dir_row.addWidget(btn_browse_audio)
        audio_layout.addRow(self.tr("Audio directory:"), audio_dir_row)

        self.chk_tts_auto_preview = QCheckBox(
            self.tr("Auto-synthesize TTS on string selection")
        )
        self.chk_tts_auto_preview.setChecked(
            getattr(self._settings, "tts_auto_preview", False)
        )
        self.chk_tts_auto_preview.setToolTip(self.tr(
            "Automatically synthesize the TTS read-out whenever you select\n"
            "a new string. May slow down navigation if synthesis takes > 1 s."
        ))
        audio_layout.addRow(self.chk_tts_auto_preview)

        audio_group.setLayout(audio_layout)
        layout.addWidget(audio_group)

        # Keyboard Shortcuts
        if self._keyboard_manager is not None:
            layout.addWidget(self._build_shortcuts_section())

        # Info note - translategemma3-st optimized
        info = QLabel(self.tr("💡 Tip: Uses translategemma3-st (custom modified) optimized for Starfield Ukrainian localization. Use English Anchors: 'To Ukrainian:', 'To English:', etc."))
        info.setWordWrap(True)
        info.setStyleSheet("color: palette(mid); font-style: italic;")
        layout.addWidget(info)

        layout.addStretch()

        layout.addStretch()
        scroll.setWidget(content)
        root_layout.addWidget(scroll)

        # Dialog buttons (kept outside scroll area)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root_layout.addWidget(buttons)

    def _default_ollama_model_list(self) -> list:
        """Return models to pre-populate the combo on dialog open."""
        models = list(self._DEFAULT_OLLAMA_MODELS)
        current = self._settings.ollama_model
        if current and current not in models:
            models.insert(0, current)
        return models

    @Slot()
    def _refresh_ollama_models(self):
        """Fetch installed models from Ollama and populate the model combo."""
        import requests
        url = self.ollama_url.text().rstrip('/')
        current = self.ollama_model.currentText().strip()
        self.btn_refresh_models.setEnabled(False)
        self.btn_refresh_models.setText(self.tr("…"))
        QApplication.processEvents()
        try:
            resp = requests.get(f"{url}/api/tags", timeout=5)
            resp.raise_for_status()
            model_names = [m['name'] for m in resp.json().get('models', [])]
            model_names.sort()
            self.ollama_model.blockSignals(True)
            self.ollama_model.clear()
            self.ollama_model.addItems(model_names)
            self.ollama_model.blockSignals(False)
            idx = self.ollama_model.findText(current)
            if idx >= 0:
                self.ollama_model.setCurrentIndex(idx)
            else:
                self.ollama_model.setCurrentText(current)
            self.lbl_connection.setText(
                self.tr("● {n} model(s) loaded").format(n=len(model_names))
            )
            self.lbl_connection.setStyleSheet("color: green;")
        except Exception as exc:
            self.lbl_connection.setText(self.tr("● Refresh failed"))
            self.lbl_connection.setStyleSheet("color: red;")
            QMessageBox.warning(
                self, self.tr("Refresh Failed"),
                self.tr("Could not load models from {url}:\n{error}").format(url=url, error=exc),
            )
        finally:
            self.btn_refresh_models.setEnabled(True)
            self.btn_refresh_models.setText(self.tr("Refresh"))

    @Slot()
    def _test_connection(self):
        """Test connection to Ollama."""
        import requests
        url = self.ollama_url.text().rstrip('/')
        model = self.ollama_model.currentText()

        self.lbl_connection.setText(self.tr("● Testing Ollama..."))
        self.lbl_connection.setStyleSheet("color: blue;")
        QApplication.processEvents()

        try:
            resp = requests.get(f"{url}/api/tags", timeout=5)
            if resp.status_code != 200:
                raise Exception(f"HTTP {resp.status_code}")

            models_data = resp.json().get('models', [])
            model_names = [m['name'] for m in models_data]

            if not any(model in m or m.startswith(model) for m in model_names):
                self.lbl_connection.setText(self.tr("● Model '{model}' not found").format(model=model))
                self.lbl_connection.setStyleSheet("color: orange;")
                QMessageBox.warning(
                    self, self.tr("Model Not Found"),
                    self.tr("Model '{model}' is not installed.\n\nAvailable models:\n").format(model=model) +
                    "\n".join(model_names[:10]) +
                    ("\n..." if len(model_names) > 10 else "") +
                    self.tr("\n\nInstall with: ollama create <model-name> -f Modelfile.<model-name>")
                )
            else:
                self.lbl_connection.setText(self.tr("● Connected ✓"))
                self.lbl_connection.setStyleSheet("color: green;")
                QMessageBox.information(self, self.tr("Success"), self.tr("Connected to Ollama!\nModel '{model}' is ready.").format(model=model))

        except requests.exceptions.ConnectionError:
            self.lbl_connection.setText(self.tr("● Connection failed"))
            self.lbl_connection.setStyleSheet("color: red;")
            QMessageBox.critical(
                self, self.tr("Connection Error"),
                self.tr("Could not connect to Ollama at {url}\n\n"
                        "Make sure Ollama is running:\n"
                        "  • Start with: ollama serve\n"
                        "  • Default URL: http://localhost:11434").format(url=url)
            )
        except Exception as e:
            self.lbl_connection.setText(self.tr("● Error"))
            self.lbl_connection.setStyleSheet("color: red;")
            QMessageBox.critical(self, self.tr("Error"), self.tr("Unexpected error: {error}").format(error=e))

    @Slot()
    def _browse_config_dir(self):
        """Browse for a custom config directory."""
        current = self._config_dir_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, self.tr("Select Config Directory"), current
        )
        if chosen:
            self._config_dir_edit.setText(chosen)

    @Slot(str)
    def _on_config_dir_changed(self, text: str) -> None:
        changed = text.strip() != self._orig_config_dir_override
        self._lbl_config_dir_restart.setVisible(changed)

    @Slot()
    def _browse_cache_dir(self):
        """Browse for a custom cache directory."""
        current = self._cache_dir_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(
            self, self.tr("Select Cache Directory"), current
        )
        if chosen:
            self._cache_dir_edit.setText(chosen)

    @Slot(str)
    def _on_cache_dir_changed(self, text: str) -> None:
        changed = text.strip() != self._orig_cache_dir_override
        self._lbl_cache_dir_restart.setVisible(changed)

    @Slot()
    def _browse_terms_file(self):
        """Browse for custom protected terms file."""
        file_path, _ = get_open_filename(
            self, self.tr("Select Protected Terms File"), "",
            self.tr("Text Files (*.txt *.TXT);;All Files (*)")
        )
        if file_path:
            self.terms_file_path.setText(file_path)

    @Slot()
    def _browse_cookies_file(self):
        file_path, _ = get_open_filename(
            self, self.tr("Select Cookie-Editor JSON Export"), "",
            self.tr("JSON Files (*.json *.JSON);;All Files (*)")
        )
        if file_path:
            self.nexusmods_cookies_edit.setText(file_path)

    @Slot()
    def _view_protected_terms(self):
        """Show dialog to view/edit protected terms."""
        from gui.protected_terms_dialog import ProtectedTermsDialog
        dialog = ProtectedTermsDialog(self._settings, self, term_protector=self._term_protector)
        dialog.exec()

    @Slot()
    def _browse_piper_binary(self) -> None:
        path, _ = get_open_filename(
            self, self.tr("Select Piper Binary"), "", self.tr("Executable (*);;All Files (*)")
        )
        if path:
            self.piper_binary_edit.setText(path)

    @Slot()
    def _browse_piper_model(self) -> None:
        path, _ = get_open_filename(
            self, self.tr("Select Piper Voice Model"), "",
            self.tr("ONNX model (*.onnx);;All Files (*)")
        )
        if path:
            self.piper_model_edit.setText(path)

    @Slot()
    def _browse_audio_dir(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        d = QFileDialog.getExistingDirectory(
            self, self.tr("Select audio files directory"),
            self.audio_dir_edit.text() or str(Path.home()),
        )
        if d:
            self.audio_dir_edit.setText(d)

    @Slot(str)
    @Slot(int)
    def _on_lang_changed(self, _index: int) -> None:
        """Show a restart-required notice when the UI language is changed."""
        new_code = self.combo_ui_lang.currentData()
        if new_code != self._orig_ui_lang:
            if not hasattr(self, "_lang_restart_lbl"):
                self._lang_restart_lbl = QLabel(
                    self.tr("⚠  Restart the application to apply the new language.")
                )
                self._lang_restart_lbl.setStyleSheet(
                    "color: #e8a020; font-style: italic; font-size: 11px;"
                )
                # Insert below the language row — find the form layout that owns combo_ui_lang
                parent_layout = self.combo_ui_lang.parentWidget()
                if parent_layout is not None:
                    lo = parent_layout.layout()
                    if lo is not None:
                        lo.addRow(self._lang_restart_lbl)
            self._lang_restart_lbl.setVisible(True)
        elif hasattr(self, "_lang_restart_lbl"):
            self._lang_restart_lbl.setVisible(False)

    def _on_theme_changed(self, theme_name: str):
        """Update theme description and apply a live preview to this dialog."""
        if not self._theme_manager:
            return
        self.lbl_theme_desc.setText(self._theme_manager.get_theme_description(theme_name))
        # Live preview: apply the theme stylesheet to the dialog so the user
        # sees the result immediately.  The app-wide stylesheet is untouched
        # until the user clicks OK.
        concrete = self._theme_manager.effective_theme(theme_name)
        preview_qss = self._theme_manager.get_stylesheet(concrete) or ""
        self.setStyleSheet(preview_qss)

    def _setup_dirty_tracking(self) -> None:
        """Connect all form widgets to _mark_dirty so any edit sets the flag."""
        for w in self.findChildren(QLineEdit):
            w.textChanged.connect(self._mark_dirty)
        for w in self.findChildren(QComboBox):
            w.currentIndexChanged.connect(self._mark_dirty)
        for w in self.findChildren(QSpinBox):
            w.valueChanged.connect(self._mark_dirty)
        for w in self.findChildren(QCheckBox):
            w.toggled.connect(self._mark_dirty)
        for w in self.findChildren(QSlider):
            w.valueChanged.connect(self._mark_dirty)
        for w in self.findChildren(QKeySequenceEdit):
            w.keySequenceChanged.connect(self._mark_dirty)

    @Slot()
    def _mark_dirty(self, *_args) -> None:
        self._dirty = True

    def accept(self):
        self._dirty = False  # saved — no warning needed on subsequent close
        super().accept()

    def reject(self):
        """Warn about unsaved changes, then restore the original theme preview."""
        if self._dirty:
            reply = QMessageBox.question(
                self,
                self.tr("Unsaved Changes"),
                self.tr("You have unsaved changes.\nDiscard them and close?"),
                QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if reply != QMessageBox.Discard:
                return
        if self._theme_manager:
            orig_concrete = self._theme_manager.effective_theme(self._original_theme)
            orig_qss = self._theme_manager.get_stylesheet(orig_concrete) or ""
            self.setStyleSheet(orig_qss)
        super().reject()

    @Slot()
    def _clear_cache(self):
        """Clear the translation cache."""
        if self._translation_cache is None:
            QMessageBox.information(self, self.tr("Cache"), self.tr("No translation cache is active."))
            return
        reply = QMessageBox.question(
            self, self.tr("Clear Cache"),
            self.tr("Remove all cached translations?\nThis cannot be undone."),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            removed = len(self._translation_cache)
            self._translation_cache.clear()
            self._translation_cache.save()
            try:
                from gui.audit_log import get_audit_log
                get_audit_log().cache_cleared(removed)
            except Exception:
                pass
            QMessageBox.information(self, self.tr("Cache"), self.tr("Translation cache cleared."))

    @Slot()
    def _manage_themes(self):
        """Open theme management dialog."""
        if self._theme_manager:
            from gui.theme_dialog import ThemeDialog
            dialog = ThemeDialog(self._theme_manager, self)
            if dialog.exec() == QDialog.Accepted:
                # Refresh theme list in case themes were added/removed
                self.combo_theme.clear()
                self.combo_theme.addItems(self._theme_manager.available_themes)
                idx = self.combo_theme.findText(self._theme_manager.current_theme)
                if idx >= 0:
                    self.combo_theme.setCurrentIndex(idx)

    def get_selected_theme(self) -> str:
        """Return the currently selected theme name."""
        if hasattr(self, 'combo_theme'):
            return self.combo_theme.currentText()
        return self._settings.theme

    def apply_to_settings(self, settings: AppSettings) -> None:
        """Apply dialog values to the given AppSettings instance."""
        settings.ollama_url = self.ollama_url.text().rstrip('/')
        settings.ollama_model = self.ollama_model.currentText()
        settings.ollama_num_predict = self.spin_num_predict.value()
        settings.ollama_num_ctx = self.spin_num_ctx.value()
        settings.ollama_num_thread = self.spin_num_thread.value()
        settings.default_source_lang = self.combo_source.currentData()
        settings.default_target_lang = self.combo_target.currentData()
        settings.quality_level = self.spin_quality.value()
        settings.long_string_threshold = self.spin_threshold.value()
        settings.long_string_action = self.combo_long_action.currentData()
        settings.skip_string_types = [
            name for name, chk in self._skip_type_checks.items() if chk.isChecked()
        ]
        settings.auto_save = self.chk_auto_save.isChecked()
        settings.enable_term_protection = self.chk_enable_protection.isChecked()
        settings.protect_english_text = self.chk_protect_english_text.isChecked()
        settings.protect_named_entities = self.chk_protect_named_entities.isChecked()
        settings.protected_terms_file = self.terms_file_path.text()
        settings.theme = self.get_selected_theme()
        settings.ui_language = self.combo_ui_lang.currentData()
        settings.font_size = self.spin_font_size.value()
        settings.color_blind_mode = self.chk_color_blind.isChecked()
        settings.enable_cache = self.chk_enable_cache.isChecked()
        settings.max_workers = self.spin_max_workers.value()
        settings.tm_fuzzy_max_score = self._tm_pct_to_score(self.slider_tm_fuzzy.value())
        settings.encrypt_cache = self.chk_encrypt_cache.isChecked()
        settings.audit_logging = self.chk_audit_log.isChecked()
        settings.enable_ai_qc = self.chk_enable_ai_qc.isChecked()
        settings.ai_qc_model = self.ai_qc_model_edit.text().strip() or "qcgemma4-st"
        settings.enable_lore_rag = self.chk_enable_lore_rag.isChecked()
        settings.lore_rag_max_snippet_chars = self.lore_rag_max_chars_spin.value()
        settings.nexusmods_api_key       = self.nexusmods_api_key_edit.text().strip()
        settings.nexusmods_file_group_id = self.nexusmods_file_group_edit.text().strip()
        settings.nexusmods_cookies_file  = self.nexusmods_cookies_edit.text().strip()
        settings.enable_audio_preview = self.chk_enable_audio_preview.isChecked()
        settings.tts_engine_type = self.combo_tts_engine.currentData()
        settings.espeak_voice = self.espeak_voice_edit.text().strip() or "uk"
        settings.espeak_speed = self.espeak_speed_spin.value()
        settings.piper_binary = self.piper_binary_edit.text().strip()
        settings.piper_model = self.piper_model_edit.text().strip()
        settings.audio_dir = self.audio_dir_edit.text().strip()
        settings.tts_auto_preview = self.chk_tts_auto_preview.isChecked()
        if self._keyboard_manager is not None:
            settings.custom_shortcuts = self.get_custom_shortcuts()
        # Config/cache dir overrides are stored in bootstrap files, not in AppSettings
        raw = self._config_dir_edit.text().strip()
        set_config_dir_override(Path(raw) if raw else None)
        raw_cache = self._cache_dir_edit.text().strip()
        set_cache_dir_override(Path(raw_cache) if raw_cache else None)

    def _build_shortcuts_section(self) -> QGroupBox:
        """Build the Keyboard Shortcuts group box with QKeySequenceEdit per action."""
        group = QGroupBox(self.tr("Keyboard Shortcuts"))
        outer = QVBoxLayout(group)

        if self._keyboard_manager is None:
            return group

        # Group actions by category
        from collections import defaultdict
        by_cat: dict = defaultdict(list)
        for entry in self._keyboard_manager.all_actions():
            by_cat[entry.category].append(entry)

        for category in sorted(by_cat):
            cat_label = QLabel(f"<b>{category}</b>")
            cat_label.setStyleSheet("margin-top: 6px;")
            outer.addWidget(cat_label)

            form = QFormLayout()
            form.setContentsMargins(16, 0, 0, 0)
            for entry in sorted(by_cat[category], key=lambda e: e.name):
                editor = QKeySequenceEdit()
                current = self._keyboard_manager.effective_shortcut(entry.id)
                editor.setKeySequence(QKeySequence(current))
                editor.setToolTip(entry.description)
                self._shortcut_editors[entry.id] = editor
                form.addRow(entry.name, editor)
            outer.addLayout(form)

        # Reset-all button
        btn_reset = QPushButton(self.tr("Reset All to Defaults"))
        btn_reset.clicked.connect(self._reset_all_shortcuts)
        outer.addWidget(btn_reset)

        return group

    @Slot()
    def _reset_all_shortcuts(self) -> None:
        if self._keyboard_manager is None:
            return
        for action_id, editor in self._shortcut_editors.items():
            entry = self._keyboard_manager._entries.get(action_id)
            if entry:
                editor.setKeySequence(QKeySequence(entry.default_shortcut))

    def get_custom_shortcuts(self) -> dict:
        """Return a dict of action_id → shortcut string for non-default values."""
        if self._keyboard_manager is None:
            return {}
        result = {}
        for action_id, editor in self._shortcut_editors.items():
            entry = self._keyboard_manager._entries.get(action_id)
            if entry is None:
                continue
            seq = editor.keySequence().toString()
            if seq != entry.default_shortcut:
                result[action_id] = seq
        return result
