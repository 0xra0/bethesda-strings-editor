"""
Vim-style macro recorder for batch string-table operations.

A MacroRecorder stores a sequence of MacroStep objects and can replay them
against a StringTableModel for bulk find-and-replace / status-setting across
thousands of rows in one shot.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class MacroStepType(str, Enum):
    REGEX_REPLACE = "regex_replace"
    SET_STATUS = "set_status"


@dataclass
class MacroStep:
    step_type: MacroStepType
    args: Dict[str, Any] = field(default_factory=dict)

    def description(self) -> str:
        if self.step_type == MacroStepType.REGEX_REPLACE:
            pattern = self.args.get("pattern", "")
            replacement = self.args.get("replacement", "")
            target = self.args.get("field", "translated").capitalize()
            suffix = " (ignore case)" if self.args.get("ignore_case") else ""
            return f"Replace /{pattern}/ → '{replacement}' in {target}{suffix}"
        if self.step_type == MacroStepType.SET_STATUS:
            return f"Set status → {self.args.get('status', '')}"
        return f"{self.step_type.value} {self.args}"

    def to_dict(self) -> dict:
        return {"type": self.step_type.value, "args": self.args}

    @classmethod
    def from_dict(cls, d: dict) -> "MacroStep":
        return cls(
            step_type=MacroStepType(d["type"]),
            args=d.get("args", {}),
        )


class MacroRecorder:
    """Records a sequence of steps and replays them against a StringTableModel."""

    def __init__(self) -> None:
        self._steps: List[MacroStep] = []

    @property
    def steps(self) -> List[MacroStep]:
        return list(self._steps)

    def clear(self) -> None:
        self._steps.clear()

    def set_steps(self, steps: List[MacroStep]) -> None:
        self._steps = list(steps)

    def replay_on_rows(
        self,
        model,
        rows: List[int],
        progress_callback: Optional[Callable[[int, int], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> int:
        """Apply all steps to each row. Returns count of rows modified."""
        modified = 0
        total = len(rows)
        for i, row_idx in enumerate(rows):
            if should_stop and should_stop():
                break
            if progress_callback:
                progress_callback(i, total)
            if self._apply_to_row(model, row_idx):
                modified += 1
        return modified

    def _apply_to_row(self, model, row_idx: int) -> bool:
        """Apply all steps to one row. Returns True if any step modified the row."""
        if row_idx < 0 or row_idx >= len(model._data):
            return False
        touched = False
        row_data = model._data[row_idx]

        for step in self._steps:
            if step.step_type == MacroStepType.REGEX_REPLACE:
                field_name = step.args.get("field", "translated")
                pattern = step.args.get("pattern", "")
                replacement = step.args.get("replacement", "")
                ignore_case = step.args.get("ignore_case", False)
                if not pattern:
                    continue
                text = row_data.get(field_name, "") or ""
                try:
                    flags = re.IGNORECASE if ignore_case else 0
                    new_text, n = re.subn(pattern, replacement, text, flags=flags)
                except re.error as e:
                    logger.warning("Macro regex error (%r): %s", pattern, e)
                    continue
                if n > 0 and new_text != text:
                    row_data[field_name] = new_text
                    touched = True

            elif step.step_type == MacroStepType.SET_STATUS:
                new_status = step.args.get("status", "translated")
                if row_data.get("status") != new_status:
                    if new_status == "pending":
                        row_data["translated"] = ""
                    row_data["status"] = new_status
                    touched = True

        return touched

    def count_matches(self, model, rows: List[int]) -> int:
        """Dry-run: count rows that would be modified (no changes made)."""
        count = 0
        for row_idx in rows:
            if row_idx < 0 or row_idx >= len(model._data):
                continue
            row_data = model._data[row_idx]
            for step in self._steps:
                if step.step_type == MacroStepType.REGEX_REPLACE:
                    field_name = step.args.get("field", "translated")
                    pattern = step.args.get("pattern", "")
                    ignore_case = step.args.get("ignore_case", False)
                    text = row_data.get(field_name, "") or ""
                    try:
                        flags = re.IGNORECASE if ignore_case else 0
                        if re.search(pattern, text, flags):
                            count += 1
                            break
                    except re.error:
                        pass
                elif step.step_type == MacroStepType.SET_STATUS:
                    new_status = step.args.get("status", "translated")
                    if row_data.get("status") != new_status:
                        count += 1
                        break
        return count

    def to_json(self) -> str:
        return json.dumps(
            {"steps": [s.to_dict() for s in self._steps]},
            ensure_ascii=False,
            indent=2,
        )

    def from_json(self, text: str) -> None:
        data = json.loads(text)
        self._steps = [MacroStep.from_dict(d) for d in data.get("steps", [])]
