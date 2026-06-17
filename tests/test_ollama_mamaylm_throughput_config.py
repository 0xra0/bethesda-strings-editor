"""
Guard tests for the mamaylm two-stream throughput config.

Background: mamaylm-gemma3-12b on the user's 16 GiB AMD RX 6800 used to thrash —
Ollama pre-allocates a full num_ctx KV window *per parallel slot*, so at the old
num_ctx 16384 a single slot already filled the card and a second slot OOM-evicted
the runner mid-batch.  The fix that re-enabled ~2× throughput was to HALVE num_ctx
to 8192 so two slots reserve 2×8192 = the same 16384 footprint that ran stably at
one slot.  These invariants encode that reasoning so a future config tweak can't
silently re-break it (re-introducing either the VRAM thrash or output truncation).

Run with:
    python -m pytest tests/test_ollama_mamaylm_throughput_config.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui.ollama_worker import OllamaWorker  # noqa: E402

# The VRAM footprint (num_ctx × slots) that ran stably as a single slot on the
# 16 GiB card.  Two-up mamaylm must not exceed it.
_STABLE_KV_FOOTPRINT = 16384


def _mamaylm():
    return OllamaWorker.MODEL_CONFIGS["mamaylm"]


def test_mamaylm_runs_two_streams():
    """Parallelism is actually enabled (the whole point of the change)."""
    assert _mamaylm()["max_concurrent"] == 2


def test_mamaylm_two_slots_fit_the_stable_footprint():
    """num_ctx × slots must not exceed the footprint that was stable single-slot."""
    cfg = _mamaylm()
    assert cfg["num_ctx"] * cfg["max_concurrent"] <= _STABLE_KV_FOOTPRINT


def test_chunk_threshold_fits_smallest_model_ctx():
    """A full-size single (non-chunked) call must fit the smallest model num_ctx so
    it is never truncated.  Rough token budget: chars/3.5 for input + a similar-size
    translation + a few hundred prompt/glossary tokens, all under the min num_ctx."""
    min_ctx = min(
        int(c.get("num_ctx", 0))
        for c in OllamaWorker.MODEL_CONFIGS.values()
        if c.get("num_ctx")
    )
    approx_tokens = OllamaWorker._CHUNK_TRANSLATE_THRESHOLD / 3.5 * 2 + 512
    assert approx_tokens < min_ctx


def test_paragraph_split_gate_below_chunk_threshold():
    """Small multi-paragraph strings take one call; the per-paragraph split only
    kicks in above _PP_SPLIT_MIN_CHARS, which must sit below the chunk threshold."""
    assert 0 < OllamaWorker._PP_SPLIT_MIN_CHARS < OllamaWorker._CHUNK_TRANSLATE_THRESHOLD


def test_chunk_pieces_fit_smallest_model_ctx():
    """Each chunk piece (<=_MAX_CHUNK_CHARS) must also comfortably fit the min ctx."""
    min_ctx = min(
        int(c.get("num_ctx", 0))
        for c in OllamaWorker.MODEL_CONFIGS.values()
        if c.get("num_ctx")
    )
    approx_tokens = OllamaWorker._MAX_CHUNK_CHARS / 3.5 * 2 + 512
    assert approx_tokens < min_ctx
