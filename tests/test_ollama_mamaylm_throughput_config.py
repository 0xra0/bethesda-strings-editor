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

from gui.ollama_worker import (  # noqa: E402
    OllamaWorker,
    TranslationRequest,
    _restore_line_structure,
)

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


# ── newline-structure safety net (paragraph-by-paragraph re-fill) ─────────────────
#
# mamaylm (a general fine-tune) can collapse, misplace, or invent paragraph breaks on
# a SHORT blank-line-delimited note that falls below the proactive _PP_SPLIT_MIN_CHARS
# gate, so it goes through a single call.  The real symptom (xTranslator export of an
# earlier run): a 3-paragraph data slate came back with the first \n\n dropped and a
# spurious \n inserted mid-sentence, drifting the QC newline count 6 → 5.  The
# mechanical restores re-insert *missing* newlines but can't undo a misplaced one, so
# the count stays wrong.  The fix: when the count still disagrees after the single-call
# restores, retranslate paragraph-by-paragraph and rejoin with app-controlled \n\n —
# the structure then matches the source by construction.  These tests pin that down.


def _mamaylm_worker():
    return OllamaWorker(model="mamaylm-gemma3-12b-v2.0:latest",
                        enable_term_protection=False, ollama_num_ctx=8192)


def test_translate_paragraphs_rejoins_with_app_separators():
    """The helper translates each non-empty paragraph and rejoins with \\n\\n, keeping
    empty (e.g. trailing) paragraphs verbatim — so the newline count matches the source
    no matter what the per-paragraph model output looks like."""
    w = _mamaylm_worker()
    seen: list = []

    def _fake_single(sub):
        seen.append(sub.original_text)
        return f"Абзац-{len(seen)}"

    w._translate_single = _fake_single  # type: ignore[method-assign]
    req = _req("Alpha one.\r\n\r\nBeta two.\r\n\r\nGamma three.\r\n\r\n")
    out = w._translate_paragraphs(req, "", "")
    assert seen == ["Alpha one.", "Beta two.", "Gamma three."]
    assert out == "Абзац-1\n\nАбзац-2\n\nАбзац-3\n\n"
    assert w._nl_count(out) == w._nl_count(req.original_text) == 6


def test_translate_paragraphs_returns_none_for_single_paragraph():
    """Not multi-paragraph → return None so the caller keeps the single-call result
    (the helper must never fire its extra per-paragraph calls on a plain string)."""
    w = _mamaylm_worker()

    def _must_not_call(*_):
        raise AssertionError("single paragraph must not be split")

    w._translate_single = _must_not_call  # type: ignore[method-assign]
    assert w._translate_paragraphs(_req("Just one paragraph, no breaks."), "", "") is None


def test_nl_count_matches_qc_formula():
    """_nl_count must count literal \\n escapes + real LF, with CRLF counting as one —
    identical to the QC newline check it guards against."""
    assert OllamaWorker._nl_count("a\r\n\r\nb\r\n\r\nc\r\n\r\n") == 6   # CRLF source
    assert OllamaWorker._nl_count("a\n\nb\n\nc\n\n") == 6               # LF translation
    assert OllamaWorker._nl_count("a\\nb\\nc") == 2                     # literal escapes


def test_multiparagraph_newline_count_repaired_end_to_end():
    """End-to-end: a single call that drifts the newline count (here the model invents
    an extra paragraph break — restores only ADD missing newlines, never remove extras)
    triggers the per-paragraph re-fill, and the final result's count matches the
    source."""
    w = _mamaylm_worker()

    def _fake_stream(payload, *_):
        if "STRUCT_BREAK_DBL_N" in payload["prompt"]:
            # Full multi-paragraph call: model mangles structure (4 → 6 newlines).
            return "Альфа текст.\n\nБета текст.\n\nГама текст.\n\nЗайвий рядок."
        # Per-paragraph call: a clean single-paragraph translation, no breaks.
        return "Переклад абзацу"

    w._stream_ollama = _fake_stream  # type: ignore[method-assign]
    src = "Alpha one.\r\n\r\nBeta two.\r\n\r\nGamma three."
    out = w._translate_single(_req(src))
    assert out is not None
    assert w._nl_count(out) == w._nl_count(src) == 4
    assert out.count("\n\n") == 2  # app-controlled separators, not the model's


def test_clean_multiparagraph_translation_skips_refill():
    """When the single call already preserves the paragraph structure, the safety net
    must NOT fire its extra per-paragraph calls (no wasted work on the common case)."""
    w = _mamaylm_worker()
    calls = {"n": 0}

    def _fake_stream(payload, *_):
        calls["n"] += 1
        if "STRUCT_BREAK_DBL_N" in payload["prompt"]:
            # Faithful structure: same two breaks as the source (the tokens restore to
            # \n\n), so the count already matches and no re-fill is needed.
            return "Альфа.[[STRUCT_BREAK_DBL_N]]Бета.[[STRUCT_BREAK_DBL_N]]Гама."
        raise AssertionError("clean structure must not trigger per-paragraph re-fill")

    w._stream_ollama = _fake_stream  # type: ignore[method-assign]
    src = "Alpha one.\r\n\r\nBeta two.\r\n\r\nGamma three."
    out = w._translate_single(_req(src))
    assert out is not None
    assert w._nl_count(out) == w._nl_count(src) == 4
    assert calls["n"] == 1  # exactly one model call — no per-paragraph fan-out


def test_restore_line_structure_snaps_to_sentence_boundary():
    """When the model flattens a multi-paragraph string, the proportional re-fill must
    put the paragraph break *between sentences*, not mid-phrase.  Here the proportional
    target lands inside 'delta', so a plain nearest-space snap would cut after 'delta'
    (off=1); sentence-snap instead reaches back to the real boundary after 'zeta.'
    (off=5).  This is pure placement — the newline count is unchanged either way."""
    original = "alpha beta gamma.\n\ndelta."
    flat = "alpha beta gamma epsilon zeta. delta extra here."
    out = _restore_line_structure(flat, original)
    assert out == "alpha beta gamma epsilon zeta.\n\ndelta extra here."
    head, _, _ = out.partition("\n\n")
    assert head.rstrip()[-1] in ".!?…"  # break landed at a sentence end
    assert out.count("\n") == original.count("\n")  # count preserved
