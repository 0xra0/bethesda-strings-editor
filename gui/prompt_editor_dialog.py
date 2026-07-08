"""
Translation Prompt Editor.

Lets the user customize the translation *system prompt* without touching code:

* **Rule 1 (style/register)** can be overridden per target language. This is the
  layer that carries the language's voice — formal vs casual, script/quotation
  conventions, terminology preferences.
* **Extra instructions** are appended verbatim to every translation prompt, for
  any language, so the user can add project-wide guidance (e.g. "prefer the
  established fan-translation vocabulary", "never use exclamation marks").

A live preview shows the fully-assembled system prompt so the user sees exactly
what the model will receive. The customizations are stored on ``AppSettings`` and
installed into ``ollama_worker`` via ``set_prompt_customizations()`` — every
backend (Ollama, Claude API, Claude Code CLI) routes through
``TranslationRequest.to_system_prompt()``, so all of them honour the edits.

The critical token-preservation rules (2–7) are intentionally *not* editable: a
user rewriting them would silently break `<Alias=…>`, `%s`, `[[STRUCT_BREAK…]]`
and similar placeholders across the whole file.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gui.ollama_worker import (
    _LANG_DISPLAY,
    TranslationRequest,
    default_style_rule,
    get_prompt_customizations,
    set_prompt_customizations,
)

# Order languages sensibly in the combos (source langs first, then the rest).
_LANG_ORDER = ["en", "ru", "uk", "de", "es", "fr", "it", "ja", "ko", "pl", "ptbr", "zhhans"]


def _ordered_langs() -> list[tuple[str, str]]:
    """(code, display-name) pairs in a stable, friendly order."""
    seen = set()
    out: list[tuple[str, str]] = []
    for code in _LANG_ORDER:
        if code in _LANG_DISPLAY:
            out.append((code, _LANG_DISPLAY[code]))
            seen.add(code)
    for code, name in _LANG_DISPLAY.items():
        if code not in seen:
            out.append((code, name))
    return out


class PromptEditorDialog(QDialog):
    """Editor for per-language style rules and a global prompt addendum."""

    def __init__(self, settings, parent=None, source_lang: str = "ru", target_lang: str = "uk"):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Translation Prompt Editor"))
        self.setMinimumSize(920, 620)

        self._settings = settings
        # Working copy of the per-language overrides — committed to settings only on Save.
        self._rules: dict[str, str] = {
            k: v for k, v in dict(getattr(settings, "custom_style_rules", {}) or {}).items()
            if isinstance(v, str)
        }
        self._current_target: str | None = None

        self._build_ui(source_lang, target_lang)
        # Load the initial target's rule and render the first preview.
        self._on_target_changed()

    # ── UI construction ────────────────────────────────────────────────────
    def _build_ui(self, source_lang: str, target_lang: str) -> None:
        root = QVBoxLayout(self)

        intro = QLabel(self.tr(
            "Customize the translation system prompt. Edits apply to every backend "
            "(Ollama, Claude API, Claude Code CLI).\n"
            "The formatting-token rules that protect <Alias=…>, %s, [[STRUCT_BREAK…]] "
            "etc. are fixed and shown in the preview."
        ))
        intro.setWordWrap(True)
        root.addWidget(intro)

        # Language selectors row.
        langs = _ordered_langs()
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel(self.tr("Source:")))
        self.combo_source = QComboBox()
        for code, name in langs:
            self.combo_source.addItem(name, code)
        self._select_code(self.combo_source, source_lang)
        self.combo_source.currentIndexChanged.connect(self._render_preview)
        lang_row.addWidget(self.combo_source)

        lang_row.addSpacing(18)
        lang_row.addWidget(QLabel(self.tr("Target:")))
        self.combo_target = QComboBox()
        for code, name in langs:
            self.combo_target.addItem(name, code)
        self._select_code(self.combo_target, target_lang)
        self.combo_target.currentIndexChanged.connect(self._on_target_changed)
        lang_row.addWidget(self.combo_target)
        lang_row.addStretch(1)
        root.addLayout(lang_row)

        # Splitter: editors on the left, live preview on the right.
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_col = QVBoxLayout(left)
        left_col.setContentsMargins(0, 0, 0, 0)

        style_box = QGroupBox(self.tr("Style rule for target language (Rule 1)"))
        style_layout = QVBoxLayout(style_box)
        self.style_edit = QPlainTextEdit()
        self.style_edit.setPlaceholderText(self.tr(
            "Register, script, quotation and terminology guidance for this language…"
        ))
        self.style_edit.textChanged.connect(self._render_preview)
        style_layout.addWidget(self.style_edit)

        style_btns = QHBoxLayout()
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("promptStatus")
        style_btns.addWidget(self.lbl_status)
        style_btns.addStretch(1)
        self.btn_reset_lang = QPushButton(self.tr("Reset this language"))
        self.btn_reset_lang.clicked.connect(self._reset_current_language)
        style_btns.addWidget(self.btn_reset_lang)
        style_layout.addLayout(style_btns)
        left_col.addWidget(style_box, 3)

        add_box = QGroupBox(self.tr("Extra instructions — appended to every prompt (all languages)"))
        add_layout = QVBoxLayout(add_box)
        self.addendum_edit = QPlainTextEdit()
        self.addendum_edit.setPlaceholderText(self.tr(
            "Optional project-wide guidance, e.g. “Prefer established fan-translation "
            "vocabulary.” Leave blank for none."
        ))
        self.addendum_edit.setPlainText(getattr(self._settings, "custom_prompt_addendum", "") or "")
        self.addendum_edit.textChanged.connect(self._render_preview)
        add_layout.addWidget(self.addendum_edit)
        left_col.addWidget(add_box, 2)

        splitter.addWidget(left)

        preview_box = QGroupBox(self.tr("Preview — assembled system prompt"))
        preview_layout = QVBoxLayout(preview_box)
        self.preview_edit = QPlainTextEdit()
        self.preview_edit.setReadOnly(True)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.Monospace)
        self.preview_edit.setFont(mono)
        self.preview_edit.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        preview_layout.addWidget(self.preview_edit)
        splitter.addWidget(preview_box)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        # Dialog buttons.
        btn_row = QHBoxLayout()
        self.btn_restore_all = QPushButton(self.tr("Restore All Defaults"))
        self.btn_restore_all.clicked.connect(self._restore_all_defaults)
        btn_row.addWidget(self.btn_restore_all)
        btn_row.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        btn_row.addWidget(buttons)
        root.addLayout(btn_row)

    @staticmethod
    def _select_code(combo: QComboBox, code: str) -> None:
        idx = combo.findData(code)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    # ── Editing behaviour ──────────────────────────────────────────────────
    def _flush_current_target(self) -> None:
        """Stash the style editor's text into the working dict for its language."""
        if self._current_target is not None:
            self._rules[self._current_target] = self.style_edit.toPlainText()

    def _on_target_changed(self) -> None:
        """Persist edits for the previous target, then load the new one."""
        self._flush_current_target()
        tgt = self.combo_target.currentData()
        self._current_target = tgt
        text = self._rules.get(tgt, "")
        if not text.strip():
            text = default_style_rule(tgt)
        self.style_edit.blockSignals(True)
        self.style_edit.setPlainText(text)
        self.style_edit.blockSignals(False)
        self._render_preview()

    def _reset_current_language(self) -> None:
        tgt = self.combo_target.currentData()
        self._rules.pop(tgt, None)
        self.style_edit.blockSignals(True)
        self.style_edit.setPlainText(default_style_rule(tgt))
        self.style_edit.blockSignals(False)
        self._render_preview()

    def _restore_all_defaults(self) -> None:
        self._rules.clear()
        self.addendum_edit.blockSignals(True)
        self.addendum_edit.setPlainText("")
        self.addendum_edit.blockSignals(False)
        tgt = self.combo_target.currentData()
        self.style_edit.blockSignals(True)
        self.style_edit.setPlainText(default_style_rule(tgt))
        self.style_edit.blockSignals(False)
        self._render_preview()

    def _update_status(self, tgt: str) -> None:
        default = default_style_rule(tgt)
        current = self.style_edit.toPlainText().strip()
        if current and current != default.strip():
            self.lbl_status.setText(self.tr("● Customized"))
        else:
            self.lbl_status.setText(self.tr("Default"))

    def _render_preview(self) -> None:
        """Rebuild the read-only preview using the current (unsaved) customizations.

        The module-level customization state is briefly swapped to the in-dialog
        values, then restored — the dialog is modal and no batch runs while it is
        open, so this never races the translation workers.
        """
        self._flush_current_target()
        tgt = self.combo_target.currentData()
        src = self.combo_source.currentData()
        self._update_status(tgt)

        saved_rules, saved_addendum = get_prompt_customizations()
        try:
            set_prompt_customizations(self._rules, self.addendum_edit.toPlainText())
            req = TranslationRequest(
                index=0,
                original_text="",
                string_id=0,
                source_lang=src,
                target_lang=tgt,
            )
            text = req.to_system_prompt()
        finally:
            set_prompt_customizations(saved_rules, saved_addendum)
        self.preview_edit.setPlainText(text)

    # ── Commit ─────────────────────────────────────────────────────────────
    def _pruned_rules(self) -> dict[str, str]:
        """Drop blank entries and any override that equals the built-in default."""
        result: dict[str, str] = {}
        for lang, rule in self._rules.items():
            text = (rule or "").strip()
            if text and text != default_style_rule(lang).strip():
                result[lang] = text
        return result

    def _on_save(self) -> None:
        self._flush_current_target()
        self.result_style_rules = self._pruned_rules()
        self.result_addendum = self.addendum_edit.toPlainText().strip()
        self.accept()

    def apply_to_settings(self, settings) -> None:
        """Write the committed customizations onto an AppSettings instance."""
        settings.custom_style_rules = dict(getattr(self, "result_style_rules", {}))
        settings.custom_prompt_addendum = getattr(self, "result_addendum", "")
