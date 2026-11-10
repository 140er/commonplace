"""Canonical CLI output remains stable for raw and authorized structured hits."""

import pytest

from mempalace.searcher import render_cli_search


def test_canonical_cli_hit_golden():
    flat = {
        "wing": "shared",
        "room": "notes",
        "source_file": "note.md",
        "source_path": "/tmp/note.md",
        "text": "\nOrdinary words\nsecond line\n",
        "distance": 0.368,
        "similarity": 0.632,
        "bm25_score": 1.445,
        "also_in": [{"source_file": "copy.md"}],
    }
    raw = {key: value for key, value in flat.items() if key not in ("source_path", "similarity")}
    raw["metadata"] = {"wing": "shared", "room": "notes", "source_file": "/tmp/note.md"}
    expected = """
============================================================
  Results for: "Ordinary"
  Wing: shared
  Room: notes
  Since: 2026-01-01
  Before: 2026-02-01
============================================================

  [1] shared / notes
      Source: note.md
      Also in: copy.md
      Match:  cosine_sim=0.632  bm25=1.445

      Ordinary words
      second line

  --------------------------------------------------------

"""
    args = dict(wing="shared", room="notes", since="2026-01-01", before="2026-02-01")
    assert render_cli_search("Ordinary", [raw], **args) == expected
    assert render_cli_search("Ordinary", [flat], **args) == expected


def test_canonical_cli_empty_golden():
    assert render_cli_search("Ordinary", []) == '\n  No results found for: "Ordinary"\n'


@pytest.mark.parametrize("vector_disabled", [False, True])
def test_canonical_cli_lexical_match_and_empty_source(vector_disabled):
    hit = {
        "wing": "shared",
        "room": "notes",
        "source_file": "?",
        "source_path": "",
        "text": "  Ordinary words  ",
        "distance": None,
        "bm25_score": 1.25,
    }
    output = render_cli_search("Ordinary", [hit], vector_disabled=vector_disabled)
    if vector_disabled:
        assert "Showing BM25-only results" in output
        assert "Match:  bm25=1.25  (vector disabled)" in output
    else:
        assert "      Source: \n" in output
        assert "Match:  bm25=1.25  (lexical candidate)" in output
    assert "      Ordinary words\n" in output
