"""Glob overlap rules of card path contracts (commonplace.boards.contracts)."""

import pytest

from commonplace.boards import contracts
from commonplace.extensions import ToolError


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
    assert contracts.parse(None) == [] and contracts.parse("") == [] and contracts.parse("[]") == []
    assert contracts.parse('["a", "b"]') == ["a", "b"]


@pytest.mark.parametrize("raw", ["not json", '{"a": 1}', '["a", 3, "b"]', "7", '"src"'])
def test_parse_marks_an_unreadable_value_instead_of_dropping_it(raw):
    assert contracts.parse(raw) is None


@pytest.mark.parametrize(
    "a,b",
    [
        ("src/app/*.{ts,tsx}", "src/app/page.tsx"),
        ("src/{api,web}/**", "src/api/users.py"),
        ("src/{api,web/{a,b}}/x.py", "src/web/b/x.py"),
        # Braces that cannot be expanded stand for "**", whatever follows them.
        ("src/{a/x.py", "docs/readme.md"),
        ("src/{a}/x.py", "docs/readme.md"),
        ("a}b/x", "zzz/y"),
        ("{p/q,r}/zzz/w{a,b}{c,d}{e,f}{g,h}{i,j}{k,l}{m,n}", "r/zzz/wacegikm"),
        (".", "any/file.py"),
        ("./", "any/file.py"),
        ("src//api/**", "src/api/users.py"),
        ("src/./api/**", "src/api/users.py"),
        ("src/web/../api/**", "src/api/users.py"),
        ("Docs/**", "docs/a.md"),
        ("Src/api/users.py", "src/API/Users.py"),
    ],
)
def test_overlap_after_canonicalising_expanding_and_casefolding(a, b):
    assert contracts.globs_overlap(a, b) and contracts.globs_overlap(b, a)


@pytest.mark.parametrize(
    "a,b",
    [
        ("src/app/*.{ts,tsx}", "src/app/page.py"),
        ("src/{api,web}/**", "src/db/users.py"),
        ("src/web/../api/**", "src/web/users.py"),
        ("{a,b}{c,d}", "ae"),
    ],
)
def test_disjoint_after_expanding(a, b):
    assert not contracts.globs_overlap(a, b) and not contracts.globs_overlap(b, a)


def test_too_many_alternatives_stay_conservative():
    many = "{a,b}{c,d}{e,f}{g,h}{i,j}{k,l}{m,n}"  # 128 expansions
    assert contracts.globs_overlap(many, "zzz")


def test_normalize_canonicalises_and_refuses_escapes():
    assert contracts.normalize(["./a//b/", ".", "a/b/../c", "x/{p,q}/*.py", "a//b"]) == [
        "a/b",
        "**",
        "a/c",
        "x/p/*.py",
        "x/q/*.py",
    ]
    assert contracts.normalize(["{./a,b}", "{x/../y,z}/*"]) == ["a", "b", "y/*", "z/*"]
    for bad in (["../x"], ["a/../.."], ["*/../x"], ["/a"], ["a\\b"]):
        with pytest.raises(ToolError):
            contracts.normalize(bad)


@pytest.mark.parametrize(
    "bad",
    [
        ["{../x,y}"],
        ["x/{a,../../b}"],
        ["src/{a"],
        ["src/{a}/x"],
        ["src/a}"],
        ["{a,b}{c,d}{e,f}{g,h}{i,j}{k,l}{m,n}"],  # 128 alternatives
        [f"d{i}/{{a,b,c}}" for i in range(22)],  # 66 stored globs
    ],
)
def test_normalize_refuses_unusable_braces_and_oversized_contracts(bad):
    with pytest.raises(ToolError):
        contracts.normalize(bad)


def test_comparison_budget_fails_closed():
    mine = [f"a{i}/*.py" for i in range(10)]
    theirs = [f"b{i}/*.py" for i in range(10)]
    assert contracts.first_overlap(mine, theirs, contracts.Budget(100)) is None
    with pytest.raises(contracts.TooLarge):
        contracts.first_overlap(mine, theirs, contracts.Budget(99))
    # The worst glob lists a start could once have compared: 32 globs of 64 alternatives.
    alternatives = "{a,b}{c,d}{e,f}{g,h}{i,j}{k,l}"
    wide = [f"s{i}/{alternatives}/x" for i in range(32)]
    with pytest.raises(contracts.TooLarge):
        contracts.first_overlap(wide, wide, contracts.Budget())
