"""
The ти/ви register-consistency guidance moved from a separate post-hoc checker
(removed) into the translation system prompt.  These tests lock that in:

  * the Ukrainian system prompt tells the model to keep ти/ви consistent and not
    to mix the two, and
  * that Ukrainian-specific guidance does not leak into other target languages.

TranslationRequest.to_system_prompt() is a pure string builder (no QThread /
network), so it runs in-process.  Both backends share it (OllamaWorker directly;
claude_client.translate() calls req.to_system_prompt()), so one assertion covers
Ollama and Claude.

Run with:
    python -m pytest tests/test_uk_register_prompt.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui.ollama_worker import TranslationRequest  # noqa: E402


def _system_prompt(target_lang: str, source_lang: str = "en") -> str:
    req = TranslationRequest(
        index=0,
        original_text="Hello there.",
        string_id=1,
        source_lang=source_lang,
        target_lang=target_lang,
    )
    return req.to_system_prompt()


def test_uk_prompt_carries_ty_vy_register_rule():
    sp = _system_prompt("uk")
    assert "ти/ви" in sp
    assert "register consistent" in sp
    # The core instruction: don't mix the two forms.
    assert "never mix ти-forms and ви-forms" in sp
    # Possessives are named so the model treats твій/ваш as register markers too.
    assert "твій/ваш" in sp


def test_uk_register_rule_covers_ru_to_uk_source():
    # The main real-world pair (Russian localization → Ukrainian) still gets it.
    sp = _system_prompt("uk", source_lang="ru")
    assert "ти/ви" in sp
    assert "register consistent" in sp


def test_ty_vy_rule_not_leaked_into_other_targets():
    for lang in ("en", "de", "fr", "pl"):
        sp = _system_prompt(lang)
        assert "ти/ви" not in sp, f"ти/ви leaked into {lang} prompt"
