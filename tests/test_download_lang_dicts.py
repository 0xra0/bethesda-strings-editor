"""
Tests that scripts/download_lang_dicts.py can regenerate every list it should.

Korean was the gap: ``data/korean_words.txt`` is loaded like any other
frequency list, but it had no entry in ``LANG_CONFIGS``, so the one documented
way to refresh the word lists silently skipped it and the file could only be
replaced by hand.  The coverage test below is derived from the checkers
themselves rather than from a hand-kept list, so a new frequency-list language
added without a downloader entry fails here instead of going unnoticed.

The corpus year is pinned per language and asserted, because it decides *which*
file a refresh writes: the six European lists are byte-identical to the 2018
corpus, while the shipped Korean list is the 2016 one — the list
``ko_particle_checker``'s "stem is a real word" guard was measured against.
Silently upgrading it would rewrite shipped data as a side effect of a refresh.

The rest covers ``download()`` publishing through a temporary file, which took
away two properties a direct ``open(out_path, "wb")`` had for free — a byte
count comparable to ``Content-Length`` and the file mode ``open`` would have
used.  Both are asserted against a fake response; nothing here touches the
network or ``data/``.
"""

import importlib
import importlib.util
import os
import stat
from pathlib import Path

import pytest

from gui._word_checker_base import WordChecker

_ROOT = Path(__file__).resolve().parent.parent
_DATA = _ROOT / "data"


def _load_script():
    """Import scripts/download_lang_dicts.py, which is not part of a package."""
    path = _ROOT / "scripts" / "download_lang_dicts.py"
    spec = importlib.util.spec_from_file_location("download_lang_dicts", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script():
    """A freshly imported copy, so monkeypatching ``requests`` stays isolated."""
    return _load_script()


@pytest.fixture(scope="module")
def configs():
    return _load_script().LANG_CONFIGS


def _frequency_list_filenames():
    """Word lists owned by a `_word_checker_base.WordChecker` instance.

    Those are exactly the "word count" frequency lists.  English, Russian and
    Ukrainian keep bespoke loaders and their own sources, so they have no
    `_checker` attribute and are excluded here — as they should be, since this
    script is not where they come from.
    """
    found = {}
    for module_path in sorted((_ROOT / "gui").glob("*_word_checker.py")):
        module = importlib.import_module(f"gui.{module_path.stem}")
        checker = getattr(module, "_checker", None)
        if isinstance(checker, WordChecker):
            found[module_path.stem] = checker._filename
    return found


def test_every_frequency_list_can_be_downloaded(configs):
    outputs = {cfg["out"] for cfg in configs.values()}
    missing = {
        module: filename
        for module, filename in _frequency_list_filenames().items()
        if filename not in outputs
    }
    assert missing == {}, f"word lists with no downloader entry: {missing}"


def test_korean_is_covered(configs):
    """The specific gap this file exists for."""
    assert "ko" in configs
    assert configs["ko"]["out"] == "korean_words.txt"


def test_every_configured_output_is_a_list_we_actually_ship(configs):
    for code, cfg in configs.items():
        assert (_DATA / cfg["out"]).is_file(), f"{code} writes an unknown file"


def test_output_filenames_are_unique(configs):
    outputs = [cfg["out"] for cfg in configs.values()]
    assert len(outputs) == len(set(outputs))


def test_korean_stays_pinned_to_the_2016_corpus(configs):
    assert "/content/2016/" in configs["ko"]["url"]


def test_every_other_language_uses_the_2018_corpus(configs):
    for code, cfg in configs.items():
        if code == "ko":
            continue
        assert "/content/2018/" in cfg["url"], f"{code} is not on the 2018 corpus"


def test_entries_are_complete(configs):
    for code, cfg in configs.items():
        assert cfg.keys() >= {"url", "out", "display"}, code
        assert cfg["url"].startswith("https://"), code
        assert cfg["display"], code


# ── download(): publishing through a temporary file ───────────────────────────

_BODY = "\n".join(f"wort{n} {1000 - n}" for n in range(200)).encode("utf-8")

_CFG = {"url": "https://example.invalid/de_50k.txt", "out": "german_words.txt",
        "display": "German"}


class _FakeRaw:
    """Stands in for urllib3's response: knows the *encoded* byte count."""

    def __init__(self, on_wire: int) -> None:
        self._on_wire = on_wire

    def tell(self) -> int:
        return self._on_wire


class _FakeResponse:
    def __init__(self, body: bytes, *, declared: int, encoding: str | None = None,
                 on_wire: int | None = None) -> None:
        self._body = body
        self.headers = {"Content-Length": str(declared)}
        if encoding:
            self.headers["Content-Encoding"] = encoding
        self.raw = _FakeRaw(declared if on_wire is None else on_wire)

    def raise_for_status(self) -> None:
        pass

    def iter_content(self, chunk_size: int = 1):
        # Always the *decoded* bytes — that is what requests hands back.
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]


@pytest.fixture
def fake_get(script, monkeypatch):
    """Install a canned response and capture the request keyword arguments."""
    seen: dict = {}

    def install(response):
        def _get(url, **kwargs):
            seen["url"] = url
            seen.update(kwargs)
            return response
        monkeypatch.setattr(script.requests, "get", _get)
        return seen

    return install


def test_a_compressed_body_is_not_mistaken_for_a_truncated_one(script, fake_get, tmp_path):
    """The regression this pair of fixes exists for.

    Every one of these files is served gzipped, so the bytes iter_content yields
    (decoded) outnumber the bytes Content-Length declares (encoded) — for Polish,
    676,499 against 282,742.  Comparing those two rejected every download, and a
    refresh that always fails is a refresh that never happens.
    """
    fake_get(_FakeResponse(_BODY, declared=len(_BODY) // 3, encoding="gzip",
                           on_wire=len(_BODY) // 3))

    assert script.download("de", _CFG, tmp_path) is True
    assert (tmp_path / "german_words.txt").read_bytes() == _BODY


def test_the_body_is_requested_uncompressed(script, fake_get, tmp_path):
    """What makes the plain byte comparison valid in the first place."""
    seen = fake_get(_FakeResponse(_BODY, declared=len(_BODY)))

    assert script.download("de", _CFG, tmp_path) is True
    assert seen["headers"]["Accept-Encoding"] == "identity"


def test_a_short_body_is_rejected_and_the_good_file_survives(script, fake_get, tmp_path):
    """Truncation must still be caught — that is what the temp file is for."""
    target = tmp_path / "german_words.txt"
    target.write_bytes(b"the list that is already correct\n")
    fake_get(_FakeResponse(_BODY[:100], declared=len(_BODY)))

    assert script.download("de", _CFG, tmp_path) is False
    assert target.read_bytes() == b"the list that is already correct\n"
    assert list(tmp_path.glob("*.part")) == []


def test_the_published_list_is_readable_by_everyone_not_just_the_downloader(
    script, fake_get, tmp_path
):
    """mkstemp creates 0600 and os.replace carries the mode across.

    The shipped lists are 0644, which is also what the previous direct write
    produced.  A 0600 word list is a quiet failure — the checker warns once and
    then answers "not a word" to everything it is asked.
    """
    fake_get(_FakeResponse(_BODY, declared=len(_BODY)))
    assert script.download("de", _CFG, tmp_path) is True

    mode = stat.S_IMODE((tmp_path / "german_words.txt").stat().st_mode)
    umask = os.umask(0)
    os.umask(umask)
    assert mode == 0o666 & ~umask
    assert mode & 0o044, "published word list is not readable by group/other"


def test_an_empty_body_never_reaches_the_target(script, fake_get, tmp_path):
    target = tmp_path / "german_words.txt"
    target.write_bytes(b"real words\n")
    fake_get(_FakeResponse(b"", declared=0))

    assert script.download("de", _CFG, tmp_path) is False
    assert target.read_bytes() == b"real words\n"
    assert list(tmp_path.glob("*.part")) == []
