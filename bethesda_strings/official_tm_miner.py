"""Mine an authoritative Translation Memory + glossary from the base game's own
official localizations.

Bethesda ships every supported language for a plugin **side by side, aligned on
the same string IDs**, inside one localization archive (e.g. Starfield's
``Starfield - Localization.ba2`` holds ``starfield_en.strings`` … ``starfield_de``
… ``starfield_pl`` … all keyed identically per extension).  Aligning the English
source against an official target language by ``(plugin, ext, id)`` therefore
yields Bethesda's *canonical* rendering of every weapon name, faction, UI verb
and quest objective — for free, with zero model calls.

Two products fall out of the alignment:

* **Translation Memory** — the full source→target pair set (deduplicated by
  source, majority target on conflict).  Feeds :class:`TranslationMemory` so a
  mod's ``"Search"`` is answered with the official ``"Durchsuchen"`` before any
  AI call.
* **Glossary** — the short, term-like subset (nouns / UI labels), filtered of
  editor-IDs and full sentences, so prompt injection and QC steer to official
  terminology.

The three string extensions have **independent ID spaces** (see
:mod:`bethesda_strings.triplet`), so alignment is always done *within* one
``(base, ext)`` key — a numeric ID is only ever compared against the same file
type of the same plugin.

Requiring **both** the source and target language to be present for a plugin is
what restricts mining to officially-localized content: third-party mods ship
English (and maybe the author's language) but not the full official language
set, so they simply drop out of the alignment.

Pure Python — no Qt.  The GUI (:mod:`gui.official_tm_dialog`) runs
:func:`mine_official` on a worker thread and feeds the result into the app's
existing :class:`TranslationMemory` / :class:`Glossary`.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

from bethesda_strings.ba2_handler import BA2File
from bethesda_strings.core import BethesdaStringFile

logger = logging.getLogger(__name__)

_STRING_EXTS = ("strings", "dlstrings", "ilstrings")

# Language codes Bethesda / the community use as the ``_<lang>`` filename suffix.
# Used only to tell a real language suffix apart from a plugin-name fragment when
# listing what a Data folder contains.
KNOWN_LANGS = frozenset({
    "en", "de", "es", "esmx", "fr", "it", "ja", "pl", "ptbr",
    "zhhans", "zhhant", "ru", "uk", "cs", "ko", "pt", "tr", "hu",
})

# Extensions that mark a value as an asset/resource path rather than user text.
_ASSET_EXTS = frozenset({
    "nif", "dds", "wav", "xwm", "fuz", "swf", "psc", "pex", "hkx",
    "esp", "esm", "esl", "ba2", "wem", "lip", "tri", "bgsm", "mat",
})

# Defaults tuned against Starfield's base game (see tests / CLAUDE.md).
DEFAULT_MAX_WORDS = 4
DEFAULT_MAX_LEN = 42
DEFAULT_MIN_CONSISTENCY = 0.5

# A text index maps a plugin/file to its decoded strings.
TextIndex = dict  # {(base: str, ext: str): {id: int -> text: str}}


# ── data classes ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AlignedPair:
    """One source→target string aligned within a single ``(base, ext)`` id space."""

    base: str
    ext: str
    id: int
    source: str
    target: str


@dataclass
class GlossaryCandidate:
    """A term-like source→target pair mined for the glossary."""

    source: str
    target: str
    count: int                       # how many aligned strings carried this source
    consistency: float               # dominant-target share (1.0 == unambiguous)
    ref: dict = field(default_factory=dict)   # extra reference langs: {lang: text}


@dataclass
class MineResult:
    source_lang: str
    target_lang: str
    tm_pairs: list          # list[tuple[str, str]] — (source, target)
    glossary: list          # list[GlossaryCandidate]
    plugin_count: int = 0   # plugins (base names) that had both languages
    aligned_pairs: int = 0  # total aligned source/target strings

    def __bool__(self) -> bool:
        return bool(self.tm_pairs or self.glossary)


# ── filename parsing ────────────────────────────────────────────────────────


def _split_key(filename: str, lang: str) -> Optional[tuple]:
    """``'starfield_de.STRINGS', 'de'`` → ``('starfield', 'strings')`` or ``None``."""
    name = Path(filename.replace("\\", "/")).name
    dot = name.rfind(".")
    if dot < 0:
        return None
    ext = name[dot + 1:].lower()
    if ext not in _STRING_EXTS:
        return None
    stem = name[:dot].lower()
    suffix = f"_{lang.lower()}"
    if not stem.endswith(suffix):
        return None
    return (stem[: -len(suffix)], ext)


def _lang_suffix(filename: str) -> Optional[str]:
    """Return the trailing ``_<lang>`` code of a strings filename, if it is a known
    language code, else ``None``."""
    name = Path(filename.replace("\\", "/")).name
    dot = name.rfind(".")
    if dot < 0 or name[dot + 1:].lower() not in _STRING_EXTS:
        return None
    stem = name[:dot]
    us = stem.rfind("_")
    if us < 0:
        return None
    cand = stem[us + 1:].lower()
    return cand if cand in KNOWN_LANGS else None


# ── scanning a game Data folder ─────────────────────────────────────────────


def available_languages(data_dir) -> set:
    """List the language codes present as ``.strings`` files under *data_dir*
    (loose ``Strings/`` files and inside ``.ba2`` archives)."""
    data_dir = Path(data_dir)
    langs: set = set()
    if not data_dir.is_dir():
        return langs

    for sub in (data_dir, data_dir / "Strings", data_dir / "strings"):
        if sub.is_dir():
            for p in sub.iterdir():
                if p.is_file():
                    lc = _lang_suffix(p.name)
                    if lc:
                        langs.add(lc)

    for ba2 in _iter_ba2s(data_dir):
        try:
            ba = BA2File(str(ba2))
        except Exception:  # noqa: BLE001 — skip DX10/unreadable archives
            continue
        try:
            for name in ba.list_strings_files():
                lc = _lang_suffix(name)
                if lc:
                    langs.add(lc)
        finally:
            _safe_close(ba)
    return langs


def _iter_ba2s(data_dir: Path) -> Iterable[Path]:
    seen: set = set()
    for pat in ("*.ba2", "*.BA2"):
        for p in sorted(data_dir.glob(pat)):
            rp = p.resolve()
            if rp not in seen:
                seen.add(rp)
                yield p


def _safe_close(ba: BA2File) -> None:
    try:
        ba.close()
    except Exception:  # noqa: BLE001
        pass


def _decode(raw: bytes, ext: str) -> Optional[dict]:
    try:
        sf = BethesdaStringFile(buffer=raw, file_extension=ext)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Miner: parse failed (%s): %s", ext, exc)
        return None
    return {s.id: s.get_string(sf.encoding) for s in sf.strings}


def scan_language(
    data_dir,
    lang: str,
    *,
    plugins: Optional[set] = None,
    progress: Optional[Callable[[str], None]] = None,
) -> TextIndex:
    """Decode every ``<plugin>_<lang>.<ext>`` string file under *data_dir*.

    Packed archives are read first (that is where the base game's official
    languages live); loose ``Strings/`` files only fill keys an archive did not
    already provide.  Returns ``{(base, ext): {id: text}}``.  When *plugins* is
    given, only those base names (lowercase) are kept.
    """
    data_dir = Path(data_dir)
    index: TextIndex = {}
    if not data_dir.is_dir():
        return index

    def _keep(key) -> bool:
        return plugins is None or key[0] in plugins

    # 1) BA2 archives — official packed content.
    for ba2 in _iter_ba2s(data_dir):
        if progress:
            progress(f"Scanning {ba2.name}")
        try:
            ba = BA2File(str(ba2))
        except Exception:  # noqa: BLE001
            continue
        try:
            for name in ba.list_strings_files():
                key = _split_key(name, lang)
                if key is None or not _keep(key) or key in index:
                    continue
                try:
                    raw = ba.extract(name)
                except Exception:  # noqa: BLE001
                    continue
                decoded = _decode(raw, key[1])
                if decoded:
                    index[key] = decoded
        finally:
            _safe_close(ba)

    # 2) Loose files — fill only missing keys.
    for sub in (data_dir, data_dir / "Strings", data_dir / "strings"):
        if not sub.is_dir():
            continue
        for p in sorted(sub.iterdir()):
            if not p.is_file():
                continue
            key = _split_key(p.name, lang)
            if key is None or not _keep(key) or key in index:
                continue
            try:
                sf = BethesdaStringFile(str(p))
                index[key] = {s.id: s.get_string(sf.encoding) for s in sf.strings}
            except Exception as exc:  # noqa: BLE001
                logger.warning("Miner: cannot parse %s: %s", p.name, exc)
    return index


# ── alignment + mining (pure, index-in → result-out) ────────────────────────


def align_indexes(source_index: Mapping, target_index: Mapping) -> list:
    """Align two language text indexes by ``(base, ext, id)``.

    Only IDs present in **both** languages with non-empty text on both sides are
    returned, so partially-localized plugins contribute only their shared rows.
    """
    aligned: list = []
    for key, src_texts in source_index.items():
        tgt_texts = target_index.get(key)
        if not tgt_texts:
            continue
        base, ext = key
        for sid, src in src_texts.items():
            tgt = tgt_texts.get(sid)
            if src and tgt and src.strip() and tgt.strip():
                aligned.append(AlignedPair(base, ext, sid, src, tgt))
    return aligned


def build_tm_pairs(aligned: Iterable, *, include_identity: bool = False) -> list:
    """Deduplicate aligned pairs into TM ``(source, target)`` entries.

    Source text is matched exactly by the engine, so keys are case-sensitive.
    When one source has several official targets (context differences), the
    majority rendering wins.

    ``include_identity=False`` (the default) drops pairs whose winning target
    equals the source: those are strings the official localization left in
    English, so as a TM lookup they'd "translate" English→English — and, worse,
    pre-filling a table row with unchanged English while marking it *translated*
    hides untranslated content.  Set True to keep them (e.g. proper nouns that
    deliberately stay identical).
    """
    by_source: dict = {}
    for ap in aligned:
        by_source.setdefault(ap.source, Counter())[ap.target] += 1
    pairs: list = []
    for src, targets in by_source.items():
        tgt, _ = targets.most_common(1)[0]
        if not include_identity and tgt == src:
            continue
        pairs.append((src, tgt))
    return pairs


def _looks_like_identifier(s: str) -> bool:
    """True for editor-IDs / resource names that are not user-facing terms."""
    if "\\" in s or "/" in s:
        return True
    if " " not in s and "_" in s:            # Human_Male_Hair_Cropped_Bang
        return True
    if "." in s:
        tail = s.rsplit(".", 1)[-1].lower()
        if tail in _ASSET_EXTS:
            return True
    return False


def _is_glossary_source(
    s: str,
    *,
    max_words: int,
    max_len: int,
) -> bool:
    s = s.strip()
    if not s or "\n" in s:
        return False
    if not any(ch.isalpha() for ch in s):
        return False
    if len(s) > max_len or len(s.split()) > max_words:
        return False
    if s[-1] in ".!?:;":                      # sentence / label → belongs in TM only
        return False
    if _looks_like_identifier(s):
        return False
    return True


def mine_glossary(
    aligned: Iterable,
    *,
    max_words: int = DEFAULT_MAX_WORDS,
    max_len: int = DEFAULT_MAX_LEN,
    min_consistency: float = DEFAULT_MIN_CONSISTENCY,
    include_identity: bool = False,
    reference_indexes: Optional[Mapping] = None,
) -> list:
    """Select the short, term-like subset of *aligned* as glossary candidates.

    Full sentences, editor-IDs and resource paths are dropped; a source with
    several official targets is kept only if its dominant rendering covers at
    least *min_consistency* of occurrences.  ``include_identity=False`` drops
    pairs whose target equals the source (they carry no translation).  When
    *reference_indexes* (``{lang: text_index}``) is given, each candidate is
    annotated with the same string in those languages (handy Slavic/Romance
    cross-references for a non-shipped target such as Ukrainian).
    """
    groups: dict = {}
    for ap in aligned:
        if not _is_glossary_source(ap.source, max_words=max_words, max_len=max_len):
            continue
        tgt = ap.target.strip()
        if not tgt:
            continue
        g = groups.get(ap.source)
        if g is None:
            g = groups[ap.source] = {"targets": Counter(), "ref": (ap.base, ap.ext, ap.id)}
        g["targets"][tgt] += 1

    out: list = []
    for src, g in groups.items():
        counter = g["targets"]
        total = sum(counter.values())
        tgt, cnt = counter.most_common(1)[0]
        share = cnt / total if total else 0.0
        if total >= 2 and share < min_consistency:
            continue
        if not include_identity and tgt == src:
            continue
        refs: dict = {}
        if reference_indexes:
            b, e, i = g["ref"]
            for lang, idx in reference_indexes.items():
                txt = (idx.get((b, e)) or {}).get(i)
                if txt and txt.strip():
                    refs[lang] = txt.strip()
        out.append(GlossaryCandidate(src, tgt, total, round(share, 3), refs))

    out.sort(key=lambda c: (-c.count, c.source.lower()))
    return out


# ── top-level convenience (the GUI worker entry point) ──────────────────────


def mine_official(
    data_dir,
    source_lang: str = "en",
    target_lang: str = "de",
    *,
    reference_langs: Iterable = (),
    plugins: Optional[set] = None,
    build_tm: bool = True,
    build_glossary: bool = True,
    max_words: int = DEFAULT_MAX_WORDS,
    max_len: int = DEFAULT_MAX_LEN,
    min_consistency: float = DEFAULT_MIN_CONSISTENCY,
    include_identity: bool = False,
    progress: Optional[Callable[[str], None]] = None,
) -> MineResult:
    """Scan *data_dir*, align *source_lang*→*target_lang*, and mine TM + glossary."""
    if progress:
        progress(f"Reading {source_lang} strings…")
    src_index = scan_language(data_dir, source_lang, plugins=plugins, progress=progress)
    if progress:
        progress(f"Reading {target_lang} strings…")
    tgt_index = scan_language(data_dir, target_lang, plugins=plugins, progress=progress)

    ref_indexes: dict = {}
    for lang in reference_langs:
        if lang and lang != source_lang and lang != target_lang:
            if progress:
                progress(f"Reading {lang} reference strings…")
            ref_indexes[lang] = scan_language(data_dir, lang, plugins=plugins, progress=progress)

    if progress:
        progress("Aligning by string ID…")
    aligned = align_indexes(src_index, tgt_index)
    plugin_bases = {ap.base for ap in aligned}

    tm_pairs = build_tm_pairs(aligned, include_identity=include_identity) if build_tm else []
    glossary = (
        mine_glossary(
            aligned,
            max_words=max_words,
            max_len=max_len,
            min_consistency=min_consistency,
            include_identity=include_identity,
            reference_indexes=ref_indexes or None,
        )
        if build_glossary
        else []
    )

    return MineResult(
        source_lang=source_lang,
        target_lang=target_lang,
        tm_pairs=tm_pairs,
        glossary=glossary,
        plugin_count=len(plugin_bases),
        aligned_pairs=len(aligned),
    )
