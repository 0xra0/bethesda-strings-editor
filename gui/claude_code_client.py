"""
Claude Code CLI backend — a drop-in replacement for :class:`ClaudeClient` that
shells out to the locally-installed ``claude`` command instead of calling the
metered Anthropic API.

Why: the ``claude`` CLI runs on the user's logged-in Claude Code **subscription**
(Pro / Max), so translation, chat, and review cost nothing per token — an
alternative for users who don't want to pay Claude API usage.  The trade-off is
one subprocess spawn per request and whatever latency the CLI adds.

Selection is by model id: any model whose id starts with ``claude-code`` is
routed here (see :func:`is_claude_code_model`).  The pseudo-model id encodes the
CLI ``--model`` alias, e.g. ``claude-code:sonnet`` → ``--model sonnet``.

The public surface mirrors :class:`gui.claude_client.ClaudeClient` (``translate``,
``chat``, ``chat_stream``, ``chat_mcp``, ``review_translation``) so the existing
translation worker and chat panel can use either client interchangeably.

Auth note: ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_AUTH_TOKEN`` are stripped from the
subprocess environment so the CLI always authenticates with the subscription
(OAuth) rather than silently falling back to API-key billing.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Generator, List, Optional

if TYPE_CHECKING:
    from bethesda_strings.character_profiles import CharacterProfile

logger = logging.getLogger(__name__)

# ── Model registry ─────────────────────────────────────────────────────────────
# Pseudo-model ids (id → human label).  The part after ``claude-code:`` is passed
# to the CLI as a ``--model`` alias; the CLI maps the alias to the newest model of
# that tier available on the user's subscription plan.
CLAUDE_CODE_MODELS: Dict[str, str] = {
    "claude-code:haiku":  "Claude Code · Haiku — fast, no API cost (subscription)",
    "claude-code:sonnet": "Claude Code · Sonnet — balanced, no API cost (subscription)",
    "claude-code:opus":   "Claude Code · Opus — highest quality, no API cost (subscription)",
}

DEFAULT_MODEL = "claude-code:sonnet"

# id suffix → CLI --model alias
_CLI_ALIAS: Dict[str, str] = {
    "claude-code":        "sonnet",   # bare id → default tier
    "claude-code:haiku":  "haiku",
    "claude-code:sonnet": "sonnet",
    "claude-code:opus":   "opus",
}

# Per-request timeout (seconds) for a single CLI call.  Override with the
# CLAUDE_CODE_TIMEOUT env var.  One string translation is quick, but subprocess
# spawn + first-token latency can add up, so this is generous.
def _default_timeout() -> float:
    try:
        return max(15.0, float(os.environ.get("CLAUDE_CODE_TIMEOUT", "300")))
    except (TypeError, ValueError):
        return 300.0


def is_claude_code_model(model_name: str) -> bool:
    """Return True when *model_name* selects the Claude Code CLI backend."""
    return bool(model_name) and model_name.startswith("claude-code")


def cli_alias_for(model_name: str) -> str:
    """Map a ``claude-code[:tier]`` id to the CLI ``--model`` alias."""
    return _CLI_ALIAS.get(model_name, "sonnet")


# ── CLI discovery ──────────────────────────────────────────────────────────────

_CLI_PATH_CACHE: Optional[str] = None
_CLI_SENTINEL = object()  # distinguishes "not looked up yet" from "looked up, None"


def find_claude_cli(refresh: bool = False) -> Optional[str]:
    """Locate the ``claude`` executable, or return None if not installed.

    Honours ``CLAUDE_CLI_PATH`` first, then ``PATH`` (``shutil.which``), then a
    few well-known install locations (npm global, the official local installer).
    The result is cached; pass ``refresh=True`` to re-scan.
    """
    global _CLI_PATH_CACHE
    if not refresh and _CLI_PATH_CACHE is not _CLI_SENTINEL and _CLI_PATH_CACHE is not None:
        return _CLI_PATH_CACHE

    override = os.environ.get("CLAUDE_CLI_PATH", "").strip()
    if override and Path(override).exists():
        _CLI_PATH_CACHE = override
        return override

    found = shutil.which("claude")
    if not found:
        home = Path.home()
        candidates = [
            home / ".local/bin/claude",
            home / ".claude/local/claude",
            home / ".npm-global/bin/claude",
            home / "bin/claude",
            Path("/usr/local/bin/claude"),
            Path("/opt/homebrew/bin/claude"),
        ]
        for cand in candidates:
            if cand.exists():
                found = str(cand)
                break

    _CLI_PATH_CACHE = found
    return found


def claude_code_available() -> bool:
    """True when the ``claude`` CLI can be located on this machine."""
    return find_claude_cli() is not None


# ── Client ─────────────────────────────────────────────────────────────────────

class ClaudeCodeError(RuntimeError):
    """Raised when a ``claude`` CLI invocation fails or returns an error result."""


class ClaudeCodeClient:
    """Subscription-backed ``claude`` CLI wrapper with the ClaudeClient interface.

    Instances are cheap and stateless between calls, so one is shared across the
    translation worker's thread pool (each ``translate()`` spawns its own
    subprocess).  All methods block until the CLI exits — call from a worker
    thread, never the GUI thread.

    ``api_key`` and ``mcp_servers`` are accepted for signature parity with
    :class:`ClaudeClient` but ignored: the CLI uses the logged-in subscription,
    and the Messages-API MCP connector does not apply to CLI invocations.
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = DEFAULT_MODEL,
        mcp_servers: "Optional[List[Dict]]" = None,
        timeout: Optional[float] = None,
    ) -> None:
        self.model = model or DEFAULT_MODEL
        self.timeout = timeout if timeout is not None else _default_timeout()
        # Accepted for parity, deliberately unused (CLI uses the subscription).
        self.mcp_servers: List[Dict] = list(mcp_servers or [])
        # Actual token usage accumulated across calls (the CLI reports real
        # usage per request).  Thread-safe because one client is shared across
        # the translation worker's thread pool.  Reset per batch by the worker.
        self._usage_lock = threading.Lock()
        self._usage: Dict = self._zero_usage()

    # ── Low-level CLI plumbing ──────────────────────────────────────────────────

    def _build_argv(self, cli: str, system: str) -> List[str]:
        """Assemble the ``claude -p`` argv for a single non-interactive call.

        ``--system-prompt`` *replaces* Claude Code's default coding-agent prompt
        so the model behaves as a clean translator (no tool/agent baggage).
        ``--strict-mcp-config`` with no ``--mcp-config`` disables any globally
        configured MCP servers so calls stay hermetic and fast.
        """
        argv = [
            cli, "-p",
            "--output-format", "json",
            "--model", cli_alias_for(self.model),
            "--strict-mcp-config",
        ]
        if system:
            argv += ["--system-prompt", system]
        return argv

    def _invoke(self, argv: List[str], prompt: str) -> "subprocess.CompletedProcess":
        """Run the CLI with *prompt* on stdin.  Isolated for test monkeypatching."""
        env = os.environ.copy()
        # Force subscription (OAuth) auth — never silently bill the API.
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
        # Run in an empty scratch dir so no project CLAUDE.md is auto-discovered.
        cwd = Path(tempfile.gettempdir()) / "bse_claude_code_cwd"
        try:
            cwd.mkdir(parents=True, exist_ok=True)
            cwd_str: Optional[str] = str(cwd)
        except OSError:
            cwd_str = None
        return subprocess.run(
            argv,
            input=prompt,
            capture_output=True,
            text=True,
            env=env,
            cwd=cwd_str,
            timeout=self.timeout,
        )

    @staticmethod
    def _parse_result(stdout: str) -> str:
        """Extract the assistant text from ``--output-format json`` stdout.

        The CLI emits a single JSON object: ``{"type":"result","subtype":...,
        "is_error":bool,"result":"<text>", ...}``.  Raises on an error result or
        unparseable output.
        """
        raw = (stdout or "").strip()
        if not raw:
            raise ClaudeCodeError("claude CLI produced no output")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # Some CLI errors print a plain line instead of JSON — surface it.
            raise ClaudeCodeError(raw[:500])
        if isinstance(data, dict):
            if data.get("is_error"):
                raise ClaudeCodeError(str(data.get("result") or data.get("subtype") or "error"))
            result = data.get("result")
            if isinstance(result, str):
                return result.strip()
            raise ClaudeCodeError(f"unexpected CLI result shape: {str(data)[:300]}")
        raise ClaudeCodeError(f"unexpected CLI output: {raw[:300]}")

    def _run(self, system: str, prompt: str) -> str:
        """Single blocking CLI round-trip: system + user prompt → reply text."""
        cli = find_claude_cli()
        if not cli:
            raise ClaudeCodeError(
                "The 'claude' CLI was not found. Install Claude Code and run "
                "'claude' once to log in, or set CLAUDE_CLI_PATH."
            )
        argv = self._build_argv(cli, system)
        try:
            proc = self._invoke(argv, prompt)
        except subprocess.TimeoutExpired:
            raise ClaudeCodeError(f"claude CLI timed out after {self.timeout:.0f}s")
        except FileNotFoundError:
            raise ClaudeCodeError(f"claude CLI not executable at {cli}")
        if proc.returncode != 0:
            # Prefer a parsed JSON error; fall back to stderr.
            try:
                text = self._parse_result(proc.stdout)
            except ClaudeCodeError:
                stderr = (proc.stderr or "").strip()
                raise ClaudeCodeError(
                    stderr[:500] or f"claude CLI exited with code {proc.returncode}"
                )
        else:
            text = self._parse_result(proc.stdout)
        # Only reached when the CLI produced a usable result — record its usage.
        self._accumulate_usage(proc.stdout)
        return text

    # ── Usage accounting ────────────────────────────────────────────────────────

    @staticmethod
    def _zero_usage() -> Dict:
        return {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0}

    def _accumulate_usage(self, stdout: str) -> None:
        """Fold one CLI response's real token usage into the running totals.

        Best-effort: the CLI's ``--output-format json`` reports an Anthropic-style
        ``usage`` block (``input_tokens`` plus ``cache_read_input_tokens`` /
        ``cache_creation_input_tokens``, and ``output_tokens``) and an equivalent
        ``total_cost_usd`` (a reference figure — not billed on a subscription).
        Any parse failure is ignored so usage tracking never breaks a translation.
        """
        try:
            data = json.loads((stdout or "").strip())
        except (json.JSONDecodeError, TypeError, ValueError):
            return
        if not isinstance(data, dict):
            return
        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}

        def _int(v) -> int:
            try:
                return int(v or 0)
            except (TypeError, ValueError):
                return 0

        inp = (_int(usage.get("input_tokens"))
               + _int(usage.get("cache_read_input_tokens"))
               + _int(usage.get("cache_creation_input_tokens")))
        out = _int(usage.get("output_tokens"))
        try:
            cost = float(data.get("total_cost_usd") or 0.0)
        except (TypeError, ValueError):
            cost = 0.0

        with self._usage_lock:
            self._usage["input_tokens"] += inp
            self._usage["output_tokens"] += out
            self._usage["cost_usd"] += cost
            self._usage["calls"] += 1

    def reset_usage(self) -> None:
        """Zero the accumulated usage (called by the worker at batch start)."""
        with self._usage_lock:
            self._usage = self._zero_usage()

    def get_usage(self) -> Dict:
        """Return a snapshot copy of the accumulated usage totals."""
        with self._usage_lock:
            return dict(self._usage)

    @staticmethod
    def _flatten_messages(messages: List[Dict]) -> str:
        """Collapse a Messages-API history into a single CLI prompt string.

        Single user turn → its text verbatim (the common case).  Multi-turn →
        role-labelled lines so the CLI sees the full conversation.
        """
        def _text(content) -> str:
            if isinstance(content, str):
                return content
            if isinstance(content, list):  # list of content blocks
                return " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type", "text") == "text"
                )
            return str(content or "")

        msgs = list(messages or [])
        if len(msgs) == 1 and msgs[0].get("role", "user") == "user":
            return _text(msgs[0].get("content", ""))
        lines: List[str] = []
        for m in msgs:
            role = "User" if m.get("role", "user") == "user" else "Assistant"
            lines.append(f"{role}: {_text(m.get('content', ''))}")
        return "\n\n".join(lines)

    # ── Translation ────────────────────────────────────────────────────────────

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        retry_hint: str = "",
        glossary_snippet: str = "",
        lore_snippet: str = "",
        context_note: str = "",
        character_profile: "Optional[CharacterProfile]" = None,
    ) -> str:
        """Translate *text* using the same prompts as the API/Ollama backends."""
        from gui.ollama_worker import TranslationRequest
        req = TranslationRequest(
            index=0,
            original_text=text,
            string_id=0,
            source_lang=source_lang,
            target_lang=target_lang,
            retry_hint=retry_hint,
            glossary_snippet=glossary_snippet,
            lore_snippet=lore_snippet,
            context_note=context_note,
            character_profile=character_profile,
        )
        return self._run(req.to_system_prompt(), req.to_prompt())

    # ── Chat ───────────────────────────────────────────────────────────────────

    def chat(
        self,
        messages: List[Dict],
        system: str = "",
        max_tokens: int = 2048,  # accepted for parity; CLI has no equivalent
    ) -> str:
        """Send a (possibly multi-turn) conversation and return the reply text."""
        return self._run(system, self._flatten_messages(messages))

    def chat_stream(
        self,
        messages: List[Dict],
        system: str = "",
        max_tokens: int = 2048,
    ) -> Generator[str, None, None]:
        """Non-streaming stand-in for the API's ``chat_stream``.

        Yields the full reply as a single chunk — the chat panel joins chunks, so
        one chunk renders identically to a stream.
        """
        yield self.chat(messages, system=system, max_tokens=max_tokens)

    def chat_mcp(
        self,
        messages: List[Dict],
        system: str = "",
        max_tokens: int = 2048,
        on_tool=None,
    ) -> str:
        """The Messages-API MCP connector doesn't apply to the CLI backend, so
        this just runs a plain chat (parity with :meth:`ClaudeClient.chat_mcp`)."""
        return self.chat(messages, system=system, max_tokens=max_tokens)

    # ── Quality review ─────────────────────────────────────────────────────────

    def review_translation(
        self,
        original: str,
        translation: str,
        source_lang: str = "ru",
        target_lang: str = "uk",
    ) -> str:
        """Ask Claude (via the CLI) to review one translation; return the review."""
        from gui.ollama_worker import _LANG_DISPLAY  # type: ignore[attr-defined]
        src_name = _LANG_DISPLAY.get(source_lang, source_lang.upper())
        tgt_name = _LANG_DISPLAY.get(target_lang, target_lang.upper())

        system = (
            f"You are an expert Bethesda Starfield game localization reviewer "
            f"specializing in {src_name} → {tgt_name} translation. "
            f"Be concise and actionable. Focus on accuracy, natural game dialogue style, "
            f"Bethesda game terminology, and format-tag preservation "
            f"(<Alias=…>, [Attack], [OPTIMIZED], %s, \\n, etc.)."
        )
        user = (
            f"Original ({src_name}):\n{original}\n\n"
            f"Translation ({tgt_name}):\n{translation}\n\n"
            f"Review this translation. "
            f"List specific issues (if any), rate overall quality "
            f"(Poor / Fair / Good / Excellent), "
            f"and if needed provide an improved version."
        )
        return self._run(system, user)
