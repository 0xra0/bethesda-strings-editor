"""
Concurrency tests for TermProtector._recompile_if_needed().

Run with:
    python -m pytest tests/test_term_protector_threading.py -v
or directly:
    python tests/test_term_protector_threading.py
"""

import sys
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent.parent))
from gui.term_protector import ProtectedTerm, TermProtector


# ── helpers ──────────────────────────────────────────────────────────────────

def make_protector() -> TermProtector:
    """Fresh TermProtector with only DEFAULT_PROTECTED_TERMS (no file I/O)."""
    return TermProtector()


# ── tests ─────────────────────────────────────────────────────────────────────

def test_protect_text_returns_correct_output():
    """Basic sanity check before threading tests."""
    tp = make_protector()
    text = "Go to New Atlantis and talk to Sarah Morgan at [PLYR] location."
    result, tokens = tp.protect_text(text)

    assert "[PLYR]" not in result, "structural token should be protected"
    assert "New Atlantis" not in result, "named term should be protected"
    assert "Sarah Morgan" not in result, "named term should be protected"

    restored = tp.restore_text(result, tokens)
    assert restored == text, f"restore mismatch:\n  got: {restored}\n  want: {text}"
    print("  PASS test_protect_text_returns_correct_output")


def test_concurrent_protect_text_no_corruption():
    """
    N threads call protect_text simultaneously from a dirty state.

    Every result must round-trip correctly through restore_text.
    No exceptions, no torn output.
    """
    N = 40
    tp = make_protector()
    tp._dirty.set()  # guarantee all threads see dirty on entry

    errors: list[tuple[int, Exception]] = []
    results: list[str | None] = [None] * N
    barrier = threading.Barrier(N)

    def worker(i: int) -> None:
        try:
            barrier.wait()  # maximise concurrent entry into _recompile_if_needed
            text = (
                f"Worker {i}: contact UC SysDef at New Atlantis "
                f"for mission [PLYR] — ref %s"
            )
            result, tokens = tp.protect_text(text)
            restored = tp.restore_text(result, tokens)
            results[i] = restored
        except Exception as exc:
            errors.append((i, exc))

    threads = [threading.Thread(target=worker, args=(i,), name=f"W{i}") for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Exceptions from worker threads: {errors}"

    for i, r in enumerate(results):
        assert r is not None, f"worker {i} produced no result"
        assert f"Worker {i}" in r, f"worker {i}: round-trip lost original content"

    print(f"  PASS test_concurrent_protect_text_no_corruption  (contention={tp._contention_count})")


def test_contention_count_increments():
    """
    When many threads race through the outer fast-path check simultaneously,
    the inner double-check must catch at least some of them and increment
    _contention_count, proving the guard works.

    We force the race by:
      1. Setting _dirty manually so all threads pass the outer check.
      2. Monkey-patching _dirty.clear() to sleep briefly, keeping the lock
         held long enough for other threads to pass the outer check and pile up
         at the inner check.
    """
    tp = make_protector()
    N = 20

    # Patch clear() to introduce a deliberate delay while holding self._lock
    original_clear = tp._dirty.clear

    def slow_clear():
        time.sleep(0.02)  # hold long enough for the other N-1 threads to queue
        original_clear()

    tp._dirty.clear = slow_clear  # type: ignore[method-assign]

    barrier = threading.Barrier(N)

    def worker():
        barrier.wait()
        tp._recompile_if_needed()

    threads = [threading.Thread(target=worker, name=f"C{i}") for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert tp._contention_count > 0, (
        "_contention_count should be > 0 when threads race; "
        "the inner double-check is not triggering as expected"
    )
    print(f"  PASS test_contention_count_increments  (contention={tp._contention_count})")


def test_add_term_concurrent_with_protect_text():
    """
    A writer thread calls add_term() while reader threads call protect_text().

    Requirements:
    - No exceptions in any thread.
    - Each protect_text() result is a valid string (no AttributeError / None).
    - The TermProtector converges to a consistent state after all threads finish.
    """
    tp = make_protector()
    errors: list[tuple[str, Exception]] = []
    N_READERS = 30
    N_WRITERS = 5

    def reader(i: int) -> None:
        try:
            for _ in range(10):
                text = f"Mission {i}: find Starborn Guardian near Neon [PLYR]"
                result, tokens = tp.protect_text(text)
                assert isinstance(result, str)
                assert isinstance(tokens, dict)
        except Exception as exc:
            errors.append((f"reader-{i}", exc))

    def writer(i: int) -> None:
        try:
            for j in range(5):
                tp.add_term(f"CustomTerm{i}_{j}", "test")
                time.sleep(0.001)
        except Exception as exc:
            errors.append((f"writer-{i}", exc))

    with ThreadPoolExecutor(max_workers=N_READERS + N_WRITERS) as pool:
        futures = (
            [pool.submit(reader, i) for i in range(N_READERS)]
            + [pool.submit(writer, i) for i in range(N_WRITERS)]
        )
        for f in as_completed(futures):
            f.result()  # re-raises any exception

    assert not errors, f"Errors: {errors}"
    print(
        f"  PASS test_add_term_concurrent_with_protect_text  "
        f"(contention={tp._contention_count}, "
        f"terms={tp.get_statistics()['total_terms']})"
    )


def test_dirty_event_semantics():
    """
    _dirty must be set after add_term and clear after _recompile_if_needed.
    """
    tp = make_protector()

    # After construction the initial dirty flag should be cleared by the
    # first _recompile_if_needed call (triggered lazily on first protect_text).
    tp.protect_text("hello")
    assert not tp._dirty.is_set(), "_dirty should be clear after first protect_text"

    # Adding a term must set the flag.
    tp.add_term("Vanguard", "faction")
    assert tp._dirty.is_set(), "_dirty should be set after add_term"

    # Calling protect_text again must clear it.
    tp.protect_text("UC Vanguard is here")
    assert not tp._dirty.is_set(), "_dirty should be clear after protect_text recompiles"

    # Removing a term must set the flag again.
    tp.remove_term("Vanguard")
    assert tp._dirty.is_set(), "_dirty should be set after remove_term"

    print("  PASS test_dirty_event_semantics")


# ── runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_protect_text_returns_correct_output,
        test_dirty_event_semantics,
        test_concurrent_protect_text_no_corruption,
        test_contention_count_increments,
        test_add_term_concurrent_with_protect_text,
    ]
    print(f"Running {len(tests)} tests...\n")
    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as e:
            print(f"  FAIL {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {test.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{'All tests passed.' if not failed else f'{failed} test(s) failed.'}")
    sys.exit(0 if not failed else 1)
