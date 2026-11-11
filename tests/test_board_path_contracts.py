"""Glob overlap rules of card path contracts (commonplace.boards.contracts)."""

import json

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
    assert contracts.parse(None) == [] and contracts.parse("[]") == []
    assert contracts.parse('["a", "b"]') == ["a", "b"]


@pytest.mark.parametrize("raw", ["", "not json", '{"a": 1}', '["a", 3, "b"]', "7", '"src"', "null"])
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
        ["{/a,b}"],
        ["{x,/etc}"],
        ["x/{/a,b}"],
        ["src/{/etc,api}"],
        ["x{/a,b}"],
        ["x/{,/a}"],
        ["{a,{/b,c}}"],
        ["{src,}/etc"],
        ["{,}/etc"],
        ["{,}/a"],
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


def test_comparison_budget_counts_segment_steps():
    mine = [f"a{i}/*.py" for i in range(10)]  # 2 segments deep
    theirs = [f"b{i}/*.py" for i in range(10)]
    assert contracts.first_overlap(mine, theirs, contracts.Budget(200)) is None
    with pytest.raises(contracts.TooLarge):
        contracts.first_overlap(mine, theirs, contracts.Budget(199))
    deep = "/".join(["?"] * 20)
    with pytest.raises(contracts.TooLarge):  # the same 100 pairs, 21 segments deep
        contracts.first_overlap(
            [f"{deep}/m{i}" for i in range(10)],
            [f"{deep}/t{i}" for i in range(10)],
            contracts.Budget(200),
        )


def test_permitted_deep_wildcard_contracts_exhaust_the_budget_in_a_few_cards():
    """What ``normalize`` accepts at most, 64 globs of 89 segments, costs 4 096 x 89 work
    units per running card, so the budget is gone after a fixed number of them. (The time
    that allows was measured separately; see the budget comment in contracts.py.)"""
    deep = "/".join(["?"] * 88)
    mine = contracts.normalize([f"{deep}/{{m{i},n{i}}}" for i in range(32)])
    theirs = contracts.normalize([f"{deep}/{{t{i},u{i}}}" for i in range(32)])
    assert len(mine) == len(theirs) == 64
    budget, cards = contracts.Budget(), 0
    with pytest.raises(contracts.TooLarge):
        while True:
            assert contracts.first_overlap(mine, theirs, budget) is None
            cards += 1
    assert cards == contracts.MAX_STEPS // (64 * 64 * 89) == 6


def test_long_single_segment_contracts_are_charged_by_length():
    """A segment's cost grows with its length: 64 globs of one 173-character segment cost
    6 units per pair (1 + 173 // 32), not 1, so far fewer running cards fit the budget than
    the segment count alone would allow."""
    mine = contracts.normalize(["?" * 170 + f"{{m{i},n{i}}}" for i in range(32)])
    theirs = contracts.normalize(["x" * 170 + f"{{t{i},u{i}}}" for i in range(32)])
    assert len(mine) == len(theirs) == 64
    weight = 1 + len(mine[0]) // contracts.SEGMENT_CHARS
    assert weight == 6
    budget, cards = contracts.Budget(), 0
    with pytest.raises(contracts.TooLarge):
        while True:
            assert contracts.first_overlap(mine, theirs, budget) is None
            cards += 1
    compiles = sum(contracts._compile_units(text) for text in {g.split("/")[0] for g in mine})
    assert cards == (contracts.MAX_STEPS - compiles) // (64 * 64 * weight)
    assert cards < contracts.MAX_STEPS // (64 * 64) // 4  # charging segments only: 537


def _stored(globs):
    return json.dumps(contracts.normalize(globs))


def test_many_large_running_cards_are_charged_before_they_are_parsed():
    """Reviewer shape L: a cheap new card against running cards of 64 stored globs of 89
    segments. Each running card costs its stored size, so the budget runs out after a few
    hundred of them; charging comparisons alone allowed about nineteen thousand."""
    running = [
        (k, json.dumps([("x/" * 88) + f"y{k:04d}{h:02d}" for h in range(64)])) for k in range(1000)
    ]
    budget = contracts.Budget()
    with pytest.raises(contracts.TooLarge) as info:
        contracts.first_overlap_running(["src/a.py"], running, budget)
    assert 300 < info.value.key < 500
    assert budget.left < 0
    assert contracts.size_units(running[0][1]) > 6000  # the stored size, billed before parsing


def test_unreadable_and_empty_running_contracts_are_billed_and_read_as_expected():
    budget = contracts.Budget()
    assert (
        contracts.first_overlap_running(["src/a.py"], [(1, "[]"), (2, '["docs/**"]')], budget)
        is None
    )
    assert 0 < contracts.MAX_STEPS - budget.left < 100
    found = contracts.first_overlap_running(["src/a.py"], [(1, "not json")], contracts.Budget())
    assert found == (1, ["**"], ("src/a.py", "**"))


def test_tiny_globs_are_billed_per_glob():
    """Reviewer shape T1: 64 short literal globs per running card. Each glob costs real work
    to read, expand and canonicalise, so about 3 700 such cards use up the budget; billing
    one unit per glob let the loop go on for about 16 000."""
    running = [(k, json.dumps([f"c{k}_{j}x" for j in range(64)])) for k in range(6000)]
    with pytest.raises(contracts.TooLarge) as info:
        contracts.first_overlap_running(["src/a.py"], running, contracts.Budget())
    assert 3000 < info.value.key < 4500


def test_long_distinct_wildcard_globs_are_billed_by_length():
    """Reviewer shape T2: 64 distinct 185-character wildcard globs per running card (the
    validator accepts them). Compiling one costs about 140 us, so the budget runs out after
    under a hundred such cards; a flat per-pattern charge let it go on for about 240."""
    running = [
        (
            k,
            _stored(
                [("?" * 179) + "{" + f"{k:03d}{j:02d}a,{k:03d}{j:02d}b" + "}" for j in range(32)]
            ),
        )
        for k in range(240)
    ]
    contracts._MATCHERS.clear()
    with pytest.raises(contracts.TooLarge) as info:
        contracts.first_overlap_running(["src/a.py"], running, contracts.Budget())
    assert info.value.key < 120


def test_compile_charge_follows_length_stars_and_classes():
    plain = contracts._compile_units("?" * 20)
    assert contracts._compile_units("?" * 185) > 3 * plain
    assert contracts._compile_units("*a" * 90) > 10 * contracts._compile_units("?" * 180) // 4
    assert contracts._compile_units("[a-z]" * 35) > contracts._compile_units("?" * 175)


def test_the_new_cards_own_stored_size_is_billed_first():
    mine = [f"src/{i}/*.py" for i in range(8)]
    cost = contracts.size_units(mine)
    contracts.first_overlap_running(mine, [], contracts.Budget(cost + 100))
    with pytest.raises(contracts.TooLarge) as info:
        contracts.first_overlap_running(mine, [], contracts.Budget(cost - 1))
    assert not hasattr(info.value, "key")  # it ran out before any running card


def test_distinct_wildcard_patterns_are_charged_for_compiling():
    """Reviewer shape Q2: 64 globs of 8 segments, each segment a distinct wildcard pattern,
    against a literal glob. Every pattern is compiled once and billed by its length, so the
    budget is gone after a few dozen running cards and only a bounded number was compiled."""

    def digits(n):
        out = []
        for _ in range(13):
            n, r = divmod(n, 3)
            out.append("x?*"[r])
        return "".join(reversed(out)) + "x" * 8

    def card(k):
        inputs = []
        for i in range(32):
            base = k * 32 + i
            prefix = [digits(base * 7 + j) for j in range(7)]
            group = "{" + digits(10**6 + base * 2) + "," + digits(10**6 + base * 2 + 1) + "}"
            inputs.append("/".join(prefix) + "/" + group)
        return _stored(inputs)

    contracts._MATCHERS.clear()
    running = [(k, card(k)) for k in range(100)]
    mine = ["/".join(["x" * 20] * 7 + ["y" * 20])]
    with pytest.raises(contracts.TooLarge) as info:
        contracts.first_overlap_running(mine, running, contracts.Budget())
    assert info.value.key < 100
    assert (
        len(contracts._MATCHERS) <= contracts.MAX_STEPS // contracts.COMPILE_UNITS
    )  # a loose bound
    # A pattern that is already compiled is not billed again.
    first = contracts.Budget()
    contracts.first_overlap(["*.py"], ["a.py"], first)
    again = contracts.Budget()
    contracts.first_overlap(["*.py"], ["a.py"], again)
    assert contracts._compile_units("*.py") == 70 + 2 * 4 + 21
    assert again.left - first.left == contracts._compile_units("*.py")


def test_live_cards_needs_an_explicit_hold():
    """The cap keeps its hour and the contract check does not: no default may pick one."""
    import inspect

    from commonplace.orch import runner_bridge

    hold = inspect.signature(runner_bridge._live_cards).parameters["hold"]
    assert hold.default is inspect.Parameter.empty and hold.kind is hold.KEYWORD_ONLY
    # The worst glob lists a start could once have compared: 32 globs of 64 alternatives.
    alternatives = "{a,b}{c,d}{e,f}{g,h}{i,j}{k,l}"
    wide = [f"s{i}/{alternatives}/x" for i in range(32)]
    with pytest.raises(contracts.TooLarge):
        contracts.first_overlap(wide, wide, contracts.Budget())
