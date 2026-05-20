"""
Theme system with built-in themes and custom theme support.
"""
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Built-in themes ───────────────────────────────────────────────

THEMES = {}

THEMES["Slate"] = """
/* Slate - Default dark theme */
QMainWindow { background-color: #1e293b; color: #f1f5f9; }
QMenuBar { background-color: #334155; color: #f1f5f9; border-bottom: 1px solid #475569; }
QMenuBar::item:selected { background-color: #475569; }
QMenu { background-color: #334155; color: #f1f5f9; border: 1px solid #475569; }
QMenu::item:selected { background-color: #475569; }
QMenu { background-color: #334155; color: #f1f5f9; }
QToolBar { background-color: #334155; border-bottom: 1px solid #475569; spacing: 4px; }
QToolBar QToolButton { color: #f1f5f9; border-radius: 4px; }
QToolBar QToolButton:hover { background-color: #475569; }
QStatusBar { background-color: #334155; color: #94a3b8; border-top: 1px solid #475569; }
QTableView { background-color: #1e293b; color: #f1f5f9; gridline-color: #334155; selection-background-color: #3b82f6; selection-color: white; alternate-background-color: #262f45; }
QHeaderView::section { background-color: #334155; color: #f1f5f9; padding: 4px; border: none; border-right: 1px solid #475569; }
QTableView::item { padding: 4px; }
QPushButton { background-color: #475569; color: #f1f5f9; border: none; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background-color: #64748b; }
QPushButton:pressed { background-color: #334155; }
QPushButton:disabled { background-color: #1e293b; color: #64748b; }
QPushButton[primary="true"] { background-color: #3b82f6; color: white; }
QPushButton[primary="true"]:hover { background-color: #60a5fa; }
QPushButton[primary="true"]:pressed { background-color: #2563eb; }
QLineEdit, QComboBox, QSpinBox { background-color: #334155; color: #f1f5f9; border: 1px solid #475569; border-radius: 4px; padding: 4px 8px; }
QComboBox QAbstractItemView { background-color: #334155; color: #f1f5f9; selection-background-color: #3b82f6; selection-color: #ffffff; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #3b82f6; }
QProgressBar { border: 1px solid #475569; border-radius: 4px; text-align: center; background-color: #334155; }
QProgressBar::chunk { background-color: #3b82f6; border-radius: 3px; }
QDialog { background-color: #1e293b; color: #f1f5f9; }
QCheckBox, QRadioButton { color: #f1f5f9; spacing: 6px; } QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; background-color: #334155; border: 2px solid #475569; border-radius: 4px; } QCheckBox::indicator:checked, QRadioButton::indicator:checked { background-color: #3b82f6; border-color: #3b82f6; image: none; } QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover { border-color: #64748b; } QRadioButton::indicator { border-radius: 9px; }
QLabel { color: #f1f5f9; }
QTableWidget { background-color: #1e293b; color: #f1f5f9; gridline-color: #334155; selection-background-color: #3b82f6; selection-color: white; alternate-background-color: #262f45; }
QGroupBox { background-color: #262f45; border: 1px solid #475569; border-radius: 6px; margin-top: 8px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; color: #94a3b8; }
QTextEdit { background-color: #334155; color: #f1f5f9; border: 1px solid #475569; border-radius: 4px; }
QScrollArea { background-color: #1e293b; border: none; }
QSplitter::handle { background-color: #475569; }
QListWidget { background-color: #1e293b; color: #f1f5f9; border: 1px solid #475569; }
QListWidget::item { padding: 2px 4px; }
QListWidget::item:selected { background-color: #3b82f6; color: white; }
QScrollBar:vertical { background-color: #1e293b; width: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background-color: #475569; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background-color: #64748b; }
QScrollBar:horizontal { background-color: #1e293b; height: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:horizontal { background-color: #475569; border-radius: 5px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background-color: #64748b; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QToolTip { background-color: #334155; color: #f1f5f9; border: 1px solid #475569; border-radius: 4px; padding: 4px 8px; }
"""

THEMES["Midnight"] = """
/* Midnight - Deep dark blue */
QMainWindow { background-color: #0f172a; color: #e2e8f0; }
QMenuBar { background-color: #1e293b; color: #e2e8f0; border-bottom: 1px solid #334155; }
QMenuBar::item:selected { background-color: #334155; }
QMenu { background-color: #1e293b; color: #e2e8f0; border: 1px solid #334155; }
QMenu::item:selected { background-color: #334155; }
QMenu { background-color: #1e293b; color: #e2e8f0; }
QToolBar { background-color: #1e293b; border-bottom: 1px solid #334155; spacing: 4px; }
QToolBar QToolButton { color: #e2e8f0; border-radius: 4px; }
QToolBar QToolButton:hover { background-color: #334155; }
QStatusBar { background-color: #1e293b; color: #64748b; border-top: 1px solid #334155; }
QTableView { background-color: #0f172a; color: #e2e8f0; gridline-color: #1e293b; selection-background-color: #2563eb; selection-color: white; alternate-background-color: #151d2e; }
QHeaderView::section { background-color: #1e293b; color: #e2e8f0; padding: 4px; border: none; border-right: 1px solid #334155; }
QTableView::item { padding: 4px; }
QPushButton { background-color: #334155; color: #e2e8f0; border: none; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background-color: #475569; }
QPushButton:pressed { background-color: #1e293b; }
QPushButton:disabled { background-color: #0f172a; color: #475569; }
QPushButton[primary="true"] { background-color: #2563eb; color: white; }
QPushButton[primary="true"]:hover { background-color: #3b82f6; }
QPushButton[primary="true"]:pressed { background-color: #1d4ed8; }
QLineEdit, QComboBox, QSpinBox { background-color: #1e293b; color: #e2e8f0; border: 1px solid #334155; border-radius: 4px; padding: 4px 8px; }
QComboBox QAbstractItemView { background-color: #1e293b; color: #e2e8f0; selection-background-color: #2563eb; selection-color: #ffffff; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #2563eb; }
QProgressBar { border: 1px solid #334155; border-radius: 4px; text-align: center; background-color: #1e293b; }
QProgressBar::chunk { background-color: #2563eb; border-radius: 3px; }
QDialog { background-color: #0f172a; color: #e2e8f0; }
QCheckBox, QRadioButton { color: #e2e8f0; spacing: 6px; } QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; background-color: #1e293b; border: 2px solid #334155; border-radius: 4px; } QCheckBox::indicator:checked, QRadioButton::indicator:checked { background-color: #2563eb; border-color: #2563eb; image: none; } QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover { border-color: #475569; } QRadioButton::indicator { border-radius: 9px; }
QLabel { color: #e2e8f0; }
QTableWidget { background-color: #0f172a; color: #e2e8f0; gridline-color: #1e293b; selection-background-color: #2563eb; selection-color: white; alternate-background-color: #151d2e; }
QGroupBox { background-color: #151d2e; border: 1px solid #334155; border-radius: 6px; margin-top: 8px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; color: #64748b; }
QTextEdit { background-color: #1e293b; color: #e2e8f0; border: 1px solid #334155; border-radius: 4px; }
QScrollArea { background-color: #0f172a; border: none; }
QSplitter::handle { background-color: #334155; }
QListWidget { background-color: #0f172a; color: #e2e8f0; border: 1px solid #334155; }
QListWidget::item { padding: 2px 4px; }
QListWidget::item:selected { background-color: #2563eb; color: white; }
QScrollBar:vertical { background-color: #0f172a; width: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background-color: #334155; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background-color: #475569; }
QScrollBar:horizontal { background-color: #0f172a; height: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:horizontal { background-color: #334155; border-radius: 5px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background-color: #475569; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QToolTip { background-color: #1e293b; color: #e2e8f0; border: 1px solid #334155; border-radius: 4px; padding: 4px 8px; }
"""

THEMES["Nord"] = """
/* Nord - Arctic blue-gray */
QMainWindow { background-color: #2e3440; color: #eceff4; }
QMenuBar { background-color: #3b4252; color: #eceff4; border-bottom: 1px solid #4c566a; }
QMenuBar::item:selected { background-color: #4c566a; }
QMenu { background-color: #3b4252; color: #eceff4; border: 1px solid #4c566a; }
QMenu::item:selected { background-color: #4c566a; }
QMenu { background-color: #3b4252; color: #eceff4; }
QToolBar { background-color: #3b4252; border-bottom: 1px solid #4c566a; spacing: 4px; }
QToolBar QToolButton { color: #eceff4; border-radius: 4px; }
QToolBar QToolButton:hover { background-color: #4c566a; }
QStatusBar { background-color: #3b4252; color: #d8dee9; border-top: 1px solid #4c566a; }
QTableView { background-color: #2e3440; color: #eceff4; gridline-color: #3b4252; selection-background-color: #5e81ac; selection-color: #eceff4; alternate-background-color: #353d4a; }
QHeaderView::section { background-color: #3b4252; color: #eceff4; padding: 4px; border: none; border-right: 1px solid #4c566a; }
QTableView::item { padding: 4px; }
QPushButton { background-color: #4c566a; color: #eceff4; border: none; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background-color: #5e81ac; }
QPushButton:pressed { background-color: #3b4252; }
QPushButton:disabled { background-color: #2e3440; color: #4c566a; }
QPushButton[primary="true"] { background-color: #5e81ac; color: #eceff4; }
QPushButton[primary="true"]:hover { background-color: #81a1c1; }
QPushButton[primary="true"]:pressed { background-color: #4c6f90; }
QLineEdit, QComboBox, QSpinBox { background-color: #3b4252; color: #eceff4; border: 1px solid #4c566a; border-radius: 4px; padding: 4px 8px; }
QComboBox QAbstractItemView { background-color: #3b4252; color: #eceff4; selection-background-color: #5e81ac; selection-color: #ffffff; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #5e81ac; }
QProgressBar { border: 1px solid #4c566a; border-radius: 4px; text-align: center; background-color: #3b4252; }
QProgressBar::chunk { background-color: #5e81ac; border-radius: 3px; }
QDialog { background-color: #2e3440; color: #eceff4; }
QCheckBox, QRadioButton { color: #eceff4; spacing: 6px; } QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; background-color: #3b4252; border: 2px solid #4c566a; border-radius: 4px; } QCheckBox::indicator:checked, QRadioButton::indicator:checked { background-color: #5e81ac; border-color: #5e81ac; image: none; } QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover { border-color: #64748b; } QRadioButton::indicator { border-radius: 9px; }
QLabel { color: #eceff4; }
QTableWidget { background-color: #2e3440; color: #eceff4; gridline-color: #3b4252; selection-background-color: #5e81ac; selection-color: #eceff4; alternate-background-color: #353d4a; }
QGroupBox { background-color: #353d4a; border: 1px solid #4c566a; border-radius: 6px; margin-top: 8px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; color: #d8dee9; }
QTextEdit { background-color: #3b4252; color: #eceff4; border: 1px solid #4c566a; border-radius: 4px; }
QScrollArea { background-color: #2e3440; border: none; }
QSplitter::handle { background-color: #4c566a; }
QListWidget { background-color: #2e3440; color: #eceff4; border: 1px solid #4c566a; }
QListWidget::item { padding: 2px 4px; }
QListWidget::item:selected { background-color: #5e81ac; color: #eceff4; }
QScrollBar:vertical { background-color: #2e3440; width: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background-color: #4c566a; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background-color: #5e81ac; }
QScrollBar:horizontal { background-color: #2e3440; height: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:horizontal { background-color: #4c566a; border-radius: 5px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background-color: #5e81ac; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QToolTip { background-color: #3b4252; color: #eceff4; border: 1px solid #4c566a; border-radius: 4px; padding: 4px 8px; }
"""

THEMES["Dracula"] = """
/* Dracula - Purple-accented dark */
QMainWindow { background-color: #282a36; color: #f8f8f2; }
QMenuBar { background-color: #343746; color: #f8f8f2; border-bottom: 1px solid #44475a; }
QMenuBar::item:selected { background-color: #44475a; }
QMenu { background-color: #343746; color: #f8f8f2; border: 1px solid #44475a; }
QMenu::item:selected { background-color: #44475a; }
QMenu { background-color: #343746; color: #f8f8f2; }
QToolBar { background-color: #343746; border-bottom: 1px solid #44475a; spacing: 4px; }
QToolBar QToolButton { color: #f8f8f2; border-radius: 4px; }
QToolBar QToolButton:hover { background-color: #44475a; }
QStatusBar { background-color: #343746; color: #bd93f9; border-top: 1px solid #44475a; }
QTableView { background-color: #282a36; color: #f8f8f2; gridline-color: #343746; selection-background-color: #bd93f9; selection-color: #282a36; alternate-background-color: #2d2f3d; }
QHeaderView::section { background-color: #343746; color: #f8f8f2; padding: 4px; border: none; border-right: 1px solid #44475a; }
QTableView::item { padding: 4px; }
QPushButton { background-color: #44475a; color: #f8f8f2; border: none; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background-color: #6272a4; }
QPushButton:pressed { background-color: #343746; }
QPushButton:disabled { background-color: #282a36; color: #44475a; }
QPushButton[primary="true"] { background-color: #bd93f9; color: #282a36; }
QPushButton[primary="true"]:hover { background-color: #caa9fa; }
QPushButton[primary="true"]:pressed { background-color: #a875f8; }
QLineEdit, QComboBox, QSpinBox { background-color: #343746; color: #f8f8f2; border: 1px solid #44475a; border-radius: 4px; padding: 4px 8px; }
QComboBox QAbstractItemView { background-color: #343746; color: #f8f8f2; selection-background-color: #bd93f9; selection-color: #ffffff; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #bd93f9; }
QProgressBar { border: 1px solid #44475a; border-radius: 4px; text-align: center; background-color: #343746; }
QProgressBar::chunk { background-color: #bd93f9; border-radius: 3px; }
QDialog { background-color: #282a36; color: #f8f8f2; }
QCheckBox, QRadioButton { color: #f8f8f2; spacing: 6px; } QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; background-color: #343746; border: 2px solid #44475a; border-radius: 4px; } QCheckBox::indicator:checked, QRadioButton::indicator:checked { background-color: #bd93f9; border-color: #bd93f9; image: none; } QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover { border-color: #6272a4; } QRadioButton::indicator { border-radius: 9px; }
QLabel { color: #f8f8f2; }
QTableWidget { background-color: #282a36; color: #f8f8f2; gridline-color: #343746; selection-background-color: #bd93f9; selection-color: #282a36; alternate-background-color: #2d2f3d; }
QGroupBox { background-color: #2d2f3d; border: 1px solid #44475a; border-radius: 6px; margin-top: 8px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; color: #bd93f9; }
QTextEdit { background-color: #343746; color: #f8f8f2; border: 1px solid #44475a; border-radius: 4px; }
QScrollArea { background-color: #282a36; border: none; }
QSplitter::handle { background-color: #44475a; }
QListWidget { background-color: #282a36; color: #f8f8f2; border: 1px solid #44475a; }
QListWidget::item { padding: 2px 4px; }
QListWidget::item:selected { background-color: #bd93f9; color: #282a36; }
QScrollBar:vertical { background-color: #282a36; width: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background-color: #44475a; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background-color: #6272a4; }
QScrollBar:horizontal { background-color: #282a36; height: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:horizontal { background-color: #44475a; border-radius: 5px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background-color: #6272a4; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QToolTip { background-color: #343746; color: #f8f8f2; border: 1px solid #44475a; border-radius: 4px; padding: 4px 8px; }
"""

THEMES["Catppuccin"] = """
/* Catppuccin Mocha - Warm dark theme */
QMainWindow { background-color: #1e1e2e; color: #cdd6f4; }
QMenuBar { background-color: #313244; color: #cdd6f4; border-bottom: 1px solid #45475a; }
QMenuBar::item:selected { background-color: #45475a; }
QMenu { background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; }
QMenu::item:selected { background-color: #45475a; }
QMenu { background-color: #313244; color: #cdd6f4; }
QToolBar { background-color: #313244; border-bottom: 1px solid #45475a; spacing: 4px; }
QToolBar QToolButton { color: #cdd6f4; border-radius: 4px; }
QToolBar QToolButton:hover { background-color: #45475a; }
QStatusBar { background-color: #313244; color: #a6adc8; border-top: 1px solid #45475a; }
QTableView { background-color: #1e1e2e; color: #cdd6f4; gridline-color: #313244; selection-background-color: #89b4fa; selection-color: #1e1e2e; alternate-background-color: #262638; }
QHeaderView::section { background-color: #313244; color: #cdd6f4; padding: 4px; border: none; border-right: 1px solid #45475a; }
QTableView::item { padding: 4px; }
QPushButton { background-color: #45475a; color: #cdd6f4; border: none; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background-color: #585b70; }
QPushButton:pressed { background-color: #313244; }
QPushButton:disabled { background-color: #1e1e2e; color: #585b70; }
QPushButton[primary="true"] { background-color: #89b4fa; color: #1e1e2e; }
QPushButton[primary="true"]:hover { background-color: #a5c6fc; }
QPushButton[primary="true"]:pressed { background-color: #6d9fef; }
QLineEdit, QComboBox, QSpinBox { background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; border-radius: 4px; padding: 4px 8px; }
QComboBox QAbstractItemView { background-color: #313244; color: #cdd6f4; selection-background-color: #89b4fa; selection-color: #ffffff; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #89b4fa; }
QProgressBar { border: 1px solid #45475a; border-radius: 4px; text-align: center; background-color: #313244; }
QProgressBar::chunk { background-color: #89b4fa; border-radius: 3px; }
QDialog { background-color: #1e1e2e; color: #cdd6f4; }
QCheckBox, QRadioButton { color: #cdd6f4; spacing: 6px; } QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; background-color: #313244; border: 2px solid #45475a; border-radius: 4px; } QCheckBox::indicator:checked, QRadioButton::indicator:checked { background-color: #89b4fa; border-color: #89b4fa; image: none; } QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover { border-color: #585b70; } QRadioButton::indicator { border-radius: 9px; }
QLabel { color: #cdd6f4; }
QTableWidget { background-color: #1e1e2e; color: #cdd6f4; gridline-color: #313244; selection-background-color: #89b4fa; selection-color: #1e1e2e; alternate-background-color: #262638; }
QGroupBox { background-color: #262638; border: 1px solid #45475a; border-radius: 6px; margin-top: 8px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; color: #a6adc8; }
QTextEdit { background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; border-radius: 4px; }
QScrollArea { background-color: #1e1e2e; border: none; }
QSplitter::handle { background-color: #45475a; }
QListWidget { background-color: #1e1e2e; color: #cdd6f4; border: 1px solid #45475a; }
QListWidget::item { padding: 2px 4px; }
QListWidget::item:selected { background-color: #89b4fa; color: #1e1e2e; }
QScrollBar:vertical { background-color: #1e1e2e; width: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background-color: #45475a; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background-color: #585b70; }
QScrollBar:horizontal { background-color: #1e1e2e; height: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:horizontal { background-color: #45475a; border-radius: 5px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background-color: #585b70; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QToolTip { background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; border-radius: 4px; padding: 4px 8px; }
"""

THEMES["Light"] = """
/* Light - Clean light theme */
QMainWindow { background-color: #f8fafc; color: #1e293b; }
QMenuBar { background-color: #ffffff; color: #1e293b; border-bottom: 1px solid #e2e8f0; }
QMenuBar::item:selected { background-color: #f1f5f9; }
QMenu { background-color: #ffffff; color: #1e293b; border: 1px solid #e2e8f0; }
QMenu::item:selected { background-color: #f1f5f9; }
QMenu { background-color: #ffffff; color: #1e293b; }
QToolBar { background-color: #ffffff; border-bottom: 1px solid #e2e8f0; spacing: 4px; }
QToolBar QToolButton { color: #1e293b; border-radius: 4px; }
QToolBar QToolButton:hover { background-color: #f1f5f9; }
QStatusBar { background-color: #ffffff; color: #64748b; border-top: 1px solid #e2e8f0; }
QTableView { background-color: #ffffff; color: #1e293b; gridline-color: #e2e8f0; selection-background-color: #3b82f6; selection-color: white; alternate-background-color: #f8fafc; }
QHeaderView::section { background-color: #f1f5f9; color: #1e293b; padding: 4px; border: none; border-right: 1px solid #e2e8f0; }
QTableView::item { padding: 4px; }
QPushButton { background-color: #e2e8f0; color: #1e293b; border: none; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background-color: #cbd5e1; }
QPushButton:pressed { background-color: #94a3b8; }
QPushButton:disabled { background-color: #f1f5f9; color: #94a3b8; }
QPushButton[primary="true"] { background-color: #3b82f6; color: white; }
QPushButton[primary="true"]:hover { background-color: #60a5fa; }
QPushButton[primary="true"]:pressed { background-color: #2563eb; }
QLineEdit, QComboBox, QSpinBox { background-color: #ffffff; color: #1e293b; border: 1px solid #cbd5e1; border-radius: 4px; padding: 4px 8px; }
QComboBox QAbstractItemView { background-color: #ffffff; color: #1e293b; selection-background-color: #3b82f6; selection-color: #ffffff; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #3b82f6; }
QProgressBar { border: 1px solid #cbd5e1; border-radius: 4px; text-align: center; background-color: #f1f5f9; }
QProgressBar::chunk { background-color: #3b82f6; border-radius: 3px; }
QDialog { background-color: #f8fafc; color: #1e293b; }
QCheckBox, QRadioButton { color: #1e293b; spacing: 6px; } QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; background-color: #f1f5f9; border: 2px solid #94a3b8; border-radius: 4px; } QCheckBox::indicator:checked, QRadioButton::indicator:checked { background-color: #3b82f6; border-color: #3b82f6; image: none; } QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover { border-color: #64748b; } QRadioButton::indicator { border-radius: 9px; }
QLabel { color: #1e293b; }
QTableWidget { background-color: #ffffff; color: #1e293b; gridline-color: #e2e8f0; selection-background-color: #3b82f6; selection-color: white; alternate-background-color: #f8fafc; }
QGroupBox { background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; margin-top: 8px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; color: #475569; }
QTextEdit { background-color: #ffffff; color: #1e293b; border: 1px solid #cbd5e1; border-radius: 4px; }
QScrollArea { background-color: #f8fafc; border: none; }
QSplitter::handle { background-color: #e2e8f0; }
QListWidget { background-color: #ffffff; color: #1e293b; border: 1px solid #e2e8f0; }
QListWidget::item { padding: 2px 4px; }
QListWidget::item:selected { background-color: #3b82f6; color: white; }
QScrollBar:vertical { background-color: #f1f5f9; width: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background-color: #cbd5e1; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background-color: #94a3b8; }
QScrollBar:horizontal { background-color: #f1f5f9; height: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:horizontal { background-color: #cbd5e1; border-radius: 5px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background-color: #94a3b8; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QToolTip { background-color: #ffffff; color: #1e293b; border: 1px solid #e2e8f0; border-radius: 4px; padding: 4px 8px; }
"""

THEMES["Solarized Dark"] = """
/* Solarized Dark - Low-contrast dark */
QMainWindow { background-color: #002b36; color: #839496; }
QMenuBar { background-color: #073642; color: #839496; border-bottom: 1px solid #586e75; }
QMenuBar::item:selected { background-color: #586e75; }
QMenu { background-color: #073642; color: #839496; border: 1px solid #586e75; }
QMenu::item:selected { background-color: #586e75; }
QMenu { background-color: #073642; color: #839496; }
QToolBar { background-color: #073642; border-bottom: 1px solid #586e75; spacing: 4px; }
QToolBar QToolButton { color: #839496; border-radius: 4px; }
QToolBar QToolButton:hover { background-color: #586e75; }
QStatusBar { background-color: #073642; color: #586e75; border-top: 1px solid #586e75; }
QTableView { background-color: #002b36; color: #839496; gridline-color: #073642; selection-background-color: #268bd2; selection-color: #002b36; alternate-background-color: #03333f; }
QHeaderView::section { background-color: #073642; color: #839496; padding: 4px; border: none; border-right: 1px solid #586e75; }
QTableView::item { padding: 4px; }
QPushButton { background-color: #586e75; color: #fdf6e3; border: none; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background-color: #657b83; }
QPushButton:pressed { background-color: #073642; }
QPushButton:disabled { background-color: #002b36; color: #586e75; }
QPushButton[primary="true"] { background-color: #268bd2; color: #fdf6e3; }
QPushButton[primary="true"]:hover { background-color: #2fa0f0; }
QPushButton[primary="true"]:pressed { background-color: #1a6fa3; }
QLineEdit, QComboBox, QSpinBox { background-color: #073642; color: #839496; border: 1px solid #586e75; border-radius: 4px; padding: 4px 8px; }
QComboBox QAbstractItemView { background-color: #073642; color: #839496; selection-background-color: #268bd2; selection-color: #ffffff; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #268bd2; }
QProgressBar { border: 1px solid #586e75; border-radius: 4px; text-align: center; background-color: #073642; }
QProgressBar::chunk { background-color: #268bd2; border-radius: 3px; }
QDialog { background-color: #002b36; color: #839496; }
QCheckBox, QRadioButton { color: #839496; spacing: 6px; } QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; background-color: #073642; border: 2px solid #586e75; border-radius: 4px; } QCheckBox::indicator:checked, QRadioButton::indicator:checked { background-color: #268bd2; border-color: #268bd2; image: none; } QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover { border-color: #839496; } QRadioButton::indicator { border-radius: 9px; }
QLabel { color: #839496; }
QTableWidget { background-color: #002b36; color: #839496; gridline-color: #073642; selection-background-color: #268bd2; selection-color: #002b36; alternate-background-color: #03333f; }
QGroupBox { background-color: #03333f; border: 1px solid #586e75; border-radius: 6px; margin-top: 8px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; color: #586e75; }
QTextEdit { background-color: #073642; color: #839496; border: 1px solid #586e75; border-radius: 4px; }
QScrollArea { background-color: #002b36; border: none; }
QSplitter::handle { background-color: #586e75; }
QListWidget { background-color: #002b36; color: #839496; border: 1px solid #586e75; }
QListWidget::item { padding: 2px 4px; }
QListWidget::item:selected { background-color: #268bd2; color: #002b36; }
QScrollBar:vertical { background-color: #002b36; width: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background-color: #586e75; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background-color: #657b83; }
QScrollBar:horizontal { background-color: #002b36; height: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:horizontal { background-color: #586e75; border-radius: 5px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background-color: #657b83; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QToolTip { background-color: #073642; color: #839496; border: 1px solid #586e75; border-radius: 4px; padding: 4px 8px; }
"""


# ─── Theme manager ─────────────────────────────────────────────────

class ThemeManager:
    """Manages theme loading, switching, and custom themes."""

    THEMES_DIR_NAME = "themes"
    AUTO_THEME = "Auto (System)"

    # Which built-in themes to use for auto-follow (dark/light OS preference)
    AUTO_DARK_THEME  = "Slate"
    AUTO_LIGHT_THEME = "Light"

    def __init__(self):
        self._themes: dict[str, str] = dict(THEMES)  # Copy built-ins
        self._current_theme: str = "Slate"
        self._custom_dir: Optional[Path] = None
        self._load_custom_themes()

    @property
    def available_themes(self) -> list[str]:
        """Return sorted list of all available theme names, with Auto first."""
        return [self.AUTO_THEME] + sorted(self._themes.keys())

    def resolve_auto(self) -> str:
        """Return the concrete theme name for the current system color scheme.

        Requires Qt 6.5+; falls back to AUTO_DARK_THEME on older Qt or when
        the color scheme cannot be determined.
        """
        try:
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import Qt
            app = QApplication.instance()
            if app is None:
                return self.AUTO_DARK_THEME
            scheme = app.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Light:
                return self.AUTO_LIGHT_THEME
            return self.AUTO_DARK_THEME
        except Exception:
            return self.AUTO_DARK_THEME

    def effective_theme(self, name: str) -> str:
        """Resolve ``name`` to a concrete theme name (handles AUTO_THEME)."""
        if name == self.AUTO_THEME:
            return self.resolve_auto()
        return name if name in self._themes else "Slate"

    @property
    def current_theme(self) -> str:
        return self._current_theme

    @property
    def custom_dir(self) -> Path:
        if self._custom_dir is None:
            self._custom_dir = Path(__file__).parent.parent / self.THEMES_DIR_NAME
        return self._custom_dir

    def get_stylesheet(self, name: str) -> Optional[str]:
        """Get stylesheet by name. Returns None if not found."""
        return self._themes.get(name)

    def set_theme(self, name: str) -> bool:
        """Set current theme by name. Returns True if found (or Auto)."""
        if name == self.AUTO_THEME or name in self._themes:
            self._current_theme = name
            logger.info(f"Theme set to: {name}")
            return True
        return False

    def get_theme_description(self, name: str) -> str:
        """Get a short description of a theme."""
        from PySide6.QtCore import QCoreApplication
        if name == self.AUTO_THEME:
            resolved = self.resolve_auto()
            return QCoreApplication.translate(
                "ThemeManager",
                "Follows the OS light/dark preference. Currently using: {theme}."
            ).format(theme=resolved)
        descriptions = {
            "Slate": QCoreApplication.translate("ThemeManager", "Default dark theme with blue accents (slate colors)"),
            "Midnight": QCoreApplication.translate("ThemeManager", "Deep dark blue, minimal contrast"),
            "Nord": QCoreApplication.translate("ThemeManager", "Arctic blue-gray palette, soft on eyes"),
            "Dracula": QCoreApplication.translate("ThemeManager", "Purple-accented dark theme"),
            "Catppuccin": QCoreApplication.translate("ThemeManager", "Warm dark theme with blue selection"),
            "Light": QCoreApplication.translate("ThemeManager", "Clean light theme with blue accents"),
            "Solarized Dark": QCoreApplication.translate("ThemeManager", "Low-contrast dark, optimized for readability"),
        }
        desc = descriptions.get(name)
        if desc:
            return desc
        # Custom themes
        if name in self._themes and name not in descriptions:
            return QCoreApplication.translate("ThemeManager", "Custom theme loaded from {path}.qss").format(path=self.custom_dir / name)
        return ""

    def is_builtin_theme(self, name: str) -> bool:
        """Check if a theme is built-in vs custom."""
        return name in THEMES

    def _load_custom_themes(self):
        """Load .qss files from the themes/ directory."""
        theme_dir = self.custom_dir
        if not theme_dir.exists():
            theme_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created custom themes directory: {theme_dir}")
            return

        count = 0
        for qss_file in sorted(theme_dir.glob("*.qss")):
            try:
                theme_name = qss_file.stem
                content = qss_file.read_text(encoding="utf-8")
                self._themes[theme_name] = content
                count += 1
                logger.info(f"Loaded custom theme: {theme_name}")
            except Exception as e:
                logger.error(f"Failed to load theme {qss_file.name}: {e}")

        if count:
            logger.info(f"Loaded {count} custom themes from {theme_dir}")

    def save_custom_theme(self, name: str, stylesheet: str) -> bool:
        """Save or update a custom theme. Returns True on success."""
        try:
            self.custom_dir.mkdir(parents=True, exist_ok=True)
            path = self.custom_dir / f"{name}.qss"
            path.write_text(stylesheet, encoding="utf-8")
            self._themes[name] = stylesheet
            logger.info(f"Saved custom theme: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to save custom theme '{name}': {e}")
            return False

    def delete_custom_theme(self, name: str) -> bool:
        """Delete a custom theme. Returns True on success."""
        if self.is_builtin_theme(name):
            return False
        try:
            path = self.custom_dir / f"{name}.qss"
            if path.exists():
                path.unlink()
            self._themes.pop(name, None)
            logger.info(f"Deleted custom theme: {name}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete custom theme '{name}': {e}")
            return False
