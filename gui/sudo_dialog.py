"""App-themed password prompt for privilege escalation.

The graphical-sudo paths (``sudo -A`` with an external ssh-askpass helper, or
``pkexec``'s polkit dialog) work, but they pop a *system* dialog that ignores the
application's theme and may not be installed on a minimal tiling-WM setup.  This
module provides a small :class:`QDialog` that inherits the app's QSS theme and
collects the password ourselves, which is then handed to ``sudo -S`` (read from
stdin) by the caller.

Security notes: the field uses password echo mode, the value is never logged, and
the dialog drops its reference on close.  Python strings are immutable so the
plaintext cannot be wiped from memory with certainty — the caller should keep the
returned value only as long as it takes to write it to the child's stdin and then
let it go out of scope.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class SudoPasswordDialog(QDialog):
    """Themed prompt that asks for the root password (paired with ``sudo -S``)."""

    def __init__(
        self,
        command: str,
        parent: Optional[QWidget] = None,
        prompt: Optional[str] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Administrator password required"))
        self.setMinimumWidth(440)
        self.setModal(True)
        self._command = command or ""
        self._setup_ui(prompt)

    def _setup_ui(self, prompt: Optional[str]) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Heading row: lock glyph + explanation.
        head = QHBoxLayout()
        icon = QLabel("\N{LOCK}")
        icon.setStyleSheet("font-size: 28px;")
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        head.addWidget(icon)

        msg = QLabel(
            prompt
            or self.tr(
                "This action needs administrator (root) privileges.\n"
                "Enter your password to continue."
            )
        )
        msg.setWordWrap(True)
        head.addWidget(msg, stretch=1)
        layout.addLayout(head)

        # Show the exact command so the user knows what they are authorising.
        if self._command:
            cmd = QLabel(self._command)
            cmd.setWordWrap(True)
            cmd.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            cmd.setStyleSheet(
                "font-family: monospace; padding: 6px 8px;"
                "background: rgba(127,127,127,0.15); border-radius: 4px;"
            )
            layout.addWidget(cmd)

        # Password field.
        self._password_edit = QLineEdit()
        self._password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._password_edit.setPlaceholderText(self.tr("Password"))
        self._password_edit.returnPressed.connect(self.accept)
        layout.addWidget(self._password_edit)

        # Reveal toggle — convenience, defaults to hidden.
        self._show = QCheckBox(self.tr("Show password"))
        self._show.toggled.connect(self._toggle_echo)
        layout.addWidget(self._show)

        # Buttons.
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_cancel = QPushButton(self.tr("Cancel"))
        self.btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_cancel)

        self.btn_ok = QPushButton(self.tr("Authenticate"))
        self.btn_ok.setProperty("primary", True)
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_ok)
        layout.addLayout(btn_row)

        self._password_edit.setFocus()

    def _toggle_echo(self, shown: bool) -> None:
        self._password_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password
        )

    def password(self) -> str:
        """The entered password (only meaningful after ``Accepted``)."""
        return self._password_edit.text()

    @staticmethod
    def get_password(
        command: str,
        parent: Optional[QWidget] = None,
        prompt: Optional[str] = None,
    ) -> Optional[str]:
        """Show the dialog; return the password, or ``None`` if cancelled."""
        dlg = SudoPasswordDialog(command, parent, prompt)
        try:
            if dlg.exec() == QDialog.DialogCode.Accepted:
                return dlg.password()
            return None
        finally:
            # Drop the editor's text so the plaintext is not held by the widget.
            dlg._password_edit.clear()
            dlg.deleteLater()
