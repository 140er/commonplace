"""Card action contracts exercised through the isolated JavaScript controller."""

import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "commonplace" / "web" / "static"
HARNESS = Path(__file__).with_name("workbench_cardmenu.mjs")
REDS = [
    "surface",
    "move",
    "title",
    "copy",
    "focus",
    "keys",
    "failure",
    "race",
    "review",
    "stale",
    "deck",
    "proposal",
]


@pytest.mark.parametrize("case", REDS)
def test_red_cardmenu(case):
    assert (STATIC / "cardmenu.js").is_file(), "Card menu module missing"  # RED
    result = subprocess.run(
        ["node", str(HARNESS), case], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_guard_node_available():
    assert shutil.which("node") is not None


def test_guard_versioned_card_route():
    source = (STATIC / "app.js").read_text()
    # The versioned PATCH lives in the shared move helper; the shell only calls it.
    mover = (STATIC / "cardmove.js").read_text()
    assert "api(`/api/cards/${card.number}`, { method: 'PATCH', body: { version, status" in mover
    assert "version = card.version" in mover or "let version = card.version" in mover
    assert "reviewForm" not in source
    assert "putMove(api, card, status" in source
