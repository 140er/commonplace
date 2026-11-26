"""Cold shell data stays independent of slow secondary reads."""

import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "slow,route",
    [
        (slow, route)
        for route in ["#/boards", "#/board/project", "#/settings", "#/memory", "#/bots/project"]
        for slow in ["/api/needs-me", "/api/rules/count", "/api/bots"]
        if not (slow == "/api/bots" and route.startswith("#/bots"))
    ],
)
def test_slow_shell_dependency(slow, route):
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("cold_start.mjs")), slow, route],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("method", ["", "GET"])
@pytest.mark.parametrize(
    "path", ["/api/memory/search?q=words", "/api/cards/1", "/api/boards/project/cards"]
)
def test_ordinary_get_preserves_slow_result(path, method):
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("ordinary_get.mjs")), path, method],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_bootstrap_directory_deadline_falls_back():
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("cold_start.mjs")), "/api/boards?view=shell"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("state", ["loading", "stale", "unavailable", "unrecognized"])
def test_project_count_state_remains_visible(state):
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync('commonplace/web/static/app.js', 'utf8');
const content = { innerHTML: '' }, pending = process.argv[1];
const context = { title() {}, enc: encodeURIComponent, e: String,
  root: { querySelector: () => content },
  state: { base: '#/boards', boards: [{ slug: 'project', counts: null, counts_state: pending }] } };
vm.runInNewContext(source.slice(source.indexOf('function boardsView()'), source.indexOf('// Needs me draws on')), context);
context.boardsView();
assert.match(content.innerHTML, pending === 'unavailable' ? /Counts unavailable/ : /Still loading counts/);
"""
    result = subprocess.run(
        ["node", "-e", script, state], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stdout + result.stderr
