"""Tests for the Official-TM miner (align base-game localizations → TM + glossary).

Pure functions only — synthetic in-memory text indexes, no game files, no Qt.
A text index is ``{(base, ext): {id: text}}``, exactly what ``scan_language``
returns; every test here feeds one straight into ``align_indexes`` / the miners.
"""

from bethesda_strings.official_tm_miner import (
    AlignedPair,
    GlossaryCandidate,
    MineResult,
    _is_glossary_source,
    _lang_suffix,
    _looks_like_identifier,
    _split_key,
    align_indexes,
    build_tm_pairs,
    mine_glossary,
)

K = ("starfield", "strings")


# ── filename parsing ─────────────────────────────────────────────────────────

def test_split_key_basic_and_case_insensitive():
    assert _split_key("starfield_de.STRINGS", "de") == ("starfield", "strings")
    assert _split_key("Blueprints_en.DLSTRINGS", "EN") == ("blueprints", "dlstrings")
    # Backslash paths inside a BA2 name list.
    assert _split_key("strings\\starfield_pl.ilstrings", "pl") == ("starfield", "ilstrings")


def test_split_key_rejects_wrong_lang_or_ext():
    assert _split_key("starfield_de.strings", "fr") is None       # wrong language
    assert _split_key("starfield_de.txt", "de") is None           # not a strings ext
    assert _split_key("starfield.strings", "de") is None          # no lang suffix
    # A plugin whose own name ends in the lang letters must not false-match.
    assert _split_key("mymodde.strings", "de") is None


def test_lang_suffix_only_known_codes():
    assert _lang_suffix("starfield_pl.ilstrings") == "pl"
    assert _lang_suffix("foo_zhhans.strings") == "zhhans"
    assert _lang_suffix("foo_xx.strings") is None                 # unknown code
    assert _lang_suffix("plainfile.strings") is None              # no underscore
    assert _lang_suffix("foo_en.txt") is None                     # not a strings ext


# ── alignment ────────────────────────────────────────────────────────────────

def test_align_matches_only_shared_nonempty_ids():
    src = {K: {1: "Search", 2: "Open", 3: "OnlyEnglish"}}
    tgt = {K: {1: "Durchsuchen", 2: "", 3: "", 9: "Fremd"}}
    aligned = align_indexes(src, tgt)
    # id 2 (empty target), id 3 (empty target), id 9 (no source) all drop out.
    assert aligned == [AlignedPair("starfield", "strings", 1, "Search", "Durchsuchen")]


def test_align_respects_independent_ext_id_spaces():
    # Same numeric id in two extensions is a DIFFERENT string — never cross-matched.
    src = {
        ("sf", "strings"):   {1: "Search"},
        ("sf", "dlstrings"): {1: "Hello there."},
    }
    tgt = {
        ("sf", "strings"):   {1: "Durchsuchen"},
        ("sf", "dlstrings"): {1: "Hallo."},
    }
    aligned = align_indexes(src, tgt)
    pairs = {(ap.ext, ap.source, ap.target) for ap in aligned}
    assert pairs == {
        ("strings", "Search", "Durchsuchen"),
        ("dlstrings", "Hello there.", "Hallo."),
    }


def test_align_skips_plugins_missing_the_target_language():
    src = {("modA", "strings"): {1: "Fire"}, ("modB", "strings"): {1: "Ice"}}
    tgt = {("modA", "strings"): {1: "Feuer"}}          # modB not localized
    aligned = align_indexes(src, tgt)
    assert [ap.base for ap in aligned] == ["modA"]


# ── TM building ──────────────────────────────────────────────────────────────

def test_build_tm_deduplicates_and_picks_majority_target():
    aligned = [
        AlignedPair("sf", "strings", 1, "Power", "Kraft"),
        AlignedPair("sf", "strings", 2, "Power", "Kraft"),
        AlignedPair("sf", "strings", 3, "Power", "Energie"),   # minority
        AlignedPair("sf", "strings", 4, "Open", "Öffnen"),
    ]
    tm = dict(build_tm_pairs(aligned))
    assert tm["Power"] == "Kraft"        # 2 vs 1 → majority wins
    assert tm["Open"] == "Öffnen"
    assert len(tm) == 2


def test_build_tm_drops_identity_by_default():
    aligned = [
        AlignedPair("sf", "strings", 1, "Search", "Durchsuchen"),
        AlignedPair("sf", "strings", 2, "Constellation", "Constellation"),  # left in English
    ]
    assert dict(build_tm_pairs(aligned)) == {"Search": "Durchsuchen"}       # identity dropped
    keep = dict(build_tm_pairs(aligned, include_identity=True))
    assert keep == {"Search": "Durchsuchen", "Constellation": "Constellation"}


# ── glossary source classification ───────────────────────────────────────────

def test_looks_like_identifier():
    assert _looks_like_identifier("Human_Male_Hair_Cropped_Bang")   # snake_case id
    assert _looks_like_identifier("meshes\\armor\\a.nif")            # path
    assert _looks_like_identifier("textures/x.dds")
    assert _looks_like_identifier("script.pex")                     # asset ext
    assert not _looks_like_identifier("Med Pack")                    # real term
    assert not _looks_like_identifier("Constellation")


def test_is_glossary_source_filters():
    ok = dict(max_words=4, max_len=42)
    assert _is_glossary_source("Med Pack", **ok)
    assert _is_glossary_source("Search", **ok)
    assert not _is_glossary_source("", **ok)                             # empty
    assert not _is_glossary_source("This is a full descriptive sentence.", **ok)  # too long
    assert not _is_glossary_source("Open the door quickly now please", **ok)      # too many words
    assert not _is_glossary_source("Press [E] to continue:", **ok)       # ends with ':'
    assert not _is_glossary_source("Human_Male_Hair", **ok)              # identifier
    assert _is_glossary_source("12.7mm", **ok)                           # caliber term kept
    assert not _is_glossary_source("...", **ok)                          # no letters
    assert not _is_glossary_source("Line one\nLine two", **ok)           # multi-line


# ── glossary mining ──────────────────────────────────────────────────────────

def test_mine_glossary_drops_identifiers_sentences_and_identity():
    aligned = [
        AlignedPair("sf", "strings", 1, "Search", "Durchsuchen"),
        AlignedPair("sf", "strings", 2, "Med Pack", "Sanitätspaket"),
        AlignedPair("sf", "strings", 3, "Human_Male_Hair", "Human_Male_Hair"),  # id + identity
        AlignedPair("sf", "strings", 4, "Constellation", "Constellation"),       # identity
        AlignedPair("sf", "strings", 5, "Long descriptive line here.", "…"),     # sentence
    ]
    gloss = mine_glossary(aligned)
    got = {c.source: c.target for c in gloss}
    assert got == {"Search": "Durchsuchen", "Med Pack": "Sanitätspaket"}


def test_mine_glossary_include_identity():
    aligned = [AlignedPair("sf", "strings", 1, "Constellation", "Constellation")]
    assert mine_glossary(aligned) == []                       # dropped by default
    gloss = mine_glossary(aligned, include_identity=True)
    assert [(c.source, c.target) for c in gloss] == [("Constellation", "Constellation")]


def test_mine_glossary_consistency_threshold():
    # 'Fire' → 'Feuer' x1, 'Schießen' x1 → 50% share; default min_consistency 0.5 keeps it,
    # but a stricter threshold drops the ambiguous term.
    aligned = [
        AlignedPair("sf", "strings", 1, "Fire", "Feuer"),
        AlignedPair("sf", "strings", 2, "Fire", "Schießen"),
    ]
    assert [c.source for c in mine_glossary(aligned, min_consistency=0.5)] == ["Fire"]
    assert mine_glossary(aligned, min_consistency=0.75) == []


def test_mine_glossary_sorted_by_count_desc():
    aligned = [
        AlignedPair("sf", "strings", 1, "Rare", "Selten"),
        AlignedPair("sf", "strings", 2, "Common", "Häufig"),
        AlignedPair("sf", "strings", 3, "Common", "Häufig"),
    ]
    gloss = mine_glossary(aligned)
    assert [c.source for c in gloss] == ["Common", "Rare"]     # count 2 before count 1
    assert gloss[0].count == 2 and gloss[0].consistency == 1.0


def test_mine_glossary_reference_annotation():
    aligned = [AlignedPair("sf", "strings", 7, "Search", "Durchsuchen")]
    refs = {"pl": {("sf", "strings"): {7: "Szukaj"}},
            "ru": {("sf", "strings"): {7: "Поиск"}}}
    gloss = mine_glossary(aligned, reference_indexes=refs)
    assert gloss[0].ref == {"pl": "Szukaj", "ru": "Поиск"}


def test_mine_glossary_reference_missing_id_is_omitted():
    aligned = [AlignedPair("sf", "strings", 7, "Search", "Durchsuchen")]
    refs = {"pl": {("sf", "strings"): {99: "Nieznane"}}}       # id 7 not present
    gloss = mine_glossary(aligned, reference_indexes=refs)
    assert gloss[0].ref == {}


# ── MineResult ───────────────────────────────────────────────────────────────

def test_mine_result_truthiness():
    assert not MineResult("en", "de", [], [])
    assert MineResult("en", "de", [("a", "b")], [])
    assert MineResult("en", "de", [], [GlossaryCandidate("a", "b", 1, 1.0)])
