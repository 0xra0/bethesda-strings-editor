"""
Theme system with built-in themes and custom theme support.
"""
import logging
import sys
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

THEMES["Gruvbox"] = """
/* Gruvbox Dark - Warm retro dark with amber/orange accents */
QMainWindow { background-color: #282828; color: #ebdbb2; }
QMenuBar { background-color: #3c3836; color: #ebdbb2; border-bottom: 1px solid #504945; }
QMenuBar::item:selected { background-color: #504945; }
QMenu { background-color: #3c3836; color: #ebdbb2; border: 1px solid #504945; }
QMenu::item:selected { background-color: #504945; }
QToolBar { background-color: #3c3836; border-bottom: 1px solid #504945; spacing: 4px; }
QToolBar QToolButton { color: #ebdbb2; border-radius: 4px; }
QToolBar QToolButton:hover { background-color: #504945; }
QStatusBar { background-color: #3c3836; color: #a89984; border-top: 1px solid #504945; }
QTableView { background-color: #282828; color: #ebdbb2; gridline-color: #3c3836; selection-background-color: #d79921; selection-color: #282828; alternate-background-color: #32302f; }
QHeaderView::section { background-color: #3c3836; color: #ebdbb2; padding: 4px; border: none; border-right: 1px solid #504945; }
QTableView::item { padding: 4px; }
QPushButton { background-color: #504945; color: #ebdbb2; border: none; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background-color: #665c54; }
QPushButton:pressed { background-color: #3c3836; }
QPushButton:disabled { background-color: #282828; color: #665c54; }
QPushButton[primary="true"] { background-color: #d79921; color: #282828; }
QPushButton[primary="true"]:hover { background-color: #fabd2f; }
QPushButton[primary="true"]:pressed { background-color: #b57614; }
QLineEdit, QComboBox, QSpinBox { background-color: #3c3836; color: #ebdbb2; border: 1px solid #504945; border-radius: 4px; padding: 4px 8px; }
QComboBox QAbstractItemView { background-color: #3c3836; color: #ebdbb2; selection-background-color: #d79921; selection-color: #282828; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #d79921; }
QProgressBar { border: 1px solid #504945; border-radius: 4px; text-align: center; background-color: #3c3836; }
QProgressBar::chunk { background-color: #d79921; border-radius: 3px; }
QDialog { background-color: #282828; color: #ebdbb2; }
QCheckBox, QRadioButton { color: #ebdbb2; spacing: 6px; } QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; background-color: #3c3836; border: 2px solid #504945; border-radius: 4px; } QCheckBox::indicator:checked, QRadioButton::indicator:checked { background-color: #d79921; border-color: #d79921; image: none; } QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover { border-color: #665c54; } QRadioButton::indicator { border-radius: 9px; }
QLabel { color: #ebdbb2; }
QTableWidget { background-color: #282828; color: #ebdbb2; gridline-color: #3c3836; selection-background-color: #d79921; selection-color: #282828; alternate-background-color: #32302f; }
QGroupBox { background-color: #32302f; border: 1px solid #504945; border-radius: 6px; margin-top: 8px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; color: #a89984; }
QTextEdit { background-color: #3c3836; color: #ebdbb2; border: 1px solid #504945; border-radius: 4px; }
QScrollArea { background-color: #282828; border: none; }
QSplitter::handle { background-color: #504945; }
QListWidget { background-color: #282828; color: #ebdbb2; border: 1px solid #504945; }
QListWidget::item { padding: 2px 4px; }
QListWidget::item:selected { background-color: #d79921; color: #282828; }
QScrollBar:vertical { background-color: #282828; width: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background-color: #504945; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background-color: #665c54; }
QScrollBar:horizontal { background-color: #282828; height: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:horizontal { background-color: #504945; border-radius: 5px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background-color: #665c54; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QToolTip { background-color: #3c3836; color: #ebdbb2; border: 1px solid #504945; border-radius: 4px; padding: 4px 8px; }
"""

THEMES["Tokyo Night"] = """
/* Tokyo Night - Deep navy with blue/cyan accents */
QMainWindow { background-color: #1a1b26; color: #c0caf5; }
QMenuBar { background-color: #16161e; color: #c0caf5; border-bottom: 1px solid #292e42; }
QMenuBar::item:selected { background-color: #292e42; }
QMenu { background-color: #16161e; color: #c0caf5; border: 1px solid #292e42; }
QMenu::item:selected { background-color: #292e42; }
QToolBar { background-color: #16161e; border-bottom: 1px solid #292e42; spacing: 4px; }
QToolBar QToolButton { color: #c0caf5; border-radius: 4px; }
QToolBar QToolButton:hover { background-color: #292e42; }
QStatusBar { background-color: #16161e; color: #565f89; border-top: 1px solid #292e42; }
QTableView { background-color: #1a1b26; color: #c0caf5; gridline-color: #16161e; selection-background-color: #7aa2f7; selection-color: #1a1b26; alternate-background-color: #1e2030; }
QHeaderView::section { background-color: #16161e; color: #c0caf5; padding: 4px; border: none; border-right: 1px solid #292e42; }
QTableView::item { padding: 4px; }
QPushButton { background-color: #292e42; color: #c0caf5; border: none; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background-color: #3d59a1; }
QPushButton:pressed { background-color: #16161e; }
QPushButton:disabled { background-color: #1a1b26; color: #414868; }
QPushButton[primary="true"] { background-color: #7aa2f7; color: #1a1b26; }
QPushButton[primary="true"]:hover { background-color: #89b4fa; }
QPushButton[primary="true"]:pressed { background-color: #3d59a1; }
QLineEdit, QComboBox, QSpinBox { background-color: #16161e; color: #c0caf5; border: 1px solid #292e42; border-radius: 4px; padding: 4px 8px; }
QComboBox QAbstractItemView { background-color: #16161e; color: #c0caf5; selection-background-color: #7aa2f7; selection-color: #1a1b26; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #7aa2f7; }
QProgressBar { border: 1px solid #292e42; border-radius: 4px; text-align: center; background-color: #16161e; }
QProgressBar::chunk { background-color: #7aa2f7; border-radius: 3px; }
QDialog { background-color: #1a1b26; color: #c0caf5; }
QCheckBox, QRadioButton { color: #c0caf5; spacing: 6px; } QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; background-color: #16161e; border: 2px solid #292e42; border-radius: 4px; } QCheckBox::indicator:checked, QRadioButton::indicator:checked { background-color: #7aa2f7; border-color: #7aa2f7; image: none; } QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover { border-color: #3d59a1; } QRadioButton::indicator { border-radius: 9px; }
QLabel { color: #c0caf5; }
QTableWidget { background-color: #1a1b26; color: #c0caf5; gridline-color: #16161e; selection-background-color: #7aa2f7; selection-color: #1a1b26; alternate-background-color: #1e2030; }
QGroupBox { background-color: #1e2030; border: 1px solid #292e42; border-radius: 6px; margin-top: 8px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; color: #565f89; }
QTextEdit { background-color: #16161e; color: #c0caf5; border: 1px solid #292e42; border-radius: 4px; }
QScrollArea { background-color: #1a1b26; border: none; }
QSplitter::handle { background-color: #292e42; }
QListWidget { background-color: #1a1b26; color: #c0caf5; border: 1px solid #292e42; }
QListWidget::item { padding: 2px 4px; }
QListWidget::item:selected { background-color: #7aa2f7; color: #1a1b26; }
QScrollBar:vertical { background-color: #1a1b26; width: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background-color: #292e42; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background-color: #3d59a1; }
QScrollBar:horizontal { background-color: #1a1b26; height: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:horizontal { background-color: #292e42; border-radius: 5px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background-color: #3d59a1; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QToolTip { background-color: #16161e; color: #c0caf5; border: 1px solid #292e42; border-radius: 4px; padding: 4px 8px; }
"""

THEMES["Monokai"] = """
/* Monokai - Classic dark with vibrant green accents */
QMainWindow { background-color: #272822; color: #f8f8f2; }
QMenuBar { background-color: #3e3d32; color: #f8f8f2; border-bottom: 1px solid #75715e; }
QMenuBar::item:selected { background-color: #49483e; }
QMenu { background-color: #3e3d32; color: #f8f8f2; border: 1px solid #75715e; }
QMenu::item:selected { background-color: #49483e; }
QToolBar { background-color: #3e3d32; border-bottom: 1px solid #75715e; spacing: 4px; }
QToolBar QToolButton { color: #f8f8f2; border-radius: 4px; }
QToolBar QToolButton:hover { background-color: #49483e; }
QStatusBar { background-color: #3e3d32; color: #75715e; border-top: 1px solid #49483e; }
QTableView { background-color: #272822; color: #f8f8f2; gridline-color: #3e3d32; selection-background-color: #a6e22e; selection-color: #272822; alternate-background-color: #2d2c27; }
QHeaderView::section { background-color: #3e3d32; color: #f8f8f2; padding: 4px; border: none; border-right: 1px solid #75715e; }
QTableView::item { padding: 4px; }
QPushButton { background-color: #49483e; color: #f8f8f2; border: none; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background-color: #75715e; }
QPushButton:pressed { background-color: #3e3d32; }
QPushButton:disabled { background-color: #272822; color: #49483e; }
QPushButton[primary="true"] { background-color: #a6e22e; color: #272822; }
QPushButton[primary="true"]:hover { background-color: #c0f050; }
QPushButton[primary="true"]:pressed { background-color: #88bb1a; }
QLineEdit, QComboBox, QSpinBox { background-color: #3e3d32; color: #f8f8f2; border: 1px solid #75715e; border-radius: 4px; padding: 4px 8px; }
QComboBox QAbstractItemView { background-color: #3e3d32; color: #f8f8f2; selection-background-color: #a6e22e; selection-color: #272822; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #a6e22e; }
QProgressBar { border: 1px solid #75715e; border-radius: 4px; text-align: center; background-color: #3e3d32; }
QProgressBar::chunk { background-color: #a6e22e; border-radius: 3px; }
QDialog { background-color: #272822; color: #f8f8f2; }
QCheckBox, QRadioButton { color: #f8f8f2; spacing: 6px; } QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; background-color: #3e3d32; border: 2px solid #75715e; border-radius: 4px; } QCheckBox::indicator:checked, QRadioButton::indicator:checked { background-color: #a6e22e; border-color: #a6e22e; image: none; } QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover { border-color: #f8f8f2; } QRadioButton::indicator { border-radius: 9px; }
QLabel { color: #f8f8f2; }
QTableWidget { background-color: #272822; color: #f8f8f2; gridline-color: #3e3d32; selection-background-color: #a6e22e; selection-color: #272822; alternate-background-color: #2d2c27; }
QGroupBox { background-color: #2d2c27; border: 1px solid #75715e; border-radius: 6px; margin-top: 8px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; color: #75715e; }
QTextEdit { background-color: #3e3d32; color: #f8f8f2; border: 1px solid #75715e; border-radius: 4px; }
QScrollArea { background-color: #272822; border: none; }
QSplitter::handle { background-color: #75715e; }
QListWidget { background-color: #272822; color: #f8f8f2; border: 1px solid #75715e; }
QListWidget::item { padding: 2px 4px; }
QListWidget::item:selected { background-color: #a6e22e; color: #272822; }
QScrollBar:vertical { background-color: #272822; width: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background-color: #49483e; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background-color: #75715e; }
QScrollBar:horizontal { background-color: #272822; height: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:horizontal { background-color: #49483e; border-radius: 5px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background-color: #75715e; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QToolTip { background-color: #3e3d32; color: #f8f8f2; border: 1px solid #75715e; border-radius: 4px; padding: 4px 8px; }
"""

THEMES["One Dark"] = """
/* One Dark - Atom editor inspired, muted blue accents */
QMainWindow { background-color: #282c34; color: #abb2bf; }
QMenuBar { background-color: #21252b; color: #abb2bf; border-bottom: 1px solid #3e4451; }
QMenuBar::item:selected { background-color: #3e4451; }
QMenu { background-color: #21252b; color: #abb2bf; border: 1px solid #3e4451; }
QMenu::item:selected { background-color: #3e4451; }
QToolBar { background-color: #21252b; border-bottom: 1px solid #3e4451; spacing: 4px; }
QToolBar QToolButton { color: #abb2bf; border-radius: 4px; }
QToolBar QToolButton:hover { background-color: #3e4451; }
QStatusBar { background-color: #21252b; color: #636d83; border-top: 1px solid #3e4451; }
QTableView { background-color: #282c34; color: #abb2bf; gridline-color: #21252b; selection-background-color: #61afef; selection-color: #282c34; alternate-background-color: #2c313c; }
QHeaderView::section { background-color: #21252b; color: #abb2bf; padding: 4px; border: none; border-right: 1px solid #3e4451; }
QTableView::item { padding: 4px; }
QPushButton { background-color: #3e4451; color: #abb2bf; border: none; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background-color: #4b5263; }
QPushButton:pressed { background-color: #21252b; }
QPushButton:disabled { background-color: #282c34; color: #4b5263; }
QPushButton[primary="true"] { background-color: #61afef; color: #282c34; }
QPushButton[primary="true"]:hover { background-color: #7ec4ff; }
QPushButton[primary="true"]:pressed { background-color: #4d8ecb; }
QLineEdit, QComboBox, QSpinBox { background-color: #21252b; color: #abb2bf; border: 1px solid #3e4451; border-radius: 4px; padding: 4px 8px; }
QComboBox QAbstractItemView { background-color: #21252b; color: #abb2bf; selection-background-color: #61afef; selection-color: #282c34; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #61afef; }
QProgressBar { border: 1px solid #3e4451; border-radius: 4px; text-align: center; background-color: #21252b; }
QProgressBar::chunk { background-color: #61afef; border-radius: 3px; }
QDialog { background-color: #282c34; color: #abb2bf; }
QCheckBox, QRadioButton { color: #abb2bf; spacing: 6px; } QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; background-color: #21252b; border: 2px solid #3e4451; border-radius: 4px; } QCheckBox::indicator:checked, QRadioButton::indicator:checked { background-color: #61afef; border-color: #61afef; image: none; } QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover { border-color: #4b5263; } QRadioButton::indicator { border-radius: 9px; }
QLabel { color: #abb2bf; }
QTableWidget { background-color: #282c34; color: #abb2bf; gridline-color: #21252b; selection-background-color: #61afef; selection-color: #282c34; alternate-background-color: #2c313c; }
QGroupBox { background-color: #2c313c; border: 1px solid #3e4451; border-radius: 6px; margin-top: 8px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; color: #636d83; }
QTextEdit { background-color: #21252b; color: #abb2bf; border: 1px solid #3e4451; border-radius: 4px; }
QScrollArea { background-color: #282c34; border: none; }
QSplitter::handle { background-color: #3e4451; }
QListWidget { background-color: #282c34; color: #abb2bf; border: 1px solid #3e4451; }
QListWidget::item { padding: 2px 4px; }
QListWidget::item:selected { background-color: #61afef; color: #282c34; }
QScrollBar:vertical { background-color: #282c34; width: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background-color: #3e4451; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background-color: #4b5263; }
QScrollBar:horizontal { background-color: #282c34; height: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:horizontal { background-color: #3e4451; border-radius: 5px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background-color: #4b5263; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QToolTip { background-color: #21252b; color: #abb2bf; border: 1px solid #3e4451; border-radius: 4px; padding: 4px 8px; }
"""

THEMES["Solarized Light"] = """
/* Solarized Light - Low-contrast light, warm cream background */
QMainWindow { background-color: #fdf6e3; color: #657b83; }
QMenuBar { background-color: #eee8d5; color: #657b83; border-bottom: 1px solid #93a1a1; }
QMenuBar::item:selected { background-color: #d4cdb8; }
QMenu { background-color: #eee8d5; color: #657b83; border: 1px solid #93a1a1; }
QMenu::item:selected { background-color: #d4cdb8; }
QToolBar { background-color: #eee8d5; border-bottom: 1px solid #93a1a1; spacing: 4px; }
QToolBar QToolButton { color: #657b83; border-radius: 4px; }
QToolBar QToolButton:hover { background-color: #d4cdb8; }
QStatusBar { background-color: #eee8d5; color: #93a1a1; border-top: 1px solid #93a1a1; }
QTableView { background-color: #fdf6e3; color: #657b83; gridline-color: #eee8d5; selection-background-color: #268bd2; selection-color: #fdf6e3; alternate-background-color: #f9f2de; }
QHeaderView::section { background-color: #eee8d5; color: #657b83; padding: 4px; border: none; border-right: 1px solid #93a1a1; }
QTableView::item { padding: 4px; }
QPushButton { background-color: #d4cdb8; color: #586e75; border: none; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background-color: #b8b0a0; }
QPushButton:pressed { background-color: #93a1a1; }
QPushButton:disabled { background-color: #eee8d5; color: #93a1a1; }
QPushButton[primary="true"] { background-color: #268bd2; color: #fdf6e3; }
QPushButton[primary="true"]:hover { background-color: #2fa0f0; }
QPushButton[primary="true"]:pressed { background-color: #1a6fa3; }
QLineEdit, QComboBox, QSpinBox { background-color: #fdf6e3; color: #657b83; border: 1px solid #93a1a1; border-radius: 4px; padding: 4px 8px; }
QComboBox QAbstractItemView { background-color: #eee8d5; color: #657b83; selection-background-color: #268bd2; selection-color: #fdf6e3; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #268bd2; }
QProgressBar { border: 1px solid #93a1a1; border-radius: 4px; text-align: center; background-color: #eee8d5; }
QProgressBar::chunk { background-color: #268bd2; border-radius: 3px; }
QDialog { background-color: #fdf6e3; color: #657b83; }
QCheckBox, QRadioButton { color: #657b83; spacing: 6px; } QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; background-color: #eee8d5; border: 2px solid #93a1a1; border-radius: 4px; } QCheckBox::indicator:checked, QRadioButton::indicator:checked { background-color: #268bd2; border-color: #268bd2; image: none; } QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover { border-color: #657b83; } QRadioButton::indicator { border-radius: 9px; }
QLabel { color: #657b83; }
QTableWidget { background-color: #fdf6e3; color: #657b83; gridline-color: #eee8d5; selection-background-color: #268bd2; selection-color: #fdf6e3; alternate-background-color: #f9f2de; }
QGroupBox { background-color: #f0e9d5; border: 1px solid #93a1a1; border-radius: 6px; margin-top: 8px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; color: #93a1a1; }
QTextEdit { background-color: #fdf6e3; color: #657b83; border: 1px solid #93a1a1; border-radius: 4px; }
QScrollArea { background-color: #fdf6e3; border: none; }
QSplitter::handle { background-color: #93a1a1; }
QListWidget { background-color: #fdf6e3; color: #657b83; border: 1px solid #93a1a1; }
QListWidget::item { padding: 2px 4px; }
QListWidget::item:selected { background-color: #268bd2; color: #fdf6e3; }
QScrollBar:vertical { background-color: #eee8d5; width: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background-color: #93a1a1; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background-color: #657b83; }
QScrollBar:horizontal { background-color: #eee8d5; height: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:horizontal { background-color: #93a1a1; border-radius: 5px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background-color: #657b83; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QToolTip { background-color: #eee8d5; color: #657b83; border: 1px solid #93a1a1; border-radius: 4px; padding: 4px 8px; }
"""

THEMES["Sepia"] = """
/* Sepia - Warm cream light theme, easy on eyes for long sessions */
QMainWindow { background-color: #f4edd6; color: #3b2e1e; }
QMenuBar { background-color: #ede3c8; color: #3b2e1e; border-bottom: 1px solid #c8b89a; }
QMenuBar::item:selected { background-color: #d8caa8; }
QMenu { background-color: #ede3c8; color: #3b2e1e; border: 1px solid #c8b89a; }
QMenu::item:selected { background-color: #d8caa8; }
QToolBar { background-color: #ede3c8; border-bottom: 1px solid #c8b89a; spacing: 4px; }
QToolBar QToolButton { color: #3b2e1e; border-radius: 4px; }
QToolBar QToolButton:hover { background-color: #d8caa8; }
QStatusBar { background-color: #ede3c8; color: #7a6856; border-top: 1px solid #c8b89a; }
QTableView { background-color: #f4edd6; color: #3b2e1e; gridline-color: #e0d4b4; selection-background-color: #c67c2f; selection-color: #f4edd6; alternate-background-color: #f0e8cc; }
QHeaderView::section { background-color: #ede3c8; color: #3b2e1e; padding: 4px; border: none; border-right: 1px solid #c8b89a; }
QTableView::item { padding: 4px; }
QPushButton { background-color: #d8caa8; color: #3b2e1e; border: none; border-radius: 4px; padding: 6px 12px; }
QPushButton:hover { background-color: #c8b89a; }
QPushButton:pressed { background-color: #b8a88a; }
QPushButton:disabled { background-color: #ede3c8; color: #a09070; }
QPushButton[primary="true"] { background-color: #c67c2f; color: #f4edd6; }
QPushButton[primary="true"]:hover { background-color: #d98f45; }
QPushButton[primary="true"]:pressed { background-color: #a86520; }
QLineEdit, QComboBox, QSpinBox { background-color: #f4edd6; color: #3b2e1e; border: 1px solid #c8b89a; border-radius: 4px; padding: 4px 8px; }
QComboBox QAbstractItemView { background-color: #ede3c8; color: #3b2e1e; selection-background-color: #c67c2f; selection-color: #f4edd6; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #c67c2f; }
QProgressBar { border: 1px solid #c8b89a; border-radius: 4px; text-align: center; background-color: #ede3c8; }
QProgressBar::chunk { background-color: #c67c2f; border-radius: 3px; }
QDialog { background-color: #f4edd6; color: #3b2e1e; }
QCheckBox, QRadioButton { color: #3b2e1e; spacing: 6px; } QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; background-color: #ede3c8; border: 2px solid #c8b89a; border-radius: 4px; } QCheckBox::indicator:checked, QRadioButton::indicator:checked { background-color: #c67c2f; border-color: #c67c2f; image: none; } QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover { border-color: #7a6856; } QRadioButton::indicator { border-radius: 9px; }
QLabel { color: #3b2e1e; }
QTableWidget { background-color: #f4edd6; color: #3b2e1e; gridline-color: #e0d4b4; selection-background-color: #c67c2f; selection-color: #f4edd6; alternate-background-color: #f0e8cc; }
QGroupBox { background-color: #ede3c8; border: 1px solid #c8b89a; border-radius: 6px; margin-top: 8px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; color: #7a6856; }
QTextEdit { background-color: #f4edd6; color: #3b2e1e; border: 1px solid #c8b89a; border-radius: 4px; }
QScrollArea { background-color: #f4edd6; border: none; }
QSplitter::handle { background-color: #c8b89a; }
QListWidget { background-color: #f4edd6; color: #3b2e1e; border: 1px solid #c8b89a; }
QListWidget::item { padding: 2px 4px; }
QListWidget::item:selected { background-color: #c67c2f; color: #f4edd6; }
QScrollBar:vertical { background-color: #ede3c8; width: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background-color: #c8b89a; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background-color: #a89880; }
QScrollBar:horizontal { background-color: #ede3c8; height: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:horizontal { background-color: #c8b89a; border-radius: 5px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background-color: #a89880; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QToolTip { background-color: #ede3c8; color: #3b2e1e; border: 1px solid #c8b89a; border-radius: 4px; padding: 4px 8px; }
"""

THEMES["Starfield"] = """
/* Starfield - Game-accurate dark navy UI
   Colors reverse-engineered from Data/Interface SWF/GFX assets:
     bg        #0a0e1a  (between dialoguemenu #050a14 and inventorymenu #0d111a)
     panel     #111a28  (panel/toolbar layer)
     alt-row   #0e1420  (HUD gradient start, alternate table rows)
     border    #1c2e48  (formID watermark tone, grid lines)
     border-hi #2a3a54  (visible dividers)
     fg        #ececec  (inventory item text from invitemcard.gfx)
     fg-dim    #c0c8d8  (secondary labels, dialogue tree)
     fg-muted  #556688  (status bar, muted text, HUD border pen)
     accent    #3ff0ff  (Starfield cyan: dialoguemenu selection, tree highlights)
     accent-hi #7df8ff  (hover)
     accent-dn #2bc8d8  (pressed)
     sel-bg    #152030  (tree selected background)
     gold      #e8e8ac  (speaker name gold — used for primary buttons)
*/
QMainWindow { background-color: #0a0e1a; color: #ececec; }
QMenuBar { background-color: #111a28; color: #ececec; border-bottom: 1px solid #1c2e48; }
QMenuBar::item:selected { background-color: #1c2e48; }
QMenu { background-color: #111a28; color: #ececec; border: 1px solid #2a3a54; }
QMenu::item:selected { background-color: #152030; color: #3ff0ff; }
QToolBar { background-color: #111a28; border-bottom: 1px solid #1c2e48; spacing: 4px; }
QToolBar QToolButton { color: #c0c8d8; border-radius: 2px; }
QToolBar QToolButton:hover { background-color: #1c2e48; color: #3ff0ff; }
QStatusBar { background-color: #111a28; color: #556688; border-top: 1px solid #1c2e48; }
QTableView { background-color: #0a0e1a; color: #ececec; gridline-color: #1c2e48; selection-background-color: #152030; selection-color: #3ff0ff; alternate-background-color: #0e1420; }
QHeaderView::section { background-color: #111a28; color: #c0c8d8; padding: 4px; border: none; border-right: 1px solid #1c2e48; border-bottom: 1px solid #1c2e48; }
QTableView::item { padding: 4px; }
QPushButton { background-color: #1c2e48; color: #c0c8d8; border: 1px solid #2a3a54; border-radius: 2px; padding: 6px 12px; }
QPushButton:hover { background-color: #2a3a54; color: #3ff0ff; border-color: #3ff0ff; }
QPushButton:pressed { background-color: #111a28; color: #2bc8d8; }
QPushButton:disabled { background-color: #0a0e1a; color: #2a3a54; border-color: #1c2e48; }
QPushButton[primary="true"] { background-color: #152030; color: #3ff0ff; border: 1px solid #3ff0ff; }
QPushButton[primary="true"]:hover { background-color: #1c3848; color: #7df8ff; border-color: #7df8ff; }
QPushButton[primary="true"]:pressed { background-color: #111a28; color: #2bc8d8; }
QLineEdit, QComboBox, QSpinBox { background-color: #0e1420; color: #ececec; border: 1px solid #2a3a54; border-radius: 2px; padding: 4px 8px; }
QComboBox QAbstractItemView { background-color: #111a28; color: #ececec; selection-background-color: #152030; selection-color: #3ff0ff; border: 1px solid #2a3a54; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #3ff0ff; }
QProgressBar { border: 1px solid #2a3a54; border-radius: 2px; text-align: center; background-color: #0e1420; color: #c0c8d8; }
QProgressBar::chunk { background-color: #3ff0ff; border-radius: 1px; }
QDialog { background-color: #0a0e1a; color: #ececec; }
QCheckBox, QRadioButton { color: #c0c8d8; spacing: 6px; } QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; background-color: #0e1420; border: 2px solid #2a3a54; border-radius: 2px; } QCheckBox::indicator:checked, QRadioButton::indicator:checked { background-color: #3ff0ff; border-color: #3ff0ff; image: none; } QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover { border-color: #3ff0ff; } QRadioButton::indicator { border-radius: 9px; }
QLabel { color: #ececec; }
QTableWidget { background-color: #0a0e1a; color: #ececec; gridline-color: #1c2e48; selection-background-color: #152030; selection-color: #3ff0ff; alternate-background-color: #0e1420; }
QGroupBox { background-color: #0e1420; border: 1px solid #2a3a54; border-radius: 2px; margin-top: 8px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; color: #556688; }
QTextEdit { background-color: #0e1420; color: #ececec; border: 1px solid #2a3a54; border-radius: 2px; }
QScrollArea { background-color: #0a0e1a; border: none; }
QSplitter::handle { background-color: #1c2e48; }
QListWidget { background-color: #0a0e1a; color: #ececec; border: 1px solid #2a3a54; }
QListWidget::item { padding: 2px 4px; }
QListWidget::item:selected { background-color: #152030; color: #3ff0ff; }
QListWidget::item:hover { background-color: #0e1420; color: #c0c8d8; }
QScrollBar:vertical { background-color: #0a0e1a; width: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background-color: #2a3a54; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background-color: #3ff0ff; }
QScrollBar:horizontal { background-color: #0a0e1a; height: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:horizontal { background-color: #2a3a54; border-radius: 5px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background-color: #3ff0ff; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QToolTip { background-color: #111a28; color: #3ff0ff; border: 1px solid #3ff0ff; border-radius: 2px; padding: 4px 8px; }
QTabWidget::pane { border: 1px solid #2a3a54; background-color: #0a0e1a; }
QTabBar::tab { background-color: #0e1420; color: #c0c8d8; border: 1px solid #1c2e48; border-bottom: none; padding: 4px 12px; margin-right: 2px; }
QTabBar::tab:selected { background-color: #152030; color: #3ff0ff; border-color: #2a3a54; border-bottom: 2px solid #3ff0ff; }
QTabBar::tab:hover:!selected { background-color: #1c2e48; color: #c0c8d8; }
QDockWidget { color: #c0c8d8; }
QDockWidget::title { background-color: #111a28; color: #556688; border-bottom: 1px solid #1c2e48; padding: 4px 8px; }
"""

THEMES["Starfield Terminal"] = """
/* Starfield Terminal - Green-on-black computer/terminal screens
   Colors from visual_context_preview Terminal context:
     bg #030e03, fg #00cc00, border #009900 */
QMainWindow { background-color: #030e03; color: #00cc00; }
QMenuBar { background-color: #071407; color: #00cc00; border-bottom: 1px solid #005500; }
QMenuBar::item:selected { background-color: #0a2010; }
QMenu { background-color: #071407; color: #00cc00; border: 1px solid #005500; }
QMenu::item:selected { background-color: #0a2010; color: #00ff00; }
QToolBar { background-color: #071407; border-bottom: 1px solid #005500; spacing: 4px; }
QToolBar QToolButton { color: #009900; border-radius: 2px; }
QToolBar QToolButton:hover { background-color: #0a2010; color: #00ff00; }
QStatusBar { background-color: #071407; color: #005500; border-top: 1px solid #005500; }
QTableView { background-color: #030e03; color: #00cc00; gridline-color: #0a1e0a; selection-background-color: #0a2010; selection-color: #00ff00; alternate-background-color: #050f05; }
QHeaderView::section { background-color: #071407; color: #009900; padding: 4px; border: none; border-right: 1px solid #005500; border-bottom: 1px solid #005500; }
QTableView::item { padding: 4px; }
QPushButton { background-color: #0a1e0a; color: #009900; border: 1px solid #005500; border-radius: 2px; padding: 6px 12px; }
QPushButton:hover { background-color: #0a2010; color: #00ff00; border-color: #00cc00; }
QPushButton:pressed { background-color: #071407; color: #009900; }
QPushButton:disabled { background-color: #030e03; color: #003300; border-color: #003300; }
QPushButton[primary="true"] { background-color: #0a2010; color: #00ff00; border: 1px solid #00cc00; }
QPushButton[primary="true"]:hover { background-color: #0f2818; color: #44ff44; border-color: #44ff44; }
QPushButton[primary="true"]:pressed { background-color: #071407; }
QLineEdit, QComboBox, QSpinBox { background-color: #050f05; color: #00cc00; border: 1px solid #005500; border-radius: 2px; padding: 4px 8px; }
QComboBox QAbstractItemView { background-color: #071407; color: #00cc00; selection-background-color: #0a2010; selection-color: #00ff00; border: 1px solid #005500; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #00cc00; }
QProgressBar { border: 1px solid #005500; border-radius: 2px; text-align: center; background-color: #050f05; color: #009900; }
QProgressBar::chunk { background-color: #00cc00; border-radius: 1px; }
QDialog { background-color: #030e03; color: #00cc00; }
QCheckBox, QRadioButton { color: #009900; spacing: 6px; } QCheckBox::indicator, QRadioButton::indicator { width: 18px; height: 18px; background-color: #050f05; border: 2px solid #005500; border-radius: 2px; } QCheckBox::indicator:checked, QRadioButton::indicator:checked { background-color: #00cc00; border-color: #00cc00; image: none; } QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover { border-color: #00cc00; } QRadioButton::indicator { border-radius: 9px; }
QLabel { color: #00cc00; }
QTableWidget { background-color: #030e03; color: #00cc00; gridline-color: #0a1e0a; selection-background-color: #0a2010; selection-color: #00ff00; alternate-background-color: #050f05; }
QGroupBox { background-color: #050f05; border: 1px solid #005500; border-radius: 2px; margin-top: 8px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; color: #005500; }
QTextEdit { background-color: #050f05; color: #00cc00; border: 1px solid #005500; border-radius: 2px; }
QScrollArea { background-color: #030e03; border: none; }
QSplitter::handle { background-color: #005500; }
QListWidget { background-color: #030e03; color: #00cc00; border: 1px solid #005500; }
QListWidget::item { padding: 2px 4px; }
QListWidget::item:selected { background-color: #0a2010; color: #00ff00; }
QListWidget::item:hover { color: #00ff00; }
QScrollBar:vertical { background-color: #030e03; width: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:vertical { background-color: #005500; border-radius: 5px; min-height: 20px; }
QScrollBar::handle:vertical:hover { background-color: #00cc00; }
QScrollBar:horizontal { background-color: #030e03; height: 10px; margin: 0; border-radius: 5px; }
QScrollBar::handle:horizontal { background-color: #005500; border-radius: 5px; min-width: 20px; }
QScrollBar::handle:horizontal:hover { background-color: #00cc00; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QToolTip { background-color: #071407; color: #00ff00; border: 1px solid #00cc00; border-radius: 2px; padding: 4px 8px; }
QTabWidget::pane { border: 1px solid #005500; background-color: #030e03; }
QTabBar::tab { background-color: #050f05; color: #009900; border: 1px solid #003300; border-bottom: none; padding: 4px 12px; margin-right: 2px; }
QTabBar::tab:selected { background-color: #0a2010; color: #00ff00; border-color: #005500; border-bottom: 2px solid #00cc00; }
QTabBar::tab:hover:!selected { background-color: #071407; color: #00cc00; }
QDockWidget { color: #009900; }
QDockWidget::title { background-color: #071407; color: #005500; border-bottom: 1px solid #005500; padding: 4px 8px; }
"""

THEMES["High Contrast"] = """
/* High Contrast — WCAG AAA, ≥7:1 contrast ratio throughout.
   Yellow focus ring follows Windows High Contrast accessibility convention. */
QMainWindow, QDialog, QWidget { background-color: #000000; color: #ffffff; }
QMenuBar { background-color: #000000; color: #ffffff; border-bottom: 2px solid #ffffff; }
QMenuBar::item:selected { background-color: #1aebff; color: #000000; }
QMenu { background-color: #000000; color: #ffffff; border: 2px solid #ffffff; }
QMenu::item:selected { background-color: #1aebff; color: #000000; }
QToolBar { background-color: #000000; border-bottom: 2px solid #ffffff; spacing: 4px; }
QToolBar QToolButton { color: #ffffff; border: 1px solid #ffffff; border-radius: 2px; padding: 2px 6px; }
QToolBar QToolButton:hover { background-color: #1aebff; color: #000000; border-color: #1aebff; }
QToolBar QToolButton:focus { border: 3px solid #ffff00; }
QStatusBar { background-color: #000000; color: #ffffff; border-top: 2px solid #ffffff; }
QTableView { background-color: #000000; color: #ffffff; gridline-color: #ffffff; selection-background-color: #1aebff; selection-color: #000000; alternate-background-color: #0d0d0d; border: 2px solid #ffffff; }
QTableView:focus { border: 3px solid #ffff00; }
QTableView::item { padding: 4px; }
QTableView::item:focus { border: 2px solid #ffff00; }
QHeaderView::section { background-color: #000000; color: #ffffff; padding: 4px; border: none; border-right: 2px solid #ffffff; border-bottom: 2px solid #ffffff; font-weight: bold; }
QPushButton { background-color: #000000; color: #ffffff; border: 2px solid #ffffff; border-radius: 2px; padding: 6px 12px; }
QPushButton:hover { background-color: #ffffff; color: #000000; }
QPushButton:focus { border: 3px solid #ffff00; }
QPushButton:pressed { background-color: #1aebff; color: #000000; }
QPushButton:disabled { color: #767676; border-color: #767676; }
QPushButton[primary="true"] { background-color: #1aebff; color: #000000; border-color: #1aebff; font-weight: bold; }
QPushButton[primary="true"]:focus { border: 3px solid #ffff00; }
QLineEdit, QComboBox, QSpinBox { background-color: #000000; color: #ffffff; border: 2px solid #ffffff; border-radius: 2px; padding: 4px 8px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border: 3px solid #ffff00; }
QComboBox QAbstractItemView { background-color: #000000; color: #ffffff; selection-background-color: #1aebff; selection-color: #000000; border: 2px solid #ffffff; }
QProgressBar { border: 2px solid #ffffff; border-radius: 2px; text-align: center; background-color: #000000; color: #ffffff; }
QProgressBar::chunk { background-color: #1aebff; border-radius: 0; }
QCheckBox, QRadioButton { color: #ffffff; spacing: 6px; }
QCheckBox::indicator, QRadioButton::indicator { width: 20px; height: 20px; background-color: #000000; border: 2px solid #ffffff; border-radius: 2px; }
QCheckBox::indicator:checked { background-color: #1aebff; border-color: #1aebff; }
QCheckBox::indicator:focus, QRadioButton::indicator:focus { border: 3px solid #ffff00; }
QRadioButton::indicator { border-radius: 10px; }
QLabel { color: #ffffff; }
QGroupBox { background-color: #0d0d0d; border: 2px solid #ffffff; border-radius: 2px; margin-top: 8px; padding-top: 16px; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 6px; color: #ffffff; font-weight: bold; }
QTextEdit { background-color: #000000; color: #ffffff; border: 2px solid #ffffff; border-radius: 2px; }
QTextEdit:focus { border: 3px solid #ffff00; }
QScrollArea { background-color: #000000; border: none; }
QSplitter::handle { background-color: #ffffff; width: 2px; height: 2px; }
QListWidget { background-color: #000000; color: #ffffff; border: 2px solid #ffffff; }
QListWidget::item { padding: 4px; border-bottom: 1px solid #333333; }
QListWidget::item:selected { background-color: #1aebff; color: #000000; }
QListWidget::item:focus { border: 2px solid #ffff00; }
QScrollBar:vertical { background-color: #000000; width: 14px; margin: 0; border: 1px solid #ffffff; }
QScrollBar::handle:vertical { background-color: #ffffff; min-height: 24px; }
QScrollBar::handle:vertical:hover { background-color: #1aebff; }
QScrollBar:horizontal { background-color: #000000; height: 14px; margin: 0; border: 1px solid #ffffff; }
QScrollBar::handle:horizontal { background-color: #ffffff; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background-color: #1aebff; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; background: none; border: none; }
QTableWidget { background-color: #000000; color: #ffffff; gridline-color: #ffffff; selection-background-color: #1aebff; selection-color: #000000; alternate-background-color: #0d0d0d; }
QToolTip { background-color: #000000; color: #ffffff; border: 2px solid #ffffff; padding: 4px 8px; }
"""

# ─── Focus indicator mixin (appended to every theme) ───────────────
# Qt QSS does not support 'outline'; focus is conveyed via border changes.
# These rules give ALL interactive widgets a visible focus ring that meets
# WCAG 2.1 SC 2.4.7 (visible focus indicator).
_FOCUS_MIXIN = """
QPushButton:focus { border: 2px solid palette(highlight); }
QToolButton:focus { border: 2px solid palette(highlight); }
QAbstractItemView:focus { border: 2px solid palette(highlight); }
QCheckBox:focus { border: 1px dashed palette(highlight); border-radius: 3px; padding: 1px; }
QRadioButton:focus { border: 1px dashed palette(highlight); border-radius: 3px; padding: 1px; }
QTabBar::tab:focus { border-bottom: 3px solid palette(highlight); }
"""

for __n, __q in list(THEMES.items()):
    if __n != "High Contrast":   # HC already has explicit focus rules
        THEMES[__n] = __q.rstrip() + "\n" + _FOCUS_MIXIN


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
            if getattr(sys, "frozen", False):
                # The bundle dir (Path(__file__).parent.parent → _MEIPASS) is
                # read-only, so user-saved themes must live in the config dir.
                try:
                    from gui.app_settings import get_config_dir
                    self._custom_dir = get_config_dir() / self.THEMES_DIR_NAME
                except Exception:
                    self._custom_dir = Path(__file__).parent.parent / self.THEMES_DIR_NAME
            else:
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
            "Gruvbox": QCoreApplication.translate("ThemeManager", "Warm retro dark with amber/orange accents"),
            "Tokyo Night": QCoreApplication.translate("ThemeManager", "Deep navy cyberpunk with blue highlights"),
            "Monokai": QCoreApplication.translate("ThemeManager", "Classic terminal dark with vibrant green accents"),
            "One Dark": QCoreApplication.translate("ThemeManager", "Atom editor inspired, muted blue accents"),
            "Solarized Light": QCoreApplication.translate("ThemeManager", "Low-contrast light, warm cream — complement to Solarized Dark"),
            "Sepia": QCoreApplication.translate("ThemeManager", "Warm cream light theme, easy on eyes for long sessions"),
            "Starfield": QCoreApplication.translate("ThemeManager", "Game-accurate dark navy UI — colors from Starfield Interface SWF/GFX assets"),
            "Starfield Terminal": QCoreApplication.translate("ThemeManager", "Green-on-black terminal/computer screen aesthetic from the game"),
            "High Contrast": QCoreApplication.translate("ThemeManager", "WCAG AAA black/white/cyan theme for visually impaired users"),
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
