"""
Shared Claude API client for translation, chat, and quality review.

All Claude-powered features (translation backend, chat assistant, quality
review) use this module so API key management and model selection is centralised.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Model registry ─────────────────────────────────────────────────────────────

CLAUDE_MODELS: Dict[str, str] = {
    "claude-haiku-4-5-20251001": "Claude Haiku 4.5 — fast, great for batch translation",
    "claude-sonnet-4-6":         "Claude Sonnet 4.6 — balanced quality & speed",
    "claude-opus-4-7":           "Claude Opus 4.7 — highest quality, slower",
}

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

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
            system=system,
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
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        response = self._client.messages.create(**kwargs)
        return response.content[0].text

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
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text
