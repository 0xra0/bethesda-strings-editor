"""
Dialog for creating, editing, and managing custom themes.
"""
from pathlib import Path
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLineEdit, QComboBox, QTextEdit,
    QPushButton, QDialogButtonBox, QLabel, QMessageBox,
    QListWidget, QListWidgetItem, QSplitter, QGroupBox, QInputDialog, QWidget
)
from PySide6.QtCore import Qt, Slot
from gui.theme_manager import ThemeManager


class ThemeDialog(QDialog):
    """Dialog for viewing, creating, and editing custom themes."""

    def __init__(self, theme_manager: ThemeManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Theme Manager"))
        self.setMinimumSize(900, 600)
        self._tm = theme_manager
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Splitter: left = theme list, right = editor
        splitter = QSplitter(Qt.Horizontal)

        # Left panel: theme list
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_layout.addWidget(QLabel(self.tr("Available Themes:")))

        self.list_themes = QListWidget()
        for name in self._tm.available_themes:
            item = QListWidgetItem(name)
            is_builtin = self._tm.is_builtin_theme(name)
            item.setToolTip(
                self.tr("{type} theme\n{description}").format(
                    type=self.tr("Built-in") if is_builtin else self.tr("Custom"),
                    description=self._tm.get_theme_description(name)
                )
            )
            if not is_builtin:
                # Mark custom themes with a different font style
                f = item.font()
                f.setItalic(True)
                item.setFont(f)
            self.list_themes.addItem(item)

        self.list_themes.currentTextChanged.connect(self._on_theme_selected)
        left_layout.addWidget(self.list_themes)

        # Theme action buttons
        btn_layout = QHBoxLayout()
        self.btn_new = QPushButton(self.tr("➕ New"))
        self.btn_new.clicked.connect(self._new_theme)
        btn_layout.addWidget(self.btn_new)

        self.btn_save = QPushButton(self.tr("💾 Save"))
        self.btn_save.clicked.connect(self._save_theme)
        btn_layout.addWidget(self.btn_save)

        self.btn_delete = QPushButton(self.tr("🗑 Delete"))
        self.btn_delete.clicked.connect(self._delete_theme)
        btn_layout.addWidget(self.btn_delete)

        self.btn_import = QPushButton(self.tr("📂 Import .qss"))
        self.btn_import.clicked.connect(self._import_theme)
        btn_layout.addWidget(self.btn_import)

        self.btn_export = QPushButton(self.tr("📤 Export .qss"))
        self.btn_export.clicked.connect(self._export_theme)
        btn_layout.addWidget(self.btn_export)

        left_layout.addLayout(btn_layout)

        # Right panel: editor
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)

        form = QFormLayout()
        self.lbl_name = QLabel(self.tr("Theme name:"))
        self.txt_name = QLineEdit()
        self.txt_name.setPlaceholderText(self.tr("MyCustomTheme"))
        form.addRow(self.lbl_name, self.txt_name)

        self.lbl_desc = QLabel(self.tr("Description:"))
        self.txt_desc = QLineEdit()
        self.txt_desc.setPlaceholderText(self.tr("A warm dark theme with purple accents"))
        form.addRow(self.lbl_desc, self.txt_desc)

        right_layout.addLayout(form)

        right_layout.addWidget(QLabel(self.tr("Stylesheet (QSS):")))

        self.txt_stylesheet = QTextEdit()
        self.txt_stylesheet.setPlaceholderText(self.tr("/* Enter QSS stylesheet here */\nQMainWindow { background-color: #1e1e2e; color: #cdd6f4; }"))
        self.txt_stylesheet.setLineWrapMode(QTextEdit.NoWrap)
        right_layout.addWidget(self.txt_stylesheet)

        # Preview button
        self.btn_preview = QPushButton(self.tr("👁 Preview"))
        self.btn_preview.clicked.connect(self._preview_theme)
        right_layout.addWidget(self.btn_preview)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([250, 650])

        layout.addWidget(splitter)

        # Dialog buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Select first theme
        if self._tm.available_themes:
            self.list_themes.setCurrentRow(0)

    def _on_theme_selected(self, name: str):
        """Load theme into editor when selected."""
        if not name:
            return
        stylesheet = self._tm.get_stylesheet(name) or ""
        self.txt_name.setText(name)
        self.txt_name.setReadOnly(self._tm.is_builtin_theme(name))
        self.txt_desc.setText(self._tm.get_theme_description(name))
        self.txt_desc.setReadOnly(True)
        self.txt_stylesheet.setPlainText(stylesheet)

        # Enable/disable buttons based on theme type
        is_builtin = self._tm.is_builtin_theme(name)
        self.btn_delete.setEnabled(not is_builtin)
        self.btn_save.setEnabled(not is_builtin)

    @Slot()
    def _new_theme(self):
        """Create a new theme from scratch."""
        name, ok = QInputDialog.getText(self, self.tr("New Theme"), self.tr("Theme name:"))
        if ok and name:
            # Validate name
            if not name.replace(' ', '').replace('-', '').replace('_', '').isalnum():
                QMessageBox.warning(self, self.tr("Invalid Name"), self.tr("Theme name can only contain letters, numbers, spaces, hyphens, and underscores."))
                return
            if self._tm.get_stylesheet(name):
                QMessageBox.warning(self, self.tr("Name Exists"), self.tr("A theme named '{name}' already exists.").format(name=name))
                return

            # Start with the current theme as a base
            base = self._tm.get_stylesheet(self._tm.current_theme) or ""
            self.txt_name.setText(name)
            self.txt_name.setReadOnly(False)
            self.txt_desc.setText("")
            self.txt_desc.setReadOnly(False)
            self.txt_stylesheet.setPlainText(base)
            self.btn_save.setEnabled(True)
            self.btn_delete.setEnabled(True)

            # Add to list temporarily (won't be saved until user clicks Save)
            self.list_themes.addItem(self.tr("✏️ {name} (unsaved)").format(name=name))

    @Slot()
    def _save_theme(self):
        """Save the current theme."""
        name = self.txt_name.text().strip()
        if not name:
            QMessageBox.warning(self, self.tr("Save Theme"), self.tr("Please enter a theme name."))
            return

        stylesheet = self.txt_stylesheet.toPlainText()
        if not stylesheet.strip():
            QMessageBox.warning(self, self.tr("Save Theme"), self.tr("Stylesheet cannot be empty."))
            return

        if self._tm.save_custom_theme(name, stylesheet):
            QMessageBox.information(self, self.tr("Theme Saved"), self.tr("Theme '{name}' saved successfully.").format(name=name))
            # Refresh list
            self._refresh_list()
            # Select the saved theme
            idx = self.list_themes.findItems(name, Qt.MatchExactly)
            if idx:
                self.list_themes.setCurrentItem(idx[0])
        else:
            QMessageBox.critical(self, self.tr("Save Failed"), self.tr("Could not save theme. Check logs for details."))

    @Slot()
    def _delete_theme(self):
        """Delete the selected custom theme."""
        name = self.txt_name.text().strip()
        if self._tm.is_builtin_theme(name):
            QMessageBox.information(self, self.tr("Cannot Delete"), self.tr("Built-in themes cannot be deleted."))
            return

        reply = QMessageBox.question(
            self, self.tr("Delete Theme"),
            self.tr("Delete custom theme '{name}'?").format(name=name),
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            if self._tm.delete_custom_theme(name):
                self._refresh_list()
            else:
                QMessageBox.critical(self, self.tr("Delete Failed"), self.tr("Could not delete theme."))

    @Slot()
    def _import_theme(self):
        """Import a .qss file as a new theme."""
        from PySide6.QtWidgets import QFileDialog
        file_path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Import QSS Theme"), "",
            self.tr("QSS Files (*.qss *.QSS);;All Files (*)")
        )
        if not file_path:
            return

        try:
            from pathlib import Path
            content = Path(file_path).read_text(encoding="utf-8")
            name = Path(file_path).stem

            # If name already exists, append suffix
            if self._tm.get_stylesheet(name):
                suffix = 1
                while self._tm.get_stylesheet(f"{name}_{suffix}"):
                    suffix += 1
                name = f"{name}_{suffix}"

            self._tm.save_custom_theme(name, content)
            self._refresh_list()
            QMessageBox.information(self, self.tr("Import Successful"), self.tr("Imported as '{name}'").format(name=name))
        except Exception as e:
            QMessageBox.critical(self, self.tr("Import Failed"), self.tr("Could not import file: {error}").format(error=e))

    @Slot()
    def _export_theme(self):
        """Export current theme to a .qss file."""
        from PySide6.QtWidgets import QFileDialog
        name = self.txt_name.text().strip()
        if not name:
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Export Theme"), f"{name}.qss",
            self.tr("QSS Files (*.qss *.QSS);;All Files (*)")
        )
        if not file_path:
            return

        try:
            Path(file_path).write_text(self.txt_stylesheet.toPlainText(), encoding="utf-8")
            QMessageBox.information(self, self.tr("Export Successful"), self.tr("Exported to {path}").format(path=file_path))
        except Exception as e:
            QMessageBox.critical(self, self.tr("Export Failed"), self.tr("Could not export: {error}").format(error=e))

    @Slot()
    def _preview_theme(self):
        """Preview theme by applying it to the app temporarily."""
        stylesheet = self.txt_stylesheet.toPlainText()
        if not stylesheet.strip():
            QMessageBox.warning(self, self.tr("Preview"), self.tr("No stylesheet to preview."))
            return

        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            # Save current
            old = app.styleSheet()
            # Apply preview
            app.setStyleSheet(stylesheet)
            QMessageBox.information(
                self, self.tr("Preview Active"),
                self.tr("Theme preview applied. Click OK to revert.")
            )
            # Revert
            app.setStyleSheet(old)

    def _refresh_list(self):
        """Refresh the theme list widget."""
        self.list_themes.clear()
        for name in self._tm.available_themes:
            item = QListWidgetItem(name)
            is_builtin = self._tm.is_builtin_theme(name)
            item.setToolTip(
                self.tr("{type} theme\n{description}").format(
                    type=self.tr("Built-in") if is_builtin else self.tr("Custom"),
                    description=self._tm.get_theme_description(name)
                )
            )
            if not is_builtin:
                f = item.font()
                f.setItalic(True)
                item.setFont(f)
            self.list_themes.addItem(item)


