"""Tests for the read-only companion-triplet reference.

The whole point of TripletReference is that .strings / .dlstrings / .ilstrings
have INDEPENDENT ID spaces and must never be merged/deduped into one file.
"""

from bethesda_strings.core import BethesdaStringFile, StringDataObject
from bethesda_strings.triplet import TripletReference, CompanionEntry


def _make_sf(ext: str, entries):
    """Build an in-memory BethesdaStringFile from (id, text) pairs."""
    sf = BethesdaStringFile(file_extension=ext)
    has_prefix = ext in ("dlstrings", "ilstrings")
    for sid, text in entries:
        obj = StringDataObject(
            id=sid, address=0, relative_offset=0, absolute_offset=0,
            null_point=0, length=0, string_array=bytearray(),
            has_length_prefix=has_prefix,
        )
        obj.set_string(text, "utf-8")
        sf.strings.append(obj)
    sf.encoding = "utf-8"
    return sf


def test_independent_id_spaces_are_kept_separate():
    # Same numeric ID, different meaning in each file (the real 0x14FC case).
    strings = _make_sf("strings", [(0x14FC, "Флакон із транквілізатором")])
    ilstrings = _make_sf("ilstrings", [(0x14FC, "А, ми закінчили?")])

    ref = TripletReference()
    ref.add_file("shatteredspace_uk.strings", strings)
    ref.add_file("starfield_uk.ilstrings", ilstrings)

    assert ref.lookup("strings", 0x14FC) == "Флакон із транквілізатором"
    assert ref.lookup("ilstrings", 0x14FC) == "А, ми закінчили?"
    # Never conflated: the .strings value must not leak into the .ilstrings space.
    assert ref.lookup("dlstrings", 0x14FC) is None
    assert len(ref) == 2


def test_add_file_returns_count_and_does_not_mutate_source():
    sf = _make_sf("dlstrings", [(1, "one"), (2, "two"), (3, "three")])
    before = list(sf.strings)  # identity snapshot
    ref = TripletReference()
    added = ref.add_file("mod_uk.dlstrings", sf)
    assert added == 3
    # The reference copies text only; it must not retain/replace the objects.
    assert sf.strings == before
    assert all(a is b for a, b in zip(sf.strings, before))


def test_extension_and_file_counts():
    ref = TripletReference()
    ref.add_file("x_uk.strings", _make_sf("strings", [(1, "a")]))
    ref.add_file("x_uk.dlstrings", _make_sf("dlstrings", [(1, "b")]))
    ref.add_file("x_uk.ilstrings", _make_sf("ilstrings", [(1, "c")]))
    assert ref.extensions() == ["dlstrings", "ilstrings", "strings"]
    assert ref.file_count() == 3
    assert bool(ref) is True


def test_entries_carry_source_and_ext():
    ref = TripletReference()
    ref.add_file("/abs/path/starfield_uk.ilstrings", _make_sf("ilstrings", [(7, "hi")]))
    entries = list(ref.iter_entries())
    assert entries == [CompanionEntry("ilstrings", 7, "hi", "starfield_uk.ilstrings")]


def test_extension_inferred_from_path_when_file_ext_blank():
    sf = _make_sf("ilstrings", [(5, "z")])
    sf.file_extension = ""  # force fallback to path suffix
    ref = TripletReference()
    ref.add_file("plugin_uk.ILSTRINGS", sf)
    assert ref.lookup("ilstrings", 5) == "z"


def test_empty_reference_is_falsey():
    ref = TripletReference()
    assert not ref
    assert len(ref) == 0
    assert ref.lookup("strings", 1) is None
