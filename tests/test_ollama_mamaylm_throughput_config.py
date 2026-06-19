"""
Guard tests for the mamaylm throughput / stability config.

Background: mamaylm-gemma3-12b on the user's 16 GiB AMD RX 6800 thrashed under
sustained batch translation.  Ollama pre-allocates a full num_ctx KV window *per
parallel slot*, so weights (~7.5 GiB Q4) + 2 slots × 8192 KV + compute buffers sat
right on the card's 16 GiB limit — the desktop compositor/browser taking a couple of
GiB tipped it over, ROCm evicted the runner mid-batch, and Ollama reloaded the model
(observed as "VRAM drops then refills" and 1706 s / 2996 s zero-token wedges).  Two
further costs: every short string was allowed to generate up to num_predict 4096
tokens (a general fine-tune rambles past the stop token on game fragments), and the
per-request num_ctx stepped 4096→8192 mid-batch, forcing a context-resize reload.

The fix has three parts.  (1) Two-up at HALF the context: 2 slots × num_ctx 8192 =
16384 KV = the footprint that ran stably at one slot × 16384, so two streams give ~2×
throughput without exceeding it — the real 49 049-string run finished two-up in 8.65 h
with zero timeouts, and single-stream would roughly double that.  (2) pin_num_ctx so
num_ctx is FIXED per request and a short→long string never forces a context-resize
reload mid-batch.  (3) num_predict 512 (a cap, not a target) so a misbehaving short
string can't ramble to a 4096-token budget and pin a slot — the adaptive path still
raises the budget for genuinely long strings.  These invariants encode that reasoning
so a future config tweak can't silently re-break it (re-introducing the VRAM thrash,
reload churn, or wasted generation).

Run with:
    python -m pytest tests/test_ollama_mamaylm_throughput_config.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui.ollama_worker import OllamaWorker, TranslationRequest  # noqa: E402

# The VRAM footprint (num_ctx × slots) that ran stably as a single slot on the
# 16 GiB card.  mamaylm must not exceed it.
_STABLE_KV_FOOTPRINT = 16384


def _mamaylm():
    return OllamaWorker.MODEL_CONFIGS["mamaylm"]


def test_mamaylm_runs_two_streams():
    """Two-up is the real throughput lever (the 49k run completed two-up in 8.65h
    with zero timeouts); single-stream would roughly double wall time."""
    assert _mamaylm()["max_concurrent"] == 2


def test_mamaylm_slots_fit_the_stable_footprint():
    """num_ctx × slots must not exceed the footprint that was stable single-slot."""
    cfg = _mamaylm()
    assert cfg["num_ctx"] * cfg["max_concurrent"] <= _STABLE_KV_FOOTPRINT


def test_mamaylm_pins_num_ctx():
    """A fixed context size means no short→long resize reload mid-batch."""
    assert _mamaylm().get("pin_num_ctx") is True


def test_mamaylm_num_predict_is_tight():
    """Short game strings must not be allowed to ramble to a huge token cap.
    The adaptive path still raises the budget (input_len×4, up to
    self.ollama_num_predict) for genuinely long strings, so this is a floor, not a
    truncation."""
    assert _mamaylm()["num_predict"] <= 1024


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


# ── runtime num_ctx pin (the actual reload trigger) ──────────────────────────────
#
# The config flag pin_num_ctx is only half the fix — every code path that builds an
# Ollama payload must *honour* it.  The "model keeps restarting mid-batch" symptom was
# the rewrite path (_call_ollama_rewrite, fired on every Russian-leakage cleanup —
# a large share of strings on mamaylm-v2.0) sending an adaptive num_ctx=4096 for short
# strings while the main translate/chunk paths sent the model's 8192, so the runner
# reloaded to resize its KV window (8192⇄4096) on roughly every other string.  Ollama
# only reloads on LOAD-TIME option changes (num_ctx the dominant one); sampling params
# never trigger it.  So the invariant a pinned model needs is: every payload-building
# path sends the *same fixed* num_ctx (the model's configured value), regardless of the
# input length or the user's num_ctx slider.  These tests pin that down at the payload
# level — config-only assertions above can't catch a path that ignores the flag.


def _capture_num_ctx(worker, call):
    """Run *call* with worker._stream_ollama stubbed; return the sent options.num_ctx."""
    captured = {}

    def _fake_stream(payload, *_):  # extra arg = the timeout _stream_ollama is called with
        captured["num_ctx"] = payload["options"]["num_ctx"]
        # A clean Ukrainian string so no downstream echo/leakage rewrite fires.
        return "Привіт, світ і друзі"

    worker._stream_ollama = _fake_stream  # type: ignore[method-assign]
    call(worker)
    return captured["num_ctx"]


def _req(text="Hello world"):
    return TranslationRequest(
        index=0, original_text=text, string_id=1,
        source_lang="en", target_lang="ukrainian",
    )


def test_pinned_rewrite_path_sends_model_num_ctx_for_short_input():
    """The rewrite path used adaptive num_ctx (4096 for a short string).  On a pinned
    model it must send the model's configured num_ctx instead — this is the exact path
    that forced the mid-batch reload."""
    w = OllamaWorker(model="mamaylm-gemma3-12b-v2.0:latest",
                     enable_term_protection=False, ollama_num_ctx=16384)
    sent = _capture_num_ctx(w, lambda x: x._call_ollama_rewrite("sys", "prompt", input_len=20))
    assert sent == _mamaylm()["num_ctx"] == 8192


def test_pinned_chunk_path_sends_model_num_ctx_for_short_input():
    w = OllamaWorker(model="mamaylm-gemma3-12b-v2.0:latest",
                     enable_term_protection=False, ollama_num_ctx=16384)
    sent = _capture_num_ctx(w, lambda x: x._call_ollama_chunk(_req(), "Hello world", 1, 1))
    assert sent == _mamaylm()["num_ctx"] == 8192


def test_pinned_paths_send_identical_num_ctx():
    """Every payload-building path on a pinned model must agree byte-for-byte, or the
    runner reloads when control passes between them mid-batch."""
    w = OllamaWorker(model="mamaylm-gemma3-12b-v2.0:latest",
                     enable_term_protection=False, ollama_num_ctx=16384)
    rewrite = _capture_num_ctx(w, lambda x: x._call_ollama_rewrite("sys", "prompt", input_len=20))
    chunk = _capture_num_ctx(w, lambda x: x._call_ollama_chunk(_req(), "Hello world", 1, 1))
    assert rewrite == chunk == _mamaylm()["num_ctx"]


def test_pinned_num_ctx_ignores_user_slider():
    """The pinned value is the VRAM-tuned model setting; the user's num_ctx slider must
    not clamp it (down OR up), or two users with different sliders would each thrash a
    different way.  Both a low (4096) and high (32768) slider must yield the model's
    8192."""
    for slider in (4096, 32768):
        w = OllamaWorker(model="mamaylm-gemma3-12b-v2.0:latest",
                         enable_term_protection=False, ollama_num_ctx=slider)
        sent = _capture_num_ctx(w, lambda x: x._call_ollama_rewrite("sys", "prompt", input_len=20))
        assert sent == _mamaylm()["num_ctx"] == 8192, f"slider={slider} leaked into num_ctx"


def test_unpinned_model_keeps_adaptive_num_ctx():
    """Contrast: a non-pinned model still scales num_ctx to the input — proving the pin
    flag (not some unrelated change) is what fixes the short-string num_ctx for mamaylm.
    A short rewrite on translategemma3-st (num_ctx 16384, unpinned) takes the small
    adaptive 4096 step, not the model max."""
    w = OllamaWorker(model="translategemma3-st",
                     enable_term_protection=False, ollama_num_ctx=16384)
    sent = _capture_num_ctx(w, lambda x: x._call_ollama_rewrite("sys", "prompt", input_len=20))
    assert sent == 4096
    assert sent < int(OllamaWorker.MODEL_CONFIGS["translategemma3-st"]["num_ctx"])
