"""
Quick dialog to add protected terms
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLineEdit, QComboBox, QPushButton, QLabel,
    QListWidget, QListWidgetItem, QMessageBox
)
from PySide6.QtCore import Qt, Slot
from gui.term_protector import ProtectedTerm

class QuickAddTermDialog(QDialog):
    """Quick dialog to add protected terms from selected text."""
    
    CATEGORIES = ['company', 'faction', 'location', 'character', 'item', 
                  'system', 'resource', 'skill', 'ui', 'custom']
    
    def __init__(self, suggested_terms: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Add Protected Terms"))
        self.setMinimumWidth(500)
        self.suggested_terms = suggested_terms
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Info
        info = QLabel(self.tr("Detected potential company/faction names. Select and add to protection list:"))
        layout.addWidget(info)
        
        # List of suggested terms
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.MultiSelection)
        
        for term in self.suggested_terms:
            item = QListWidgetItem(term)
            item.setCheckState(Qt.Checked)
            self.list_widget.addItem(item)
        
        layout.addWidget(self.list_widget)
        
        # Category selection
        category_layout = QHBoxLayout()
        category_layout.addWidget(QLabel(self.tr("Category:")))
        self.combo_category = QComboBox()
        for cat in self.CATEGORIES:
            self.combo_category.addItem(self.tr(cat), cat)
        self.combo_category.setCurrentIndex(self.combo_category.findData('company'))
        category_layout.addWidget(self.combo_category)
        category_layout.addStretch()
        layout.addLayout(category_layout)
        
        # Buttons
        btn_layout = QHBoxLayout()
        
        self.btn_add = QPushButton(self.tr("Add Selected"))
        self.btn_add.clicked.connect(self.accept)
        self.btn_add.setProperty("primary", True)
        self.btn_add.setStyleSheet("padding: 8px 16px;")
        btn_layout.addWidget(self.btn_add)
        
        self.btn_skip = QPushButton(self.tr("Skip"))
        self.btn_skip.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_skip)
        
        layout.addLayout(btn_layout)
    
    def get_selected_terms(self) -> list:
        """Get selected terms with category."""
        category = self.combo_category.currentData()
        terms = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.Checked:
                terms.append(ProtectedTerm(
                    term=item.text(),
                    category=category,
                    case_sensitive=True
                ))
        return terms
