"""
Tests for gui/glossary.py — GlossaryEntry, Glossary, GlossaryManager.

No Qt dependency: runs headlessly without a display.

Run with:
    python tests/test_glossary.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui.glossary import Glossary, GlossaryEntry, GlossaryManager


# ── helpers ────────────────────────────────────────────────────────────────────


def _make_entry(**kwargs) -> GlossaryEntry:
    defaults = dict(
        source_term="Companion",
        target_term="Компаньйон",
        category="Characters",
        definition="A non-player companion",
        examples=["Meet your companion."],
        notes="",
    )
    defaults.update(kwargs)
    return GlossaryEntry(**defaults)  # type: ignore[arg-type]


# ── GlossaryEntry ──────────────────────────────────────────────────────────────


def test_entry_auto_id():
    e = GlossaryEntry(source_term="AI", target_term="ШІ")
    assert e.id, "id should be auto-generated"
    assert len(e.id) == 36  # UUID4 format
    print("  PASS test_entry_auto_id")


def test_entry_defaults():
    e = GlossaryEntry(source_term="Foo", target_term="Бар")
    assert e.category == ""
    assert e.definition == ""
    assert e.examples == []
    assert e.notes == ""
    print("  PASS test_entry_defaults")


# ── Glossary CRUD ──────────────────────────────────────────────────────────────


def test_add_and_retrieve():
    g = Glossary()
    e = _make_entry()
    g.add_entry(e)
    assert len(g) == 1
    assert g.get_entry(e.id) is e
    print("  PASS test_add_and_retrieve")


def test_update_entry():
    g = Glossary()
    e = _make_entry()
    g.add_entry(e)
    e.target_term = "НОВИЙ"
    g.update_entry(e)
    retrieved = g.get_entry(e.id)
    assert retrieved is not None
    assert retrieved.target_term == "НОВИЙ"
    print("  PASS test_update_entry")


def test_remove_entry():
    g = Glossary()
    e = _make_entry()
    g.add_entry(e)
    g.remove_entry(e.id)
    assert len(g) == 0
    assert g.get_entry(e.id) is None
    print("  PASS test_remove_entry")


def test_remove_entries_bulk():
    g = Glossary()
    entries = [_make_entry(source_term=f"Term{i}") for i in range(5)]
    for e in entries:
        g.add_entry(e)
    ids = [entries[0].id, entries[2].id, entries[4].id]
    g.remove_entries(ids)
    assert len(g) == 2
    print("  PASS test_remove_entries_bulk")


def test_clear():
    g = Glossary()
    for i in range(3):
        g.add_entry(_make_entry(source_term=f"T{i}"))
    g.clear()
    assert len(g) == 0
    print("  PASS test_clear")


# ── Glossary search ────────────────────────────────────────────────────────────


def test_search_by_source():
    g = Glossary()
    # Use explicit, distinct definitions to avoid accidental substring matches
    g.add_entry(_make_entry(source_term="Companion", target_term="Компаньйон", definition=""))
    g.add_entry(_make_entry(source_term="Settlement", target_term="Поселення", definition="A place"))
    results = g.search("comp")
    assert len(results) == 1, f"Expected 1 result, got {len(results)}: {[e.source_term for e in results]}"
    assert results[0].source_term == "Companion"
    print("  PASS test_search_by_source")


def test_search_by_target():
    g = Glossary()
    g.add_entry(_make_entry(source_term="AI", target_term="штучний інтелект"))
    results = g.search("штучний")
    assert len(results) == 1
    print("  PASS test_search_by_target")


def test_search_empty_returns_all():
    g = Glossary()
    for i in range(3):
        g.add_entry(_make_entry(source_term=f"T{i}"))
    assert len(g.search("")) == 3
    print("  PASS test_search_empty_returns_all")


def test_filter_by_category():
    g = Glossary()
    g.add_entry(_make_entry(source_term="Companion", category="Characters"))
    g.add_entry(_make_entry(source_term="Settlement", category="Locations"))
    g.add_entry(_make_entry(source_term="Ship", category="Vehicles"))
    results = g.filter_by_category("Characters")
    assert len(results) == 1
    assert results[0].source_term == "Companion"
    print("  PASS test_filter_by_category")


def test_categories():
    g = Glossary()
    g.add_entry(_make_entry(source_term="A", category="Alpha"))
    g.add_entry(_make_entry(source_term="B", category="Beta"))
    g.add_entry(_make_entry(source_term="C", category="Alpha"))
    cats = g.categories()
    assert cats == ["Alpha", "Beta"]
    print("  PASS test_categories")


# ── Glossary.find_terms_in_text ────────────────────────────────────────────────


def test_find_terms_basic():
    g = Glossary()
    g.add_entry(_make_entry(source_term="Companion", target_term="Компаньйон"))
    hits = g.find_terms_in_text("Meet the Companion at the base.")
    assert len(hits) == 1
    assert hits[0][2].source_term == "Companion"
    print("  PASS test_find_terms_basic")


def test_find_terms_case_insensitive():
    g = Glossary()
    g.add_entry(_make_entry(source_term="companion"))
    hits = g.find_terms_in_text("A COMPANION walked in.")
    assert len(hits) == 1
    print("  PASS test_find_terms_case_insensitive")


def test_find_terms_word_boundary():
    g = Glossary()
    g.add_entry(_make_entry(source_term="AI"))
    # "AI" should not match "MAILING"
    hits = g.find_terms_in_text("AI powers MAILING systems.")
    assert len(hits) == 1
    assert hits[0][2].source_term == "AI"
    print("  PASS test_find_terms_word_boundary")


def test_find_terms_multiple():
    g = Glossary()
    g.add_entry(_make_entry(source_term="Companion", target_term="Компаньйон"))
    g.add_entry(_make_entry(source_term="Settlement", target_term="Поселення"))
    hits = g.find_terms_in_text("The Companion built a Settlement.")
    assert len(hits) == 2
    print(f"  PASS test_find_terms_multiple  ({len(hits)} hits)")


def test_find_terms_sorted_by_position():
    g = Glossary()
    g.add_entry(_make_entry(source_term="Settlement", target_term="Поселення"))
    g.add_entry(_make_entry(source_term="Companion", target_term="Компаньйон"))
    hits = g.find_terms_in_text("A Companion visited the Settlement.")
    assert hits[0][2].source_term == "Companion"
    assert hits[1][2].source_term == "Settlement"
    print("  PASS test_find_terms_sorted_by_position")


def test_find_terms_empty_text():
    g = Glossary()
    g.add_entry(_make_entry())
    assert g.find_terms_in_text("") == []
    assert g.find_terms_in_text("No match here.") == []
    print("  PASS test_find_terms_empty_text")


# ── JSON persistence ───────────────────────────────────────────────────────────


def test_save_and_load_json():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "glossary.json"
        g1 = Glossary(path)
        e = _make_entry()
        g1.add_entry(e)
        g1.save_json()

        g2 = Glossary(path)
        assert len(g2) == 1
        loaded = g2.get_entry(e.id)
        assert loaded is not None
        assert loaded.source_term == "Companion"
        assert loaded.target_term == "Компаньйон"
        assert loaded.category == "Characters"
        assert loaded.examples == ["Meet your companion."]
    print("  PASS test_save_and_load_json")


def test_load_json_skips_empty_source():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "g.json"
        data = {
            "entries": [
                {"source_term": "", "target_term": "Foo", "id": "abc"},
                {"source_term": "Real", "target_term": "Bar", "id": "def"},
            ]
        }
        path.write_text(json.dumps(data))
        g = Glossary(path)
        assert len(g) == 1
        assert g.entries[0].source_term == "Real"
    print("  PASS test_load_json_skips_empty_source")


# ── CSV persistence ────────────────────────────────────────────────────────────


def test_export_and_import_csv():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "glossary.csv"
        g1 = Glossary()
        g1.add_entry(_make_entry(source_term="Ship", target_term="Корабель"))
        g1.add_entry(
            _make_entry(
                source_term="Station",
                target_term="Станція",
                examples=["At the Station.", "Dock at Station."],
            )
        )
        g1.export_csv(path)

        g2 = Glossary()
        count = g2.import_csv(path)
        assert count == 2
        sources = {e.source_term for e in g2.entries}
        assert "Ship" in sources
        assert "Station" in sources

        station = next(e for e in g2.entries if e.source_term == "Station")
        assert len(station.examples) == 2
    print("  PASS test_export_and_import_csv")


def test_import_csv_alternate_headers():
    """Accept 'Source'/'Target' capitalized headers from xTranslator exports."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "alt.csv"
        path.write_text("Source,Target,Category\nCompanion,Компаньйон,Characters\n", encoding="utf-8")
        g = Glossary()
        count = g.import_csv(path)
        assert count == 1
        assert g.entries[0].source_term == "Companion"
    print("  PASS test_import_csv_alternate_headers")


# ── TBX persistence ────────────────────────────────────────────────────────────


def test_export_and_import_tbx():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "glossary.tbx"
        g1 = Glossary()
        g1.add_entry(
            GlossaryEntry(
                source_term="Companion",
                target_term="Компаньйон",
                category="Characters",
                definition="A companion NPC",
                notes="Appears in dialogue",
                examples=["Talk to your companion."],
            )
        )
        g1.export_tbx(path)

        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "Companion" in content
        assert "Компаньйон" in content
        assert "Characters" in content

        g2 = Glossary()
        count = g2.import_tbx(path)
        assert count == 1
        e = g2.entries[0]
        assert e.source_term == "Companion"
        assert e.target_term == "Компаньйон"
        assert e.definition == "A companion NPC"
    print("  PASS test_export_and_import_tbx")


# ── GlossaryManager ────────────────────────────────────────────────────────────


def test_manager_global_lookup():
    with tempfile.TemporaryDirectory() as tmp:
        mgr = GlossaryManager(Path(tmp))
        mgr.global_glossary.add_entry(
            _make_entry(source_term="Companion", target_term="Компаньйон")
        )
        hits = mgr.find_terms_in_text("Meet the Companion.")
        assert len(hits) == 1
    print("  PASS test_manager_global_lookup")


def test_manager_project_shadows_global():
    with tempfile.TemporaryDirectory() as tmp:
        mgr = GlossaryManager(Path(tmp))
        mgr.global_glossary.add_entry(
            _make_entry(source_term="Ship", target_term="Корабель")
        )
        proj = Glossary(None, label="Project")
        proj.add_entry(_make_entry(source_term="Ship", target_term="Шип (project)"))
        mgr.project_glossary = proj

        hits = mgr.find_terms_in_text("Board the Ship.")
        assert len(hits) == 1
        assert hits[0][2].target_term == "Шип (project)"
    print("  PASS test_manager_project_shadows_global")


def test_manager_validate_translation_pass():
    with tempfile.TemporaryDirectory() as tmp:
        mgr = GlossaryManager(Path(tmp))
        mgr.global_glossary.add_entry(
            _make_entry(source_term="Companion", target_term="Компаньйон")
        )
        issues = mgr.validate_translation(
            "Meet the Companion.", "Зустрінь Компаньйона."
        )
        assert issues == []
    print("  PASS test_manager_validate_translation_pass")


def test_manager_validate_translation_fail():
    with tempfile.TemporaryDirectory() as tmp:
        mgr = GlossaryManager(Path(tmp))
        mgr.global_glossary.add_entry(
            _make_entry(source_term="Companion", target_term="Компаньйон")
        )
        issues = mgr.validate_translation(
            "Meet the Companion.", "Зустрінь свого союзника."
        )
        assert len(issues) == 1
        assert issues[0][0].source_term == "Companion"
    print("  PASS test_manager_validate_translation_fail")


def test_manager_build_prompt_snippet():
    with tempfile.TemporaryDirectory() as tmp:
        mgr = GlossaryManager(Path(tmp))
        mgr.global_glossary.add_entry(
            _make_entry(source_term="Companion", target_term="Компаньйон")
        )
        snippet = mgr.build_prompt_snippet("Meet the Companion.")
        assert "Companion" in snippet
        assert "Компаньйон" in snippet
        assert "→" in snippet
    print(f"  PASS test_manager_build_prompt_snippet  ({snippet!r})")


def test_manager_build_prompt_snippet_no_match():
    with tempfile.TemporaryDirectory() as tmp:
        mgr = GlossaryManager(Path(tmp))
        mgr.global_glossary.add_entry(_make_entry(source_term="Ship"))
        snippet = mgr.build_prompt_snippet("No relevant terms here.")
        assert snippet == ""
    print("  PASS test_manager_build_prompt_snippet_no_match")


def test_manager_load_project_glossary():
    with tempfile.TemporaryDirectory() as tmp:
        src_file = Path(tmp) / "starfield_en.strings"
        src_file.touch()
        mgr = GlossaryManager(Path(tmp))
        proj = mgr.load_project_glossary(src_file)
        assert proj is not None
        expected_path = Path(tmp) / "starfield_en.glossary.json"
        assert proj.path == expected_path
    print("  PASS test_manager_load_project_glossary")


def test_manager_all_entries():
    with tempfile.TemporaryDirectory() as tmp:
        mgr = GlossaryManager(Path(tmp))
        mgr.global_glossary.add_entry(_make_entry(source_term="GlobalTerm"))
        proj = Glossary(None, "project")
        proj.add_entry(_make_entry(source_term="ProjectTerm"))
        mgr.project_glossary = proj
        all_e = mgr.all_entries()
        scopes = [s for s, _ in all_e]
        assert "global" in scopes
        assert "project" in scopes
    print("  PASS test_manager_all_entries")


# ── runner ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_entry_auto_id,
        test_entry_defaults,
        test_add_and_retrieve,
        test_update_entry,
        test_remove_entry,
        test_remove_entries_bulk,
        test_clear,
        test_search_by_source,
        test_search_by_target,
        test_search_empty_returns_all,
        test_filter_by_category,
        test_categories,
        test_find_terms_basic,
        test_find_terms_case_insensitive,
        test_find_terms_word_boundary,
        test_find_terms_multiple,
        test_find_terms_sorted_by_position,
        test_find_terms_empty_text,
        test_save_and_load_json,
        test_load_json_skips_empty_source,
        test_export_and_import_csv,
        test_import_csv_alternate_headers,
        test_export_and_import_tbx,
        test_manager_global_lookup,
        test_manager_project_shadows_global,
        test_manager_validate_translation_pass,
        test_manager_validate_translation_fail,
        test_manager_build_prompt_snippet,
        test_manager_build_prompt_snippet_no_match,
        test_manager_load_project_glossary,
        test_manager_all_entries,
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
