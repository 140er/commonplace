"""Look switch (Deck | Classic) contracts: top-of-Settings placement, both-way switching without a freeze,
and Rotate/Revoke kept apart from everyday settings. Browser-free; the browser cases live in
test_deck_look_switch_browser.py."""

import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "commonplace/web/static"
HARNESS = Path(__file__).with_name("deck_look_switch.mjs")


def run_node(path):
    return subprocess.run(
        [shutil.which("node"), "--test", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_look_switch_harness():
    assert shutil.which("node"), "Node is required for the Look switch checks"
    result = run_node(HARNESS)
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-2000:]


# Each mutation breaks one rule of the new behaviour; the harness must notice every one.
MUTATIONS = (
    (
        "deck.js",
        "box.insertAdjacentHTML('afterbegin', lookHTML(",
        "box.insertAdjacentHTML('beforeend', lookHTML(",
    ),
    ("deck.js", "    appearance.stop();\n    setDeck(on, storage);", "    setDeck(on, storage);"),
    ("deck.js", "if (on === deckOn(storage)) return;", ""),
    ("deck.js", "if (look) choose(look.dataset.look);", "if (look) choose('classic');"),
    (
        "deck.js",
        "    leave();\n    reload();\n  }\n  const toggle",
        "    leave();\n    reload();\n    reload();\n  }\n  const toggle",
    ),
    ("deck-settings-capy.js", "['look', 'deck-bot-sharing',", "['deck-bot-sharing',"),
    ("deck-dither.js", "limit > 0 ? Math.min(size, Math.ceil(limit * dpr)) : size", "size"),
    (
        "agents.js",
        'const tokenGroup = (...buttons) => `<details class="agent-tokens" data-agent-tokens><summary>Access tokens</summary>${buttons.join("")}</details>`;',
        'const tokenGroup = (...buttons) => buttons.join("");',
    ),
)


@pytest.mark.parametrize(("name", "good", "bad"), MUTATIONS)
def test_harness_fails_when_a_look_switch_rule_is_broken(tmp_path, name, good, bad):
    static = tmp_path / "commonplace/web/static"
    shutil.copytree(STATIC, static)
    (tmp_path / "tests").mkdir()
    shutil.copy(HARNESS, tmp_path / "tests")
    text = (static / name).read_text()
    assert good in text, "the mutation no longer matches the code"
    (static / name).write_text(text.replace(good, bad))
    result = run_node(tmp_path / "tests/deck_look_switch.mjs")
    assert result.returncode != 0, "the broken rule went unnoticed"
