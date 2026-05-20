"""
Settings/Preferences dialog with term protection settings
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QComboBox, QSpinBox, QCheckBox,
    QPushButton, QDialogButtonBox, QGroupBox, QLabel,
    QMessageBox, QApplication, QFileDialog, QSlider, QTextEdit, QWidget, QScrollArea, QFrame
)
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QKeySequence, QValidator
from PySide6.QtWidgets import QKeySequenceEdit
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from gui.app_settings import AppSettings
from gui.file_dialog_helper import get_open_filename

if TYPE_CHECKING:
    from gui.term_protector import TermProtector


class SettingsDialog(QDialog):
    """Dialog for configuring Ollama and term protection settings."""
    SUPPORTED_LANGUAGES = [
        'English', 'Russian', 'Ukrainian',
    ]

    # Supported Ollama models
    SUPPORTED_MODELS = [
        'translategemma3-st',
        'translategemma3-st-2',
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
        self._setup_ui()
        self._fit_to_screen()

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
        self.ollama_model.addItems(self.SUPPORTED_MODELS)
        self.ollama_model.setEditable(False)
        self.ollama_model.setCurrentText(self._settings.ollama_model)
        self.ollama_model.setToolTip(
            self.tr(
                "translategemma3-st: Fine-tuned for Starfield Ukrainian localization\n"
                "translategemma3-st-2: Higher quality, typically slower"
            )
        )
        ollama_layout.addRow(self.tr("Model:"), self.ollama_model)

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

        # Statistics
        stats_label = QLabel(self.tr("ℹ️ Default protection includes: Location IDs, Form IDs, Faction names, Character names, Resources, Skills, etc."))
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
            self.combo_ui_lang.addItem(self.tr("English"), "English")
            self.combo_ui_lang.addItem(self.tr("Ukrainian"), "Ukrainian")
            self.combo_ui_lang.setCurrentIndex(self.combo_ui_lang.findData(self._settings.ui_language))
            theme_layout.addRow(self.tr("Interface Language:"), self.combo_ui_lang)

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
    def _browse_terms_file(self):
        """Browse for custom protected terms file."""
        file_path, _ = get_open_filename(
            self, self.tr("Select Protected Terms File"), "",
            self.tr("Text Files (*.txt *.TXT);;All Files (*)")
        )
        if file_path:
            self.terms_file_path.setText(file_path)

    @Slot()
    def _view_protected_terms(self):
        """Show dialog to view/edit protected terms."""
        from gui.protected_terms_dialog import ProtectedTermsDialog
        dialog = ProtectedTermsDialog(self._settings, self, term_protector=self._term_protector)
        dialog.exec()

    @Slot(str)
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

    def reject(self):
        """Restore the original theme preview on cancel."""
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
            self._translation_cache.clear()
            self._translation_cache.save()
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
        settings.auto_save = self.chk_auto_save.isChecked()
        settings.enable_term_protection = self.chk_enable_protection.isChecked()
        settings.protect_english_text = self.chk_protect_english_text.isChecked()
        settings.protected_terms_file = self.terms_file_path.text()
        settings.theme = self.get_selected_theme()
        settings.ui_language = self.combo_ui_lang.currentData()
        settings.enable_cache = self.chk_enable_cache.isChecked()
        settings.max_workers = self.spin_max_workers.value()
        settings.tm_fuzzy_max_score = self._tm_pct_to_score(self.slider_tm_fuzzy.value())
        if self._keyboard_manager is not None:
            settings.custom_shortcuts = self.get_custom_shortcuts()

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
