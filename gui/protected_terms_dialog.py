"""
Dialog for viewing and editing protected terms
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QLabel, QLineEdit, QComboBox,
    QFileDialog, QDialogButtonBox
)
from typing import Optional
from PySide6.QtCore import Qt, Slot
from gui.term_protector import TermProtector, ProtectedTerm
from pathlib import Path


class ProtectedTermsDialog(QDialog):
    """Dialog for managing protected terms."""
    
    CATEGORIES = ['location', 'character', 'item', 'faction', 'system', 
                  'ui', 'resource', 'skill', 'ship', 'custom']
    
    def __init__(self, settings, parent=None, term_protector: Optional[TermProtector] = None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Protected Terms Manager"))
        self.setMinimumSize(800, 600)
        self._settings = settings
        self._term_protector = term_protector  # Shared instance from main window
        self._terms: list[ProtectedTerm] = []
        self._setup_ui()
        self._load_terms()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Info label
        info = QLabel(self.tr("Protected terms will NOT be translated. Add game-specific names, locations, items, etc."))
        info.setWordWrap(True)
        layout.addWidget(info)
        
        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels([
            self.tr('Term'), 
            self.tr('Category'), 
            self.tr('Case Sensitive')
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)
        
        # Add term controls
        add_layout = QHBoxLayout()
        add_layout.addWidget(QLabel(self.tr("Add new term:")))
        self.new_term_input = QLineEdit()
        self.new_term_input.setPlaceholderText(self.tr("Enter term to protect..."))
        add_layout.addWidget(self.new_term_input)
        
        self.new_term_category = QComboBox()
        for cat in self.CATEGORIES:
            self.new_term_category.addItem(self.tr(cat), cat)
        add_layout.addWidget(self.new_term_category)
        
        self.btn_add = QPushButton(self.tr("Add"))
        self.btn_add.clicked.connect(self._add_term)
        add_layout.addWidget(self.btn_add)
        
        layout.addLayout(add_layout)
        
        # Buttons
        btn_layout = QHBoxLayout()
        
        self.btn_import = QPushButton(self.tr("Import from File..."))
        self.btn_import.clicked.connect(self._import_terms)
        btn_layout.addWidget(self.btn_import)
        
        self.btn_export = QPushButton(self.tr("Export to File..."))
        self.btn_export.clicked.connect(self._export_terms)
        btn_layout.addWidget(self.btn_export)
        
        btn_layout.addStretch()
        
        self.btn_remove = QPushButton(self.tr("Remove Selected"))
        self.btn_remove.clicked.connect(self._remove_selected)
        btn_layout.addWidget(self.btn_remove)
        
        self.btn_clear = QPushButton(self.tr("Clear All"))
        self.btn_clear.clicked.connect(self._clear_all)
        btn_layout.addWidget(self.btn_clear)
        
        layout.addLayout(btn_layout)
        
        # Dialog buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._apply_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
    
    def _load_terms(self):
        """Load protected terms from protector."""
        if self._term_protector is not None:
            # Use shared instance from main window (includes game terms + custom terms)
            self._terms = list(self._term_protector.protected_terms.values())
        else:
            # Fallback: create a fresh protector (will only have default terms)
            protector = TermProtector()
            self._terms = list(protector.protected_terms.values())
        self._refresh_table()
    
    def _refresh_table(self):
        """Refresh table with current terms."""
        self.table.setRowCount(0)
        
        for term in sorted(self._terms, key=lambda t: t.term):
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            # Term
            term_item = QTableWidgetItem(term.term)
            term_item.setFlags(term_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, term_item)
            
            # Category
            category_item = QTableWidgetItem(self.tr(term.category))
            category_item.setFlags(category_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 1, category_item)
            
            # Case sensitive
            case_item = QTableWidgetItem(self.tr("Yes") if term.case_sensitive else self.tr("No"))
            case_item.setFlags(case_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 2, case_item)
    
    @Slot()
    def _apply_and_accept(self):
        """Sync edited terms back to the live TermProtector, then close."""
        if self._term_protector is not None:
            new_keys = {t.term for t in self._terms}
            # Remove terms the user deleted
            for term in list(self._term_protector.protected_terms.keys()):
                if term not in new_keys:
                    self._term_protector.remove_term(term)
            # Add terms the user added
            for t in self._terms:
                if t.term not in self._term_protector.protected_terms:
                    self._term_protector.add_protected_term(t)
        self.accept()

    @Slot()
    def _add_term(self):
        """Add new protected term."""
        term_text = self.new_term_input.text().strip()
        if not term_text:
            QMessageBox.warning(self, self.tr("Invalid Input"), self.tr("Please enter a term to protect."))
            return
        
        category = self.new_term_category.currentData()
        
        # Check if already exists
        if any(t.term == term_text for t in self._terms):
            QMessageBox.warning(self, self.tr("Duplicate"), self.tr("Term '{term}' is already protected.").format(term=term_text))
            return
        
        # Add term
        self._terms.append(ProtectedTerm(
            term=term_text,
            category=category,
            case_sensitive=True
        ))
        
        self.new_term_input.clear()
        self._refresh_table()
    
    @Slot()
    def _remove_selected(self):
        """Remove selected terms."""
        selected_rows = set(item.row() for item in self.table.selectedItems())
        if not selected_rows:
            QMessageBox.information(self, self.tr("No Selection"), self.tr("Please select terms to remove."))
            return
        
        # Remove from list
        rows_to_remove = sorted(selected_rows, reverse=True)
        for row in rows_to_remove:
            _item = self.table.item(row, 0)
            if _item is None:
                continue
            term_text = _item.text()
            self._terms = [t for t in self._terms if t.term != term_text]
        
        self._refresh_table()
    
    @Slot()
    def _clear_all(self):
        """Clear all custom terms (keep defaults)."""
        reply = QMessageBox.question(
            self, self.tr("Clear All"),
            self.tr("Remove all custom protected terms? Default terms will be kept."),
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self._terms = [
                ProtectedTerm(term=t, category=cat, case_sensitive=True)
                for t, cat in TermProtector.DEFAULT_PROTECTED_TERMS
            ]
            self._refresh_table()
    
    @Slot()
    def _import_terms(self):
        """Import terms from file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Import Protected Terms"), "",
            self.tr("Text Files (*.txt *.TXT);;All Files (*)")
        )
        if not file_path:
            return
        
        try:
            protector = TermProtector()
            protector.load_custom_terms(Path(file_path))
            
            # Merge with existing
            existing_terms = {t.term for t in self._terms}
            for term in protector.protected_terms.values():
                if term.term not in existing_terms:
                    self._terms.append(term)
            
            self._refresh_table()
            QMessageBox.information(self, self.tr("Import Successful"), 
                                  self.tr("Imported terms from {path}").format(path=file_path))
        
        except Exception as e:
            QMessageBox.critical(self, self.tr("Import Failed"), self.tr("Failed to import: {error}").format(error=e))
    
    @Slot()
    def _export_terms(self):
        """Export terms to file."""
        file_path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Export Protected Terms"), "protected_terms.txt",
            self.tr("Text Files (*.txt *.TXT);;All Files (*)")
        )
        if not file_path:
            return
        
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write("# Protected Terms\n")
                f.write("# Format: Term,Category\n\n")
                for term in sorted(self._terms, key=lambda t: t.term):
                    f.write(f"{term.term},{term.category}\n")
            
            QMessageBox.information(self, self.tr("Export Successful"),
                                  self.tr("Exported {count} terms to {path}").format(count=len(self._terms), path=file_path))
        
        except Exception as e:
            QMessageBox.critical(self, self.tr("Export Failed"), self.tr("Failed to export: {error}").format(error=e))
