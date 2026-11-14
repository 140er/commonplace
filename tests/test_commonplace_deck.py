"""The Deck look (the default; classic is an explicit opt-out): switch, bar, tabs, screen registry and shell wiring, run in node."""

import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "commonplace/web/static"


def test_deck_switch_bar_and_wiring():
    for name in ("deck.js", "deck.css", "fonts/inter-latin.woff2", "fonts/Inter-OFL.txt"):
        assert (STATIC / name).exists(), name
    node = shutil.which("node")
    assert node, "Node is required for the Deck checks"
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("commonplace_deck.mjs"))],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-2000:]


# Each mutation puts back a rule of the old classic-by-default look; the harness must notice.
MUTATIONS = (
    ("deck.js", "storage?.getItem(UI_KEY) !== 'classic'", "storage?.getItem(UI_KEY) === 'deck'"),
    ("deck.js", "catch { return true; }", "catch { return false; }"),
    (
        "deck.js",
        "storage?.setItem(UI_KEY, on ? 'deck' : 'classic')",
        "(on ? storage?.setItem(UI_KEY, 'deck') : storage?.removeItem(UI_KEY))",
    ),
    ("deck-prepaint.js", "on = saved !== 'classic'", "on = saved === 'deck'"),
)


@pytest.mark.parametrize(("name", "good", "old"), MUTATIONS)
def test_harness_fails_when_the_old_classic_default_returns(tmp_path, name, good, old):
    static = tmp_path / "commonplace/web/static"
    shutil.copytree(STATIC, static)
    (tmp_path / "tests").mkdir()
    shutil.copy(Path(__file__).with_name("commonplace_deck.mjs"), tmp_path / "tests")
    text = (static / name).read_text()
    assert good in text, "the mutation no longer matches the code"
    (static / name).write_text(text.replace(good, old))
    result = subprocess.run(
        [shutil.which("node"), "--test", str(tmp_path / "tests/commonplace_deck.mjs")],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0, "the old behaviour went unnoticed"
