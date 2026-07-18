"""
Shared Claude API client for translation, chat, and quality review.

All Claude-powered features (translation backend, chat assistant, quality
review) use this module so API key management and model selection is centralised.

Prompt caching is enabled on every call: system prompts are marked with
cache_control so repeated requests in a session pay ~10 % of normal input cost
for the stable system-prompt portion.
"""

from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING, Dict, Generator, List, Optional

if TYPE_CHECKING:
    from bethesda_strings.character_profiles import CharacterProfile

logger = logging.getLogger(__name__)

# ── Rate-limit / transient-error backoff ─────────────────────────────────────
# The Anthropic API returns 429 (rate limit) and 5xx (transient) under load. We
# retry those with exponential backoff + jitter, honouring a Retry-After header
# when present. Non-retriable errors (auth, bad-request, 4xx≠429) propagate
# immediately. The SDK's own auto-retry is disabled (max_retries=0) so retries
# aren't doubled.
_RETRY_STATUSES: frozenset[int] = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
_RETRY_EXC_NAMES: frozenset[str] = frozenset({
    "RateLimitError", "InternalServerError", "APIConnectionError",
    "APITimeoutError", "APIConnectionTimeoutError", "OverloadedError",
})
_MAX_RETRIES = 5
_BASE_DELAY = 1.0    # seconds
_MAX_DELAY = 30.0    # cap per sleep


def _retry_after_seconds(exc: object) -> Optional[float]:
    """Extract a Retry-After header (seconds) from an API error, if present."""
    resp = getattr(exc, "response", None)
    headers = getattr(resp, "headers", None)
    if not headers:
        return None
    try:
        val = headers.get("retry-after")
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _is_retriable_error(exc: BaseException) -> bool:
    """True for rate-limit / transient errors that are worth retrying."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status in _RETRY_STATUSES:
        return True
    return type(exc).__name__ in _RETRY_EXC_NAMES

# ── Model registry ─────────────────────────────────────────────────────────────

CLAUDE_MODELS: Dict[str, str] = {
    "claude-haiku-4-5":  "Claude Haiku 4.5 — fast, great for batch translation",
    "claude-sonnet-4-6": "Claude Sonnet 4.6 — balanced quality & speed",
    "claude-opus-4-8":   "Claude Opus 4.8 — highest quality, slower",
}

DEFAULT_MODEL = "claude-haiku-4-5"

# ── Pricing table (USD per million tokens, Anthropic public pricing) ───────────
# cache_write: cost to write a cache entry (first call per session)
# cache_read:  cost to read a cached entry (~10 % of normal input)
CLAUDE_PRICING: Dict[str, Dict[str, float]] = {
    "claude-haiku-4-5":  {"input": 0.80,  "output": 4.00,  "cache_write": 1.00,  "cache_read": 0.08},
    "claude-sonnet-4-6": {"input": 3.00,  "output": 15.00, "cache_write": 3.75,  "cache_read": 0.30},
    "claude-opus-4-8":   {"input": 15.00, "output": 75.00, "cache_write": 18.75, "cache_read": 1.50},
}

_CHARS_PER_TOKEN = 3.5   # conservative chars-per-token ratio for mixed EN/CYR text
_SYS_PROMPT_TOKENS = 1220  # measured: TranslationRequest.to_system_prompt() ≈ 4250 chars


def estimate_batch_cost(model: str, requests: list) -> Dict[str, float]:
    """
    Estimate Claude API cost for a translation batch (offline, no API call).

    Uses character-based token approximation (chars / 3.5) and models Anthropic
    prompt caching: the system prompt is written to cache on the first request
    and read from cache on all subsequent ones.

    Returns a dict with keys:
        input_tokens, output_tokens, cache_write_tokens, cache_read_tokens,
        cost_with_cache, cost_without_cache, cache_savings_pct
    """
    price = CLAUDE_PRICING.get(model, CLAUDE_PRICING[DEFAULT_MODEL])

    user_tokens_list = [len(r.original_text) / _CHARS_PER_TOKEN for r in requests]
    output_tokens_list = [len(r.original_text) / 3.0 for r in requests]

    n = len(requests)
    total_user_input = sum(user_tokens_list)
    total_output = sum(output_tokens_list)

    # First request: full system prompt (input) + cache write
    # Remaining requests: cached system prompt read + user input
    if n == 0:
        return {
            "input_tokens": 0, "output_tokens": 0,
            "cache_write_tokens": 0, "cache_read_tokens": 0,
            "cost_with_cache": 0.0, "cost_without_cache": 0.0,
            "cache_savings_pct": 0.0,
        }

    # Tokens billed at normal input rate: first sys prompt + all user prompts
    normal_input = _SYS_PROMPT_TOKENS + total_user_input
    cache_write  = float(_SYS_PROMPT_TOKENS)         # written once
    cache_read   = _SYS_PROMPT_TOKENS * max(0, n - 1)  # read on remaining

    cost_with_cache = (
        normal_input   / 1_000_000 * price["input"]
        + cache_write  / 1_000_000 * price["cache_write"]
        + cache_read   / 1_000_000 * price["cache_read"]
        + total_output / 1_000_000 * price["output"]
    )

    # What it would cost with no caching at all
    cost_no_cache = (
        (_SYS_PROMPT_TOKENS * n + total_user_input) / 1_000_000 * price["input"]
        + total_output / 1_000_000 * price["output"]
    )

    savings_pct = (1 - cost_with_cache / cost_no_cache) * 100 if cost_no_cache > 0 else 0.0

    return {
        "input_tokens":      int(normal_input + cache_read),
        "output_tokens":     int(total_output),
        "cache_write_tokens": int(cache_write),
        "cache_read_tokens":  int(cache_read),
        "cost_with_cache":   cost_with_cache,
        "cost_without_cache": cost_no_cache,
        "cache_savings_pct": savings_pct,
    }


# Key used in the app's SecretStore
_SECRET_KEY = "anthropic-api-key"

# Beta flag required by the Messages API MCP connector (remote MCP servers).
_MCP_BETA = "mcp-client-2025-11-20"


def is_claude_model(model_name: str) -> bool:
    """Return True when *model_name* identifies a Claude model."""
    return model_name.startswith("claude-")


# ── API key helpers ────────────────────────────────────────────────────────────

def get_api_key() -> Optional[str]:
    """Retrieve the Anthropic API key from SecretStore (returns None if not set)."""
    try:
        from gui.secret_store import SecretStore
        return SecretStore().get(_SECRET_KEY) or None
    except Exception as exc:
        logger.warning("Could not read Claude API key: %s", exc)
        return None


def set_api_key(key: str) -> bool:
    """Persist the Anthropic API key to SecretStore.  Returns True on success."""
    try:
        from gui.secret_store import SecretStore
        SecretStore().set(_SECRET_KEY, key.strip())
        return True
    except Exception as exc:
        logger.error("Could not save Claude API key: %s", exc)
        return False


def clear_api_key() -> None:
    """Remove the stored Anthropic API key."""
    try:
        from gui.secret_store import SecretStore
        SecretStore().delete(_SECRET_KEY)
    except Exception:
        pass


# ── Client ─────────────────────────────────────────────────────────────────────

class ClaudeClient:
    """
    Thin synchronous wrapper around the Anthropic Python SDK.

    Instantiate with an API key and model name.  All methods block
    until the API responds — call from a worker thread, not the GUI thread.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        mcp_servers: "Optional[List[Dict]]" = None,
    ) -> None:
        import anthropic  # late import — only needed when actually used
        # max_retries=0: we do our own 429/5xx backoff in _create_message() so the
        # two mechanisms don't compound into very long stalls.
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=0)
        self.model = model
        # Remote MCP servers Claude may call tools on via the Messages API MCP
        # connector.  Each entry: {"name": str, "url": str,
        # "authorization_token": str (optional)}.  Empty = no MCP tools.
        self.mcp_servers: List[Dict] = list(mcp_servers or [])

    # ── Request helper with backoff ──────────────────────────────────────────────

    def _create_message(self, beta: bool = False, **kwargs):
        """Call messages.create with exponential-backoff retry on 429/5xx.

        Honours a Retry-After header when the server sends one; otherwise uses
        exponential backoff with jitter, capped at _MAX_DELAY per sleep and
        _MAX_RETRIES attempts. Non-retriable errors propagate on the first try.
        """
        create = (
            self._client.beta.messages.create if beta
            else self._client.messages.create
        )
        delay = _BASE_DELAY
        for attempt in range(_MAX_RETRIES):
            try:
                return create(**kwargs)
            except BaseException as exc:
                if not _is_retriable_error(exc) or attempt == _MAX_RETRIES - 1:
                    raise
                wait = _retry_after_seconds(exc)
                if wait is None:
                    wait = min(delay, _MAX_DELAY) + random.uniform(0, 0.5)
                    delay *= 2
                else:
                    wait = min(wait, _MAX_DELAY)
                logger.warning(
                    "Claude API %s — retry %d/%d in %.1fs",
                    type(exc).__name__, attempt + 1, _MAX_RETRIES - 1, wait,
                )
                time.sleep(wait)
        # Unreachable: the final attempt either returns or re-raises above.
        raise RuntimeError("Claude request retry loop exited without a result")

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
        """
        Translate *text* from *source_lang* to *target_lang*.

        Reuses the same system-prompt and user-turn format as OllamaWorker so
        the model receives consistent instructions regardless of which backend
        is active.
        """
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
        system = req.to_system_prompt()
        prompt = req.to_prompt()

        # Build API kwargs — add temperature only when a profile overrides it
        api_kwargs: dict = {
            "model": self.model,
            "max_tokens": min(4096, max(256, len(text) * 3)),
            "system": [{"type": "text", "text": system,
                        "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": prompt}],
        }
        if character_profile and character_profile.temperature is not None:
            api_kwargs["temperature"] = character_profile.temperature

        response = self._create_message(**api_kwargs)
        return response.content[0].text.strip()

    # ── Chat ───────────────────────────────────────────────────────────────────

    def chat(
        self,
        messages: List[Dict],
        system: str = "",
        max_tokens: int = 2048,
    ) -> str:
        """
        Send a multi-turn conversation to Claude.

        *messages* is a list of ``{"role": "user"|"assistant", "content": "…"}``
        dicts (standard Anthropic Messages API format).
        Returns the assistant's reply text.
        """
        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = [{"type": "text", "text": system,
                                  "cache_control": {"type": "ephemeral"}}]

        response = self._create_message(**kwargs)
        return response.content[0].text

    def chat_stream(
        self,
        messages: List[Dict],
        system: str = "",
        max_tokens: int = 2048,
    ) -> Generator[str, None, None]:
        """
        Streaming variant of :meth:`chat`.  Yields text delta chunks so the
        caller can display tokens as they arrive.
        """
        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = [{"type": "text", "text": system,
                                  "cache_control": {"type": "ephemeral"}}]

        with self._client.messages.stream(**kwargs) as stream:
            yield from stream.text_stream

    # ── MCP connector (remote MCP-server tools) ─────────────────────────────────

    def _mcp_request_kwargs(self) -> dict:
        """Build the MCP-connector request kwargs, or ``{}`` when unconfigured.

        Returns the ``betas`` / ``mcp_servers`` / ``tools`` trio the Messages
        API needs so Claude can call tools on remote MCP servers.  Every server
        in ``mcp_servers`` must be paired with an ``mcp_toolset`` tool that
        references it by name, or the API rejects the whole request — so both
        halves are built here together.  Rows missing a name or URL are skipped
        rather than sent (they would 400).
        """
        servers: List[Dict] = []
        tools: List[Dict] = []
        for entry in self.mcp_servers:
            name = (entry.get("name") or "").strip()
            url = (entry.get("url") or "").strip()
            if not name or not url:
                continue
            server: Dict = {"type": "url", "url": url, "name": name}
            token = (entry.get("authorization_token") or "").strip()
            if token:
                server["authorization_token"] = token
            servers.append(server)
            tools.append({"type": "mcp_toolset", "mcp_server_name": name})
        if not servers:
            return {}
        return {"betas": [_MCP_BETA], "mcp_servers": servers, "tools": tools}

    def chat_mcp(
        self,
        messages: List[Dict],
        system: str = "",
        max_tokens: int = 2048,
        on_tool=None,
    ) -> str:
        """Multi-turn chat in which Claude may call tools on the configured
        remote MCP servers (Messages API MCP connector).

        Falls back to a plain :meth:`chat` when no MCP servers are configured,
        so callers can use it unconditionally.  The connector runs server-side:
        Anthropic connects to each server and executes the tools, returning
        ``mcp_tool_use`` / ``mcp_tool_result`` blocks in the response.  A
        ``pause_turn`` stop reason means the server-side tool loop reached its
        per-request iteration cap — we re-send the accumulated turn to resume.
        ``on_tool(name)`` is invoked for each MCP tool Claude calls (UI hook).
        Returns the assistant's final reply text.
        """
        mcp = self._mcp_request_kwargs()
        if not mcp:
            return self.chat(messages, system=system, max_tokens=max_tokens)

        convo: List[Dict] = list(messages)
        text_parts: List[str] = []
        for _ in range(8):  # bound pause_turn continuations
            kwargs: dict = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": convo,
                **mcp,
            }
            if system:
                kwargs["system"] = [{"type": "text", "text": system,
                                     "cache_control": {"type": "ephemeral"}}]

            response = self._create_message(beta=True, **kwargs)
            for block in response.content:
                btype = getattr(block, "type", "")
                if btype == "text":
                    text_parts.append(block.text)
                elif btype == "mcp_tool_use" and on_tool is not None:
                    try:
                        on_tool(getattr(block, "name", "") or "")
                    except Exception:
                        pass

            if getattr(response, "stop_reason", None) == "pause_turn":
                # Resume the server-side tool loop by echoing the paused turn.
                convo = convo + [{"role": "assistant", "content": response.content}]
                continue
            break

        return "".join(text_parts).strip()

    # ── Quality review ─────────────────────────────────────────────────────────

    def review_translation(
        self,
        original: str,
        translation: str,
        source_lang: str = "ru",
        target_lang: str = "uk",
    ) -> str:
        """
        Ask Claude to review a single translation and return structured feedback.

        Covers: accuracy, naturalness, game terminology, format-tag preservation,
        and language-specific issues.  Returns a human-readable review string.
        """
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

        # Scale the budget with the text being reviewed, like translate() does.
        # A flat 1024 truncated the review of any book or note mid-sentence —
        # and because the reply is appended to the chat history verbatim, the
        # half-finished text then rode along into the next Suggest request.
        # The review restates the translation and may append an improved
        # version, so budget for both plus the prose around them.
        review_budget = min(4096, max(1024, (len(original) + len(translation)) * 2))

        response = self._create_message(
            model=self.model,
            max_tokens=review_budget,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text
