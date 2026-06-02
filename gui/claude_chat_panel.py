"""
Claude AI Assistant chat panel.

A dockable panel that lets the user:
  - Chat with Claude about the current string / translation
  - Ask Claude to review the active translation (quality review)
  - Apply Claude's suggested translation directly to the table

The panel automatically populates context when the user selects a string in
the main table so Claude always has the relevant source/translation in view.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


# ── Background chat worker ────────────────────────────────────────────────────

class _ChatWorker(QThread):
    """Calls Claude in a background thread, streaming tokens to the UI."""

    token_ready  = Signal(str)   # incremental text delta
    reply_ready  = Signal(str)   # full reply (for history storage)
    error_signal = Signal(str)

    def __init__(
        self,
        api_key: str,
        model: str,
        messages: List[Dict],
        system: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.api_key  = api_key
        self.model    = model
        self.messages = messages
        self.system   = system

    def run(self) -> None:
        try:
            from gui.claude_client import ClaudeClient
            client = ClaudeClient(self.api_key, self.model)
            parts: List[str] = []
            for chunk in client.chat_stream(self.messages, system=self.system):
                parts.append(chunk)
                self.token_ready.emit(chunk)
            self.reply_ready.emit("".join(parts))
        except Exception as exc:
            self.error_signal.emit(str(exc))


class _ReviewWorker(QThread):
    """Calls Claude's review endpoint in a background thread."""

    review_ready = Signal(str)
    error_signal = Signal(str)

    def __init__(
        self,
        api_key: str,
        model: str,
        original: str,
        translation: str,
        source_lang: str,
        target_lang: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.api_key     = api_key
        self.model       = model
        self.original    = original
        self.translation = translation
        self.source_lang = source_lang
        self.target_lang = target_lang

    def run(self) -> None:
        try:
            from gui.claude_client import ClaudeClient
            client = ClaudeClient(self.api_key, self.model)
            review = client.review_translation(
                self.original, self.translation, self.source_lang, self.target_lang
            )
            self.review_ready.emit(review)
        except Exception as exc:
            self.error_signal.emit(str(exc))


# ── Main panel ────────────────────────────────────────────────────────────────

class ClaudeChatPanel(QDockWidget):
    """
    Dockable Claude AI Assistant panel.

    Signals
    -------
    apply_translation(str)
        Emitted when the user clicks "Use as Translation".
        The string argument is the text Claude suggested.
    """

    apply_translation = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ClaudeChatPanel")
        self.setWindowTitle(self.tr("Claude AI Assistant"))
        self.setFeatures(
            QDockWidget.DockWidgetClosable
            | QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetFloatable,
        )

        # State
        self._api_key:   str       = ""
        self._model:     str       = "claude-haiku-4-5"
        self._source_lang: str     = "ru"
        self._target_lang: str     = "uk"
        self._history:  List[Dict] = []   # [{"role": …, "content": …}]
        self._current_original:    str = ""
        self._current_translation: str = ""
        self._worker:   Optional[_ChatWorker]   = None
        self._reviewer: Optional[_ReviewWorker] = None

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("ClaudeChatRoot")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # ── API key + model bar ────────────────────────────────────────────────
        key_row = QHBoxLayout()
        key_row.setSpacing(4)

        key_row.addWidget(QLabel(self.tr("Key:")))
        self._edit_key = QLineEdit()
        self._edit_key.setEchoMode(QLineEdit.Password)
        self._edit_key.setPlaceholderText(self.tr("Anthropic API key (sk-ant-…)"))
        self._edit_key.setToolTip(
            self.tr("Your Anthropic API key.  Find it at console.anthropic.com")
        )
        self._edit_key.textChanged.connect(self._on_key_changed)
        key_row.addWidget(self._edit_key, stretch=1)

        self._combo_model = QComboBox()
        from gui.claude_client import CLAUDE_MODELS
        for model_id, label in CLAUDE_MODELS.items():
            self._combo_model.addItem(label, model_id)
        self._combo_model.currentIndexChanged.connect(self._on_model_changed)
        key_row.addWidget(self._combo_model)

        layout.addLayout(key_row)

        # ── Context strip (current string) ─────────────────────────────────────
        self._context_frame = QFrame()
        self._context_frame.setFrameShape(QFrame.StyledPanel)
        self._context_frame.setStyleSheet(
            "QFrame { background: rgba(30,41,59,0.6); border-radius: 4px; }"
        )
        ctx_layout = QVBoxLayout(self._context_frame)
        ctx_layout.setContentsMargins(6, 4, 6, 4)
        ctx_layout.setSpacing(2)

        self._lbl_context_title = QLabel(self.tr("No string selected"))
        self._lbl_context_title.setStyleSheet("font-weight: bold; font-size: 11px;")
        ctx_layout.addWidget(self._lbl_context_title)

        self._lbl_original = QLabel()
        self._lbl_original.setWordWrap(True)
        self._lbl_original.setStyleSheet("color: #94a3b8; font-size: 11px;")
        ctx_layout.addWidget(self._lbl_original)

        self._lbl_translation = QLabel()
        self._lbl_translation.setWordWrap(True)
        self._lbl_translation.setStyleSheet("color: #7dd3fc; font-size: 11px;")
        ctx_layout.addWidget(self._lbl_translation)

        layout.addWidget(self._context_frame)

        # ── Chat history ───────────────────────────────────────────────────────
        self._chat_view = QTextEdit()
        self._chat_view.setReadOnly(True)
        self._chat_view.setObjectName("ClaudeChatView")
        self._chat_view.document().setDefaultStyleSheet(
            """
            .user    { color: #7dd3fc; margin-bottom: 4px; }
            .claude  { color: #d1fae5; margin-bottom: 4px; }
            .system  { color: #94a3b8; font-style: italic; margin-bottom: 2px; }
            b        { font-weight: 600; }
            """
        )
        self._chat_view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._chat_view, stretch=1)

        # ── Quick-action buttons ───────────────────────────────────────────────
        quick_row = QHBoxLayout()
        quick_row.setSpacing(4)

        self._btn_review = QPushButton(self.tr("Review Translation"))
        self._btn_review.setToolTip(
            self.tr("Ask Claude to review the current translation for quality issues")
        )
        self._btn_review.clicked.connect(self._do_review)
        quick_row.addWidget(self._btn_review)

        self._btn_suggest = QPushButton(self.tr("Suggest Translation"))
        self._btn_suggest.setToolTip(
            self.tr("Ask Claude to translate the current source string")
        )
        self._btn_suggest.clicked.connect(self._do_suggest)
        quick_row.addWidget(self._btn_suggest)

        self._btn_apply = QPushButton(self.tr("Use as Translation"))
        self._btn_apply.setToolTip(
            self.tr(
                "Apply Claude's last suggested translation to the selected table row.\n"
                "The suggestion is the last code block or plain text in the chat."
            )
        )
        self._btn_apply.setEnabled(False)
        self._btn_apply.clicked.connect(self._do_apply)
        quick_row.addWidget(self._btn_apply)

        layout.addLayout(quick_row)

        # ── Input area ─────────────────────────────────────────────────────────
        input_row = QHBoxLayout()
        input_row.setSpacing(4)

        self._input = QPlainTextEdit()
        self._input.setMaximumHeight(72)
        self._input.setPlaceholderText(
            self.tr(
                "Ask Claude about this string… (Ctrl+Enter to send)"
            )
        )
        self._input.installEventFilter(self)
        input_row.addWidget(self._input, stretch=1)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(2)

        self._btn_send = QPushButton(self.tr("Send"))
        self._btn_send.setFixedWidth(70)
        self._btn_send.clicked.connect(self._do_send)
        btn_col.addWidget(self._btn_send)

        self._btn_clear = QPushButton(self.tr("Clear"))
        self._btn_clear.setFixedWidth(70)
        self._btn_clear.setToolTip(self.tr("Clear conversation history"))
        self._btn_clear.clicked.connect(self._do_clear)
        btn_col.addWidget(self._btn_clear)

        input_row.addLayout(btn_col)
        layout.addLayout(input_row)

        # Loading indicator
        self._lbl_thinking = QLabel(self.tr("Claude is thinking…"))
        self._lbl_thinking.setStyleSheet("color: #94a3b8; font-style: italic;")
        self._lbl_thinking.setVisible(False)
        layout.addWidget(self._lbl_thinking)

        self.setWidget(root)

        # Load persisted API key
        from gui.claude_client import get_api_key
        key = get_api_key()
        if key:
            self._api_key = key
            self._edit_key.setText(key)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_current_string(
        self,
        string_id: int,
        original: str,
        translation: str,
        source_lang: str = "ru",
        target_lang: str = "uk",
    ) -> None:
        """Called by MainWindow when the user selects a table row."""
        self._current_original    = original
        self._current_translation = translation
        self._source_lang         = source_lang
        self._target_lang         = target_lang

        self._lbl_context_title.setText(
            self.tr("String 0x{sid:08X}").format(sid=string_id)
        )
        snip = 120
        self._lbl_original.setText(
            original[:snip] + ("…" if len(original) > snip else "")
        )
        self._lbl_translation.setText(
            (translation[:snip] + ("…" if len(translation) > snip else ""))
            if translation else self.tr("<no translation yet>")
        )

    def set_api_key(self, key: str) -> None:
        """Programmatically set the API key (e.g. from settings dialog)."""
        self._api_key = key.strip()
        self._edit_key.setText(self._api_key)

    def set_model(self, model_id: str) -> None:
        """Programmatically select a Claude model."""
        for i in range(self._combo_model.count()):
            if self._combo_model.itemData(i) == model_id:
                self._combo_model.setCurrentIndex(i)
                break
        self._model = model_id

    # ── Key / model slots ─────────────────────────────────────────────────────

    @Slot(str)
    def _on_key_changed(self, text: str) -> None:
        self._api_key = text.strip()
        from gui.claude_client import set_api_key
        if self._api_key:
            set_api_key(self._api_key)

    @Slot(int)
    def _on_model_changed(self, _idx: int) -> None:
        self._model = self._combo_model.currentData()

    # ── Chat / review actions ─────────────────────────────────────────────────

    def _check_ready(self) -> bool:
        """Return True if API key is set, show a warning otherwise."""
        if not self._api_key:
            QMessageBox.warning(
                self,
                self.tr("API Key Required"),
                self.tr(
                    "Please enter your Anthropic API key in the field above.\n"
                    "You can get one at console.anthropic.com"
                ),
            )
            return False
        return True

    def _system_prompt(self) -> str:
        from gui.ollama_worker import _LANG_DISPLAY  # type: ignore[attr-defined]
        src = _LANG_DISPLAY.get(self._source_lang, self._source_lang.upper())
        tgt = _LANG_DISPLAY.get(self._target_lang, self._target_lang.upper())
        return (
            f"You are a Bethesda Starfield game localization assistant "
            f"helping with {src} → {tgt} translation. "
            f"You have access to the current string being worked on (shown in each user turn). "
            f"Be concise and practical. When suggesting a translation, wrap it in a code block: "
            f"```\n<translation here>\n```"
        )

    @Slot()
    def _do_review(self) -> None:
        if not self._check_ready():
            return
        if not self._current_original:
            self._append_system("Select a string in the table first.")
            return

        self._set_busy(True)
        self._reviewer = _ReviewWorker(
            api_key=self._api_key,
            model=self._model,
            original=self._current_original,
            translation=self._current_translation,
            source_lang=self._source_lang,
            target_lang=self._target_lang,
            parent=self,
        )
        self._reviewer.review_ready.connect(self._on_review_done)
        self._reviewer.error_signal.connect(self._on_error)
        self._reviewer.finished.connect(lambda: self._set_busy(False))
        self._reviewer.start()

    @Slot()
    def _do_suggest(self) -> None:
        if not self._check_ready():
            return
        if not self._current_original:
            self._append_system("Select a string in the table first.")
            return

        msg = (
            f"Please translate this game string:\n\n"
            f"Source: {self._current_original}"
        )
        if self._current_translation:
            msg += f"\n\nExisting translation (may need improvement): {self._current_translation}"
        self._send_message(msg)

    @Slot()
    def _do_apply(self) -> None:
        """Extract last code block from chat and emit apply_translation."""
        html = self._chat_view.toPlainText()
        # Find last ```…``` block
        import re
        blocks = re.findall(r"```\n?(.*?)\n?```", html, re.DOTALL)
        if blocks:
            suggestion = blocks[-1].strip()
            self.apply_translation.emit(suggestion)
            self._append_system(f"Applied: {suggestion[:80]}…" if len(suggestion) > 80 else f"Applied: {suggestion}")
        else:
            QMessageBox.information(
                self,
                self.tr("No suggestion found"),
                self.tr(
                    "No code block found in the last reply.\n"
                    "Ask Claude to suggest a translation first."
                ),
            )

    @Slot()
    def _do_send(self) -> None:
        text = self._input.toPlainText().strip()
        if not text:
            return
        if not self._check_ready():
            return
        self._input.clear()
        self._send_message(text)

    @Slot()
    def _do_clear(self) -> None:
        self._history.clear()
        self._chat_view.clear()
        self._btn_apply.setEnabled(False)
        self._append_system("Conversation cleared.")

    def _send_message(self, user_text: str) -> None:
        # Add current context as a preamble so Claude knows what string we're on
        context_note = ""
        if self._current_original:
            from gui.ollama_worker import _LANG_DISPLAY  # type: ignore[attr-defined]
            src = _LANG_DISPLAY.get(self._source_lang, self._source_lang.upper())
            tgt = _LANG_DISPLAY.get(self._target_lang, self._target_lang.upper())
            context_note = (
                f"[Current string — Source ({src}): {self._current_original[:200]}"
            )
            if self._current_translation:
                context_note += f" | Current translation ({tgt}): {self._current_translation[:200]}"
            context_note += "]\n\n"

        full_text = context_note + user_text
        self._history.append({"role": "user", "content": full_text})
        self._append_user(user_text)

        self._set_busy(True)
        self._worker = _ChatWorker(
            api_key=self._api_key,
            model=self._model,
            messages=list(self._history),
            system=self._system_prompt(),
            parent=self,
        )
        # Prepare the streaming block before the worker starts
        self._begin_claude_stream()
        self._worker.token_ready.connect(self._on_token)
        self._worker.reply_ready.connect(self._on_reply)
        self._worker.error_signal.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_busy(False))
        self._worker.start()

    # ── Worker result slots ───────────────────────────────────────────────────

    def _begin_claude_stream(self) -> None:
        """Insert the 'Claude:' header and record the cursor position for token insertion."""
        cursor = self._chat_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._chat_view.setTextCursor(cursor)
        self._chat_view.insertHtml('<p class="claude"><b>Claude:</b><br>')
        self._stream_start = self._chat_view.textCursor().position()
        self._stream_parts: list = []

    @Slot(str)
    def _on_token(self, chunk: str) -> None:
        """Append a streaming token at the tracked cursor position."""
        self._stream_parts.append(chunk)
        cursor = self._chat_view.textCursor()
        cursor.setPosition(self._stream_start)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertText("".join(self._stream_parts))
        self._scroll_bottom()

    @Slot(str)
    def _on_reply(self, text: str) -> None:
        """Replace raw streamed text with nicely formatted HTML."""
        import re
        self._history.append({"role": "assistant", "content": text})

        # Build the formatted content (same logic as _append_claude)
        formatted = re.sub(
            r"```\n?(.*?)\n?```",
            r'<pre style="background:rgba(30,41,59,0.8);border-radius:4px;padding:6px;'
            r'margin:4px 0;color:#a7f3d0;">\1</pre>',
            self._esc(text),
            flags=re.DOTALL,
        )
        formatted = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", formatted)
        formatted = formatted.replace("\n", "<br>")

        # Overwrite the plain-text stream with formatted HTML
        cursor = self._chat_view.textCursor()
        cursor.setPosition(self._stream_start)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertHtml(formatted + "</p>")

        self._scroll_bottom()
        self._btn_apply.setEnabled(bool(re.search(r"```", text)))

    @Slot(str)
    def _on_review_done(self, text: str) -> None:
        self._history.append({"role": "assistant", "content": text})
        self._append_claude(text, prefix="📋 Translation Review")

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        self._append_system(f"Error: {msg}")
        logger.error("Claude chat error: %s", msg)

    # ── Chat view helpers ─────────────────────────────────────────────────────

    def _append_user(self, text: str) -> None:
        self._chat_view.append(
            f'<p class="user"><b>You:</b> {self._esc(text)}</p>'
        )
        self._scroll_bottom()

    def _append_claude(self, text: str, prefix: str = "Claude") -> None:
        import re
        # Highlight code blocks
        formatted = re.sub(
            r"```\n?(.*?)\n?```",
            r'<pre style="background:rgba(30,41,59,0.8);border-radius:4px;padding:6px;'
            r'margin:4px 0;color:#a7f3d0;">\1</pre>',
            self._esc(text),
            flags=re.DOTALL,
        )
        # Basic markdown bold
        formatted = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", formatted)
        # Newlines to <br>
        formatted = formatted.replace("\n", "<br>")
        self._chat_view.append(
            f'<p class="claude"><b>{self._esc(prefix)}:</b><br>{formatted}</p>'
        )
        self._scroll_bottom()

    def _append_system(self, text: str) -> None:
        self._chat_view.append(f'<p class="system">{self._esc(text)}</p>')
        self._scroll_bottom()

    def _scroll_bottom(self) -> None:
        sb = self._chat_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    @staticmethod
    def _esc(text: str) -> str:
        import html
        return html.escape(text)

    def _set_busy(self, busy: bool) -> None:
        self._lbl_thinking.setVisible(busy)
        self._btn_send.setEnabled(not busy)
        self._btn_review.setEnabled(not busy)
        self._btn_suggest.setEnabled(not busy)

    # ── Ctrl+Enter to send ────────────────────────────────────────────────────

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent
        from PySide6.QtGui import QKeyEvent
        if obj is self._input and event.type() == QEvent.KeyPress:
            ke: QKeyEvent = event  # type: ignore[assignment]
            if ke.key() == Qt.Key_Return and (ke.modifiers() & Qt.ControlModifier):
                self._do_send()
                return True
        return super().eventFilter(obj, event)
