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
from typing import Dict, Generator, List, Optional

logger = logging.getLogger(__name__)

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

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        import anthropic  # late import — only needed when actually used
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    # ── Translation ────────────────────────────────────────────────────────────

    def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
        retry_hint: str = "",
        glossary_snippet: str = "",
        context_note: str = "",
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
            context_note=context_note,
        )
        system = req.to_system_prompt()
        prompt = req.to_prompt()

        response = self._client.messages.create(
            model=self.model,
            max_tokens=min(4096, max(256, len(text) * 3)),
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
        )
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

        response = self._client.messages.create(**kwargs)
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
            f"(<Alias=…>, [PLYR], [MALE]/[FEMALE], %s, \\n, etc.)."
        )
        user = (
            f"Original ({src_name}):\n{original}\n\n"
            f"Translation ({tgt_name}):\n{translation}\n\n"
            f"Review this translation. "
            f"List specific issues (if any), rate overall quality "
            f"(Poor / Fair / Good / Excellent), "
            f"and if needed provide an improved version."
        )

        response = self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text
