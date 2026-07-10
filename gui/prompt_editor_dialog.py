"""
Translation Prompt Editor.

Lets the user customize the translation *system prompt* without touching code:

* **Tuning dials** — structured knobs (language style, formality, vocabulary,
  grammar, expression/localization, translation rigor) that add a "Translation
  preferences" block to the prompt. Only the knobs the user turns contribute
  text; neutral/default choices add nothing. Driven from
  ``ollama_worker.PROMPT_DIALS`` so the UI and the prompt builder never drift.
* **Rule 1 (style/register)** can be overridden per target language. This is the
  layer that carries the language's voice — formal vs casual, script/quotation
  conventions, terminology preferences.
* **Extra instructions** are appended verbatim to every translation prompt, for
  any language, so the user can add project-wide guidance.

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
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gui.ollama_worker import (
    _LANG_DISPLAY,
    PROMPT_DIALS,
    TranslationRequest,
    default_style_rule,
    get_player_gender,
    get_prompt_customizations,
    set_player_gender,
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
    """Editor for tuning dials, per-language style rules, and a global addendum."""

    def __init__(self, settings, parent=None, source_lang: str = "ru", target_lang: str = "uk"):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Translation Prompt Editor"))
        self.setMinimumSize(1040, 700)

        self._settings = settings
        # Working copy of the per-language overrides — committed to settings only on Save.
        self._rules: dict[str, str] = {
            k: v for k, v in dict(getattr(settings, "custom_style_rules", {}) or {}).items()
            if isinstance(v, str)
        }
        self._current_target: str | None = None
        # key → ("single", QComboBox) | ("multi", {option_key: QCheckBox})
        self._dial_widgets: dict = {}

        self._build_ui(source_lang, target_lang)
        self._load_dials(dict(getattr(settings, "custom_prompt_dials", {}) or {}))
        self._set_player_gender(getattr(settings, "player_gender", "") or "")
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

        # Splitter: editors on the left (scrollable), live preview on the right.
        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_col = QVBoxLayout(left)
        left_col.setContentsMargins(0, 0, 0, 0)

        left_col.addWidget(self._build_dials_group())

        style_box = QGroupBox(self.tr("Style rule for target language (Rule 1)"))
        style_layout = QVBoxLayout(style_box)
        self.style_edit = QPlainTextEdit()
        self.style_edit.setPlaceholderText(self.tr(
            "Register, script, quotation and terminology guidance for this language…"
        ))
        self.style_edit.setMinimumHeight(120)
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
        left_col.addWidget(style_box)

        add_box = QGroupBox(self.tr("Extra instructions — appended to every prompt (all languages)"))
        add_layout = QVBoxLayout(add_box)
        self.addendum_edit = QPlainTextEdit()
        self.addendum_edit.setPlaceholderText(self.tr(
            "Optional project-wide guidance, e.g. “Prefer established fan-translation "
            "vocabulary.” Leave blank for none."
        ))
        self.addendum_edit.setMinimumHeight(80)
        self.addendum_edit.setPlainText(getattr(self._settings, "custom_prompt_addendum", "") or "")
        self.addendum_edit.textChanged.connect(self._render_preview)
        add_layout.addWidget(self.addendum_edit)
        left_col.addWidget(add_box)
        left_col.addStretch(1)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(left)
        splitter.addWidget(left_scroll)

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

    def _build_dials_group(self) -> QGroupBox:
        """Build the 'Translation preferences' group from the PROMPT_DIALS spec."""
        box = QGroupBox(self.tr("Translation preferences"))
        form = QFormLayout(box)
        # Player character gender — a separate module-level layer (not a PROMPT_DIALS
        # entry), so it is not stored in / collected from _dial_widgets.
        self.combo_player_gender = QComboBox()
        for label, value in (
            (self.tr("Unspecified"), ""),
            (self.tr("Male"), "male"),
            (self.tr("Female"), "female"),
            (self.tr("Neutral (avoid gendered forms)"), "neutral"),
        ):
            self.combo_player_gender.addItem(label, value)
        self.combo_player_gender.setToolTip(self.tr(
            "Grammatical gender for player-referring text in gendered target languages. "
            "Only affects languages that inflect for gender (Ukrainian, Polish, German, …)."
        ))
        self.combo_player_gender.currentIndexChanged.connect(self._render_preview)
        form.addRow(self.tr("Player gender:"), self.combo_player_gender)
        for spec in PROMPT_DIALS:
            label = self.tr(spec["label"]) + ":"
            if spec["multi"]:
                container = QWidget()
                grid = QGridLayout(container)
                grid.setContentsMargins(0, 0, 0, 0)
                checks: dict = {}
                for i, (okey, olabel, _instr) in enumerate(spec["options"]):
                    cb = QCheckBox(self.tr(olabel))
                    cb.toggled.connect(self._render_preview)
                    grid.addWidget(cb, i // 2, i % 2)
                    checks[okey] = cb
                self._dial_widgets[spec["key"]] = ("multi", checks)
                form.addRow(label, container)
            else:
                combo = QComboBox()
                for okey, olabel, _instr in spec["options"]:
                    combo.addItem(self.tr(olabel), okey)
                combo.currentIndexChanged.connect(self._render_preview)
                self._dial_widgets[spec["key"]] = ("single", combo)
                form.addRow(label, combo)
        return box

    @staticmethod
    def _select_code(combo: QComboBox, code: str) -> None:
        idx = combo.findData(code)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    # ── Dials ──────────────────────────────────────────────────────────────
    def _load_dials(self, dials: dict) -> None:
        """Set the dial widgets from a saved dials dict (signals blocked)."""
        for key, (kind, widget) in self._dial_widgets.items():
            val = dials.get(key)
            if kind == "single":
                widget.blockSignals(True)
                idx = widget.findData(val) if val else -1
                widget.setCurrentIndex(idx if idx >= 0 else 0)
                widget.blockSignals(False)
            else:  # multi
                selected = set(val or [])
                for okey, cb in widget.items():
                    cb.blockSignals(True)
                    cb.setChecked(okey in selected)
                    cb.blockSignals(False)

    def _collect_dials(self) -> dict:
        """Read the dial widgets into a dict, omitting default/empty choices."""
        out: dict = {}
        for spec in PROMPT_DIALS:
            kind, widget = self._dial_widgets[spec["key"]]
            if kind == "single":
                val = widget.currentData()
                if val and val != spec["default"]:
                    out[spec["key"]] = val
            else:  # multi
                selected = [okey for okey, cb in widget.items() if cb.isChecked()]
                if selected:
                    out[spec["key"]] = selected
        return out

    def _set_player_gender(self, value: str) -> None:
        """Select the player-gender combo entry for *value* (signals blocked)."""
        idx = self.combo_player_gender.findData(value or "")
        self.combo_player_gender.blockSignals(True)
        self.combo_player_gender.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_player_gender.blockSignals(False)

    def _player_gender_value(self) -> str:
        """The currently-selected player gender ("" when Unspecified)."""
        return self.combo_player_gender.currentData() or ""

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
        self._load_dials({})
        self._set_player_gender("")
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

        saved_rules, saved_addendum, saved_dials = get_prompt_customizations()
        saved_gender = get_player_gender()
        try:
            set_prompt_customizations(
                self._rules, self.addendum_edit.toPlainText(), self._collect_dials()
            )
            set_player_gender(self._player_gender_value())
            req = TranslationRequest(
                index=0,
                original_text="",
                string_id=0,
                source_lang=src,
                target_lang=tgt,
            )
            text = req.to_system_prompt()
        finally:
            set_prompt_customizations(saved_rules, saved_addendum, saved_dials)
            set_player_gender(saved_gender)
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
        self.result_dials = self._collect_dials()
        self.result_player_gender = self._player_gender_value()
        self.accept()

    def apply_to_settings(self, settings) -> None:
        """Write the committed customizations onto an AppSettings instance."""
        settings.custom_style_rules = dict(getattr(self, "result_style_rules", {}))
        settings.custom_prompt_addendum = getattr(self, "result_addendum", "")
        settings.custom_prompt_dials = dict(getattr(self, "result_dials", {}))
        settings.player_gender = getattr(self, "result_player_gender", "")
