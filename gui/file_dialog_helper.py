"""
Thin wrappers around QFileDialog that fix column widths in Qt's own
(non-native) file dialog. Without this the Date Modified column is
always too narrow and shows truncated text like "4/28/2...:51 PM".
"""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QHeaderView, QTreeView


def _fix_columns(dialog: QFileDialog) -> None:
    """Make Size / Type / Date columns fit their content; Name gets the rest."""
    def _apply():
        tree = dialog.findChild(QTreeView)
        if tree is None:
            return
        h = tree.header()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

    # Try immediately (widgets are created in QFileDialog's constructor)
    _apply()
    # Also schedule for after the first paint in case the view is lazy
    QTimer.singleShot(0, _apply)


def get_open_filename(
    parent=None,
    caption: str = "",
    directory: str = "",
    filter: str = "",
) -> tuple[str, str]:
    """Drop-in replacement for QFileDialog.getOpenFileName."""
    dialog = QFileDialog(parent, caption, directory, filter)
    dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    _fix_columns(dialog)
    if dialog.exec():
        files = dialog.selectedFiles()
        return (files[0] if files else ""), dialog.selectedNameFilter()
    return "", ""


def get_save_filename(
    parent=None,
    caption: str = "",
    directory: str = "",
    filter: str = "",
) -> tuple[str, str]:
    """Drop-in replacement for QFileDialog.getSaveFileName."""
    dialog = QFileDialog(parent, caption, directory, filter)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    _fix_columns(dialog)
    if dialog.exec():
        files = dialog.selectedFiles()
        return (files[0] if files else ""), dialog.selectedNameFilter()
    return "", ""
