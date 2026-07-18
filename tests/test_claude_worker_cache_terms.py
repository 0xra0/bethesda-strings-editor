"""
Regression tests for ClaudeTranslationWorker cache / term-protection wiring.

These lock in four bugs found by auditing the KR fork against this repo — all in
the Claude backend (the Ollama path was already correct):

1. Term restore used a non-existent method name (``restore`` → ``restore_text``),
   so protected placeholders leaked into the output.
2. ``if self.translation_cache:`` — TranslationCache defines ``__len__`` with no
   ``__bool__``, so an *empty* cache was falsy and disabled read+write forever.
3. Cache/TM were read with no ``is_retry`` guard, so a QC retranslation got the
   same stale string back, defeating the retry.
4. The cache write used a non-existent method (``put`` → ``set``).

The worker runs its ThreadPoolExecutor and emits signals synchronously on the
calling thread (direct connection), so a plain ``translate_batch`` call is enough
to observe results — no event loop needed. A fake Claude client and fake term
protector keep this hermetic (no network, no real ``claude`` binary).
"""

import pytest

from PySide6.QtWidgets import QApplication

from gui.claude_translation_worker import ClaudeTranslationWorker
from gui.ollama_worker import TranslationRequest
from gui.translation_cache import TranslationCache


@pytest.fixture(scope="module")
def _app():
    app = QApplication.instance() or QApplication([])
    yield app


class FakeClient:
    """Records how many times translate() was called; echoes its input."""

    def __init__(self):
        self.calls = 0

    def translate(self, text, **kwargs):
        self.calls += 1
        return f"TR:{text}"


class FakeProtector:
    """protect_text/restore_text only — deliberately has NO restore() method.

    If the worker regressed to calling ``.restore()`` the AttributeError would be
    swallowed and ``restore_text_called`` would stay False with the placeholder
    left in the output.
    """

    def __init__(self):
        self.restore_text_called = False

    def protect_text(self, text, exclude_categories=None):
        if "Sarah" in text:
            return text.replace("Sarah", "__T0__"), {"__T0__": "Sarah"}
        return text, {}

    def restore_text(self, text, token_map, protected_text=""):
        self.restore_text_called = True
        for tok, orig in token_map.items():
            text = text.replace(tok, orig)
        return text


def _make_worker(_app, client, cache=None, protector=None, model="claude-code:sonnet"):
    worker = ClaudeTranslationWorker(
        api_key="",
        model=model,
        source_lang="en",
        target_lang="uk",
        term_protector=protector,
        translation_cache=cache,
    )
    worker._claude = client  # swap the real CLI/API client for the fake
    return worker


def _run(worker, reqs):
    """Run one batch synchronously and collect (results_by_index, errors)."""
    results: dict = {}
    errors: list = []
    worker.translation_ready.connect(lambda i, t, sid: results.__setitem__(i, t))
    worker.error.connect(errors.append)
    worker.translate_batch(list(reqs))
    return results, errors


def _req(index=0, text="Hello", string_id=1, **kw):
    return TranslationRequest(
        index=index, original_text=text, string_id=string_id,
        source_lang="en", target_lang="uk", **kw,
    )


# ── Bug 1: term restore uses restore_text, placeholders don't leak ───────────
def test_protected_terms_are_restored(_app):
    client = FakeClient()
    prot = FakeProtector()
    worker = _make_worker(_app, client, protector=prot)

    results, errors = _run(worker, [_req(text="Hello Sarah")])

    assert not errors
    assert prot.restore_text_called is True
    # Placeholder must be gone, original term back in place.
    assert results[0] == "TR:Hello __T0__".replace("__T0__", "Sarah")
    assert "__T0__" not in results[0]


# ── Bug 2 + 4: an empty cache still populates and serves later hits ───────────
def test_empty_cache_populates_and_is_reused(_app):
    cache = TranslationCache()  # empty → falsy under the old `if cache:` check
    assert len(cache) == 0

    client = FakeClient()
    worker = _make_worker(_app, client, cache=cache)
    results, errors = _run(worker, [_req(text="Reactor online")])
    assert not errors
    assert results[0] == "TR:Reactor online"
    # Bug 2/4: the write must have happened despite starting from empty.
    assert len(cache) == 1

    # Second identical request is served from cache — no new client call.
    client2 = FakeClient()
    worker2 = _make_worker(_app, client2, cache=cache)
    results2, _ = _run(worker2, [_req(text="Reactor online")])
    assert results2[0] == "TR:Reactor online"
    assert client2.calls == 0


# ── Bug 3: a retry bypasses the cache ────────────────────────────────────────
def test_retry_hint_bypasses_cache(_app):
    cache = TranslationCache()
    client = FakeClient()

    # Prime the cache with a normal translation.
    w1 = _make_worker(_app, client, cache=cache)
    _run(w1, [_req(text="Docking clamps")])
    assert client.calls == 1
    assert len(cache) == 1

    # Same text, but this is a QC retranslation → must hit the model again,
    # not return the cached (flawed) string.
    w2 = _make_worker(_app, client, cache=cache)
    results, errors = _run(w2, [_req(text="Docking clamps", retry_hint="too literal")])
    assert not errors
    assert client.calls == 2
    assert results[0] == "TR:Docking clamps"


# ── Bug 3: a retry bypasses the translation memory ───────────────────────────
def _tm_with(id_entries=(), src_entries=()):
    """A real TranslationMemory — never a dict stand-in.

    Standing this in as ``{7: "..."}`` is what hid the fact that the worker
    called ``TranslationMemory.get()``, which does not exist: a dict has that
    method, the real class has ``get_by_id``.  Every string in a batch failed
    with AttributeError as soon as a TM was loaded, and the test passed.
    """
    from gui.translation_memory import TranslationMemory
    tm = TranslationMemory()
    for sid, text in id_entries:
        tm._by_id[sid] = text
    if src_entries:
        tm.add_pairs(src_entries)
    return tm


def test_retry_hint_bypasses_translation_memory(_app):
    client = FakeClient()
    worker = _make_worker(_app, client)
    worker.translation_memory = _tm_with(id_entries=[(7, "STALE TM VALUE")])

    # Non-retry: the TM hit short-circuits the model.
    results, _ = _run(worker, [_req(text="Grav drive", string_id=7)])
    assert results[0] == "STALE TM VALUE"
    assert client.calls == 0

    # Retry: the TM is skipped and the model is called.
    worker2 = _make_worker(_app, client)
    worker2.translation_memory = _tm_with(id_entries=[(7, "STALE TM VALUE")])
    results2, _ = _run(worker2, [_req(text="Grav drive", string_id=7, retry_hint="fix")])
    assert results2[0] == "TR:Grav drive"
    assert client.calls == 1


def test_source_keyed_tm_is_used_by_the_claude_backend(_app):
    """A memory with no IDs at all still resolves — by source text.

    The Official-TM miner and TMX import both produce source-keyed memories.
    Looking up by ID alone left them permanently unused on this backend.
    """
    client = FakeClient()
    worker = _make_worker(_app, client)
    worker.translation_memory = _tm_with(src_entries=[("Grav drive", "Грав-двигун")])

    results, errors = _run(worker, [_req(text="Grav drive", string_id=7)])
    assert not errors
    assert results[0] == "Грав-двигун"
    assert client.calls == 0
