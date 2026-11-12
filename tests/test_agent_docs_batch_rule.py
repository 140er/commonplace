"""The batch merge rule lives in the agent docs every agent reads (AGENTS.md and CLAUDE.md).

The rule decided on 10 Nov 2026 existed only as a board-card comment, conditional on a tooling card, so agent
briefs kept telling builders to wait for a full-suite run per PR. These tests keep the rule in both
files and the two files identical, so no agent reads a different policy.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ("AGENTS.md", "CLAUDE.md")
SECTION = "## Testing and merging (batch merge rule)"


def _section(name: str) -> str:
    text = (ROOT / name).read_text(encoding="utf-8")
    assert text.count(SECTION) == 1, f"{name} must hold the batch merge rule section exactly once"
    body = text.split(SECTION, 1)[1]
    return body.split("\n## ", 1)[0]


def test_agent_docs_are_identical():
    assert (ROOT / "AGENTS.md").read_text(encoding="utf-8") == (ROOT / "CLAUDE.md").read_text(
        encoding="utf-8"
    )


def test_batch_rule_states_the_per_pr_gate_and_the_batch_lane():
    for name in DOCS:
        body = _section(name)
        assert "affected tests" in body
        assert "Do not run, request or wait for a full-suite run per PR" in body
        assert "full suite runs on the `develop` tip" in body
        assert "bisected" in body and "revert PR" in body
        assert "hold **only while `develop` is red**" in body


def test_batch_rule_says_waiting_is_a_bug():
    for name in DOCS:
        body = _section(name)
        assert "never be the bottleneck" in body
        assert "never waits for a check" in body
