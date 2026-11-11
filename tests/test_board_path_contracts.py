"""Glob overlap rules of card path contracts (commonplace.boards.contracts)."""

import pytest

from commonplace.boards import contracts


@pytest.mark.parametrize(
    "a,b",
    [
        ("src/api/**", "src/api/users.py"),
        ("src/api/users.py", "src/api/users.py"),
        ("src", "src/api/users.py"),  # a directory owns what is below it
        ("src/*.py", "src/a.py"),
        ("**", "anything/at/all.rs"),
        ("src/**/test_*.py", "src/a/b/c.py"),
        ("src/a*", "src/*b"),  # both wild: may share a name
        ("*.md", "*.md"),
    ],
)
def test_overlapping_globs(a, b):
    assert contracts.globs_overlap(a, b) and contracts.globs_overlap(b, a)


@pytest.mark.parametrize(
    "a,b",
    [
        ("src/api/**", "src/web/**"),
        ("src/api/users.py", "src/api/teams.py"),
        ("docs/*.md", "src/*.md"),
        ("src/*.py", "src/a/b.py"),
        ("src/a.py", "src/a"),
    ],
)
def test_disjoint_globs(a, b):
    assert not contracts.globs_overlap(a, b) and not contracts.globs_overlap(b, a)


def test_first_overlap_and_empty_contracts():
    assert contracts.first_overlap(["a/**", "b/x"], ["c/y", "b/**"]) == ("b/x", "b/**")
    assert contracts.first_overlap(["a/**"], ["b/**"]) is None
    assert contracts.first_overlap([], ["b/**"]) is None
    assert contracts.first_overlap(["a/**"], None) is None


def test_parse_is_forgiving():
    assert contracts.parse(None) == [] and contracts.parse("") == []
    assert contracts.parse("not json") == [] and contracts.parse('{"a": 1}') == []
    assert contracts.parse('["a", 3, "b"]') == ["a", "b"]
