"""
Advanced batch translation dialog with filtering and preview
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QProgressBar, QLabel, QCheckBox,
    QGroupBox, QFormLayout, QTextEdit, QDialogButtonBox,
    QSpinBox
)
from PySide6.QtCore import Qt, Slot, Signal
from bethesda_strings import BethesdaStringFile
from gui.string_table import StringTableModel
from gui.ollama_worker import TranslationRequest


class TranslationDialog(QDialog):
    """Advanced dialog for batch translation with filtering options."""
    
    def __init__(self, file: BethesdaStringFile, model: StringTableModel, 
                 settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Batch Translation"))
        self.setMinimumSize(900, 600)
        
        self.file = file
        self.model = model
        self.settings = settings
        
        self._setup_ui()
        
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Filters
        filter_group = QGroupBox(self.tr("Filter Strings"))
        filter_layout = QFormLayout()
        
        self.chk_empty_only = QCheckBox(self.tr("Only untranslated strings"))
        self.chk_empty_only.setChecked(True)
        filter_layout.addRow(self.chk_empty_only)
        
        self.chk_min_length = QCheckBox(self.tr("Minimum length:"))
        self.spin_min_length = QSpinBox()
        self.spin_min_length.setRange(1, 500)
        self.spin_min_length.setValue(10)
        self.spin_min_length.setEnabled(False)
        self.chk_min_length.toggled.connect(self.spin_min_length.setEnabled)
        length_layout = QHBoxLayout()
        length_layout.addWidget(self.chk_min_length)
        length_layout.addWidget(self.spin_min_length)
        filter_layout.addRow(length_layout)
        
        self.chk_max_length = QCheckBox(self.tr("Maximum length:"))
        self.spin_max_length = QSpinBox()
        self.spin_max_length.setRange(10, 5000)
        self.spin_max_length.setValue(500)
        self.spin_max_length.setEnabled(False)
        self.chk_max_length.toggled.connect(self.spin_max_length.setEnabled)
        max_length_layout = QHBoxLayout()
        max_length_layout.addWidget(self.chk_max_length)
        max_length_layout.addWidget(self.spin_max_length)
        filter_layout.addRow(max_length_layout)
        
        filter_group.setLayout(filter_layout)
        layout.addWidget(filter_group)
        
        # Preview table
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels([self.tr('ID'), self.tr('Original'), self.tr('Preview')])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)
        
        # Load filtered strings
        self._populate_table()
        
        # Action buttons
        btn_layout = QHBoxLayout()
        
        self.btn_select_all = QPushButton(self.tr("Select All"))
        self.btn_select_all.clicked.connect(self.table.selectAll)
        btn_layout.addWidget(self.btn_select_all)
        
        self.btn_clear = QPushButton(self.tr("Clear Selection"))
        self.btn_clear.clicked.connect(self.table.clearSelection)
        btn_layout.addWidget(self.btn_clear)
        
        btn_layout.addStretch()
        
        self.btn_translate = QPushButton(self.tr("Translate Selected"))
        self.btn_translate.clicked.connect(self._start_translation)
        self.btn_translate.setProperty("primary", True)
        self.btn_translate.setStyleSheet("padding: 8px 16px;")
        btn_layout.addWidget(self.btn_translate)
        
        layout.addLayout(btn_layout)
        
        # Progress
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.lbl_progress = QLabel()
        progress_layout = QHBoxLayout()
        progress_layout.addWidget(self.lbl_progress)
        progress_layout.addWidget(self.progress)
        layout.addLayout(progress_layout)
        
        # Dialog buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        
    def _populate_table(self):
        """Fill table with filtered strings."""
        self.table.setRowCount(0)  # Clear existing
        
        for i, row_data in enumerate(self.model._data):
            # Apply filters
            if self.chk_empty_only.isChecked() and row_data.get('translated'):
                continue
            if self.chk_min_length.isEnabled() and row_data['length'] < self.spin_min_length.value():
                continue
            if self.chk_max_length.isEnabled() and row_data['length'] > self.spin_max_length.value():
                continue
                
            # Add to table
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            id_item = QTableWidgetItem(f"0x{row_data['id']:08X}")
            id_item.setData(Qt.UserRole, i)  # Store model index
            self.table.setItem(row, 0, id_item)
            
            orig_item = QTableWidgetItem(row_data['original'][:100] + '…' if len(row_data['original']) > 100 else row_data['original'])
            orig_item.setToolTip(row_data['original'])
            self.table.setItem(row, 1, orig_item)
            
            preview_item = QTableWidgetItem(row_data.get('translated', '') or '—')
            preview_item.setForeground(Qt.gray if not row_data.get('translated') else Qt.black)
            self.table.setItem(row, 2, preview_item)
    
    @Slot()
    def _start_translation(self):
        """Start translation for selected rows."""
        selected_rows = [item.row() for item in self.table.selectedItems()]
        if not selected_rows:
            return
            
        # Get unique model indices
        model_indices = []
        seen = set()
        for row in selected_rows:
            item = self.table.item(row, 0)
            if item:
                model_idx = item.data(Qt.UserRole)
                if model_idx not in seen:
                    seen.add(model_idx)
                    model_indices.append(model_idx)
        
        if not model_indices:
            return
            
        # Prepare requests
        from gui.ollama_worker import TranslationRequest
        requests = []
        source = self.settings.get('default_source_lang', 'English')
        target = self.settings.get('default_target_lang', 'French')
        quality = self.settings.get('quality_level', 7)
        
        for idx in model_indices:
            row = self.model.get_row_data(idx)
            if row.get('translated'):  # Skip already translated
                continue
            requests.append(TranslationRequest(
                index=idx,
                original_text=row['original'],
                string_id=row['id'],
                source_lang=source,
                target_lang=target,
                quality_level=quality
            ))
        
        if not requests:
            return
            
        # Show progress UI
        self.progress.setVisible(True)
        self.progress.setRange(0, len(requests))
        self.progress.setValue(0)
        self.lbl_progress.setText(self.tr("Translating {current}/{total}...").format(current=0, total=len(requests)))
        self.btn_translate.setEnabled(False)
        
        # In a real app, you'd emit a signal to main window's worker
        # For this dialog, we'll just simulate
        self._simulate_translation(requests)
    
    def _simulate_translation(self, requests: list):
        """Simulate translation progress (replace with actual worker call in production)."""
        import time
        from PySide6.QtWidgets import QApplication
        
        for i, req in enumerate(requests):
            # Simulate API delay
            time.sleep(0.2)
            
            # Simulate translation (in real app: call ollama_worker)
            translated = f"[AI: {req.target_lang}] {req.original_text[:50]}..."
            
            # Update model and table
            self.model.set_translated_text(req.index, translated)
            
            # Update table preview
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                if item and item.data(Qt.UserRole) == req.index:
                    _col2 = self.table.item(row, 2)
                    if _col2 is not None:
                        _col2.setText(translated[:50] + '…')
                        _col2.setForeground(Qt.black)
                    break
            
            # Update progress
            self.progress.setValue(i + 1)
            self.lbl_progress.setText(self.tr("Translating {current}/{total}...").format(current=i+1, total=len(requests)))
            QApplication.processEvents()
        
        # Complete
        self.progress.setVisible(False)
        self.btn_translate.setEnabled(True)
        self.lbl_progress.setText(self.tr("✓ Translated {count} strings").format(count=len(requests)))
