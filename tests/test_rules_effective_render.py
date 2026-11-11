"""Effective rendering stays byte-compatible across truncation budgets."""

import json
import random
from pathlib import Path

import pytest

from commonplace.rules import render
from commonplace.rules.render import REPLY_BYTES, TOO_LARGE, length, md, text


def reference_effective(rows, seq, rules_seq, *, format="text", mode="off", budget=REPLY_BYTES):
    """Count is the total authorized set; truncation never splits a rule."""
    render = md if format == "md" else text
    header = f"effective rules: {len(rows)}"
    marker = f"rules_seq {rules_seq}" if mode != "off" else "rules_seq off"

    def reply(selected, tail):
        return "\n".join([header, render(selected), f"seq {seq}", tail])

    complete = reply(rows, marker)
    if length(complete) <= budget:
        return complete, "served"
    shown = []
    for row in rows:
        candidate = [*shown, row]
        tail = f"Incomplete: {len(rows) - len(candidate)} rules not shown.\n{TOO_LARGE}"
        if length(reply(candidate, tail)) <= budget:
            shown.append(row)
    tail = f"Incomplete: {len(rows) - len(shown)} rules not shown.\n{TOO_LARGE}"
    return reply(shown, tail), "too_large"


@pytest.mark.parametrize("format", ["md", "text"])
@pytest.mark.parametrize("mode", ["off", "warn"])
def test_effective_matches_original_across_budget_sweep(format, mode):
    rng = random.Random(124907)
    alphabet = "aé界🧠<>@&\n\t\x85\u202e\u2028\U000e0001"
    for size in (0, 1, 9, 10, 11, 24):
        rows = [
            dict(
                key=f"r-{i}",
                kind=rng.choice(list(dict(render.HEADINGS))),
                scope=rng.choice(["global", "board", "repo", "author"]),
                scope_ref=rng.choice(["ops", "repo/app", "ops space", "a@b", "界🧠"]),
                text="".join(rng.choices(alphabet, k=rng.randrange(1, 45))),
                status="accepted",
                source="owner",
                version=rng.randrange(1, 12),
            )
            for i in range(size)
        ]
        full, _ = reference_effective(rows, 123, 123456789, format=format, mode=mode, budget=100000)
        budgets = set(range(0, length(full) + 38, 37)) | {length(full), length(full) - 1}
        for budget in list(budgets):
            expected = reference_effective(
                rows, 123, 123456789, format=format, mode=mode, budget=budget
            )
            if expected[1] == "too_large":
                budgets.update((length(expected[0]), length(expected[0]) - 1))
        for budget in sorted(budgets):
            assert render.effective(
                rows, 123, 123456789, format=format, mode=mode, budget=budget
            ) == reference_effective(rows, 123, 123456789, format=format, mode=mode, budget=budget)


def test_effective_captured_goldens():
    captured = json.loads(
        Path(__file__).with_name("fixtures").joinpath("rules_effective_goldens.json").read_text()
    )
    for case in captured["cases"]:
        assert render.effective(
            captured["rows"],
            9,
            123456789,
            format=case["format"],
            mode="warn",
            budget=case["budget"],
        ) == (case["reply"], case["state"])


@pytest.mark.parametrize("format", ["md", "text"])
@pytest.mark.parametrize("budget", [240, 1000000])
def test_effective_quotes_each_rule_and_label_at_most_once(monkeypatch, format, budget):
    rows = [
        dict(
            key=str(i),
            kind="limit",
            scope="board",
            scope_ref="ops space",
            text="x" * 150,
            status="accepted",
            source="owner",
            version=1,
        )
        for i in range(1000)
    ]
    calls = []
    original = render.quote

    def quote(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(render, "quote", quote)
    render.effective(rows, 1, 123456789, format=format, mode="warn", budget=budget)
    assert len(calls) == len(rows) * (2 if format == "md" else 1)
