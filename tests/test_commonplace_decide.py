"""Decide queue: the phone route's states, and the pagehide send."""

import shutil
import subprocess
from pathlib import Path

import pytest

from commonplace.web import STATIC


def test_decide_queue_states_render():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is needed for the decide queue render check")
    script = Path(__file__).with_name("commonplace_decide_render.mjs")
    subprocess.run([node, str(script)], check=True, timeout=20)


def test_hiding_the_page_sends_the_held_answer():
    app = (Path(STATIC) / "app.js").read_text()
    assert "pagehide" in app and "keepalive" in app
    assert "queueFlush(true)" in app
    assert 'state.base = start !== undefined ? "#/decide"' in app
