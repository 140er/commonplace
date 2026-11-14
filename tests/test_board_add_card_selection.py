"""Add card shows and selects the new card; the side panel never shows another card; Escape closes it.

Usability test, 14 Nov 2026: after Add card the form stayed open and the side panel first showed an
unrelated card with a live Approve button while the address already named the new card. Escape did not
close the panel, the logo seemed to do nothing while the project list loaded, and slow screens showed a
bare "Loading…". Browser-free: node runs the shell's own functions (sliced out of app.js) against the real
board renderer; the mutation tests re-run each case against a copy with one fix undone.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "commonplace/web/static"
SCRIPT = Path(__file__).with_name("board_add_card_selection.mjs")
CASES = ["add_flow", "add_flow_classic", "stale_panel", "escape", "logo", "loading"]

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is required")


def run_case(case, static=STATIC):
    return subprocess.run(
        ["node", str(SCRIPT), case],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env={**os.environ, "STATIC_DIR": str(static)},
    )


@pytest.mark.parametrize("case", CASES)
def test_shell_behaviour(case):
    result = run_case(case)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"{case}: PASS" in result.stdout


# (case, file, text that makes the behaviour work, text with the fix undone)
MUTATIONS = {
    "form stays open": ("add_flow", "app.js", "  state.addingCard = false;\n  [", "  [ "),
    "new card not selected": (
        "add_flow",
        "app.js",
        "state.selected = card.number; state.boardFilter",
        "state.boardFilter",
    ),
    "filter not lifted": (
        "add_flow",
        "app.js",
        'state.boardFilter = "all"; state.justAdded',
        "state.justAdded",
    ),
    "address not set": (
        "add_flow",
        "app.js",
        "  location.hash = `#/card/${card.number}`;\n}\nfunction markOpenCard",
        "}\nfunction markOpenCard",
    ),
    "column not shown": (
        "add_flow",
        "app.js",
        "  state.boardColumn = cardColumn(",
        "  void cardColumn(",
    ),
    "no notice": (
        "add_flow",
        "deck-board.js",
        'role="status" data-board-added',
        "data-board-added",
    ),
    "stale panel kept": (
        "stale_panel",
        "app.js",
        'detailsUI?.stop(); detailsUI = null; showLoading(cardPanelHost, "Loading card…");',
        "void 0;",
    ),
    "escape ignored in a field": (
        "escape",
        "app.js",
        'if (event.key === "Escape" && escapeInPanel(event, typing, panelOpen)) return;',
        "",
    ),
    "escape drops typed text": (
        "escape",
        "app.js",
        "if (typed) field.blur(); else closeCardPanel();",
        "closeCardPanel();",
    ),
    "logo waits for the project list": (
        "logo",
        "app.js",
        '(hash === "#/" && state.deck?.on() !== true) ||',
        'hash === "#/" ||',
    ),
    "no loading text on route": (
        "logo",
        "app.js",
        'showLoading(root.querySelector("#content"), "Loading…")',
        'root.querySelector("#content").innerHTML = message("Loading…")',
    ),
    "hint too late": ("loading", "loading.js", "SLOW_AFTER_MS = 1000", "SLOW_AFTER_MS = 5000"),
    "hint on a replaced screen": (
        "loading",
        "loading.js",
        "latest.get(host) === id && host.innerHTML === html",
        "true",
    ),
    "hint on a newer identical text": ("loading", "loading.js", "latest.get(host) === id && ", ""),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_mutation_is_caught(name, tmp_path):
    case, file, good, bad = MUTATIONS[name]
    copy = tmp_path / "static"
    copy.mkdir()
    for js in STATIC.glob("*.js"):
        shutil.copy(js, copy / js.name)
    source = (copy / file).read_text()
    assert source.count(good) == 1, f"{name}: the guarded text must appear exactly once in {file}"
    (copy / file).write_text(source.replace(good, bad))
    assert run_case(case, copy).returncode != 0, f"{name}: the {case} case did not notice"
