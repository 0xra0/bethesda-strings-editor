"""Centralized logging configuration with colored level tags.

The console renders each record's level as a colored, bracketed tag — `[INFO]`,
`[WARN]`, `[ERROR]`, etc. — so the severity stands out at a glance. The on-disk
log (`translator.log`) uses the same layout but with no ANSI escape codes, so it
stays grep-friendly and free of terminal control characters.

Colors are emitted only when the stream is an interactive terminal. Set
``NO_COLOR`` to force plain output, or ``FORCE_COLOR`` to force colors even when
the output is redirected.
"""
import logging
import os
import sys

# ANSI SGR codes used to color the bracketed level tag on the console.
_RESET = "\033[0m"
_LEVEL_COLORS = {
    logging.DEBUG: "\033[90m",          # bright black / grey
    logging.INFO: "\033[32m",           # green
    logging.WARNING: "\033[33m",        # yellow
    logging.ERROR: "\033[31m",          # red
    logging.CRITICAL: "\033[1;97;41m",  # bold white on red
}

# Short, mostly fixed-width tags. WARNING -> WARN, CRITICAL -> CRIT.
_LEVEL_TAGS = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARN",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRIT",
}

# Widest bracketed tag is "[ERROR]"/"[DEBUG]" (7 chars); pad the rest to align.
_TAG_WIDTH = 7

_DATEFMT = "%Y-%m-%d %H:%M:%S"
_FORMAT = "%(asctime)s %(level_tag)s %(name)s: %(message)s"


def _supports_color(stream) -> bool:
    """Return True if *stream* is a terminal that should receive ANSI colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty()


class LevelTagFormatter(logging.Formatter):
    """Formatter that renders the level as a bracketed tag, optionally colored.

    Padding is applied *outside* the color codes so columns stay aligned
    regardless of whether the tag is colored.
    """

    def __init__(self, *args, color: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self._color = color

    def format(self, record: logging.LogRecord) -> str:
        tag = _LEVEL_TAGS.get(record.levelno, record.levelname)
        bracket = f"[{tag}]"
        pad = " " * max(0, _TAG_WIDTH - len(bracket))
        if self._color:
            color = _LEVEL_COLORS.get(record.levelno, "")
            record.level_tag = f"{color}{bracket}{_RESET}{pad}"
        else:
            record.level_tag = f"{bracket}{pad}"
        return super().format(record)


def setup_logging(level: int = logging.INFO, log_file: str = "translator.log") -> logging.Logger:
    """Configure the root logger with a colored console + plain file handler.

    Idempotent: any handlers already attached to the root logger are removed
    first, so calling this more than once does not duplicate output.
    """
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(
        LevelTagFormatter(_FORMAT, datefmt=_DATEFMT, color=_supports_color(sys.stdout))
    )
    root.addHandler(console)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(LevelTagFormatter(_FORMAT, datefmt=_DATEFMT, color=False))
        root.addHandler(file_handler)

    return root
