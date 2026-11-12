"""Owner invite block and workspace switcher through a fake DOM, no browser."""

import json
import shutil
import subprocess
from pathlib import Path

from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.test_commonplace_web import request


def _served(hub, name):
    status, _, body = request(hub, f"/app/{name}.js")
    assert status == 200, f"{name} asset must be served"
    return body.decode()


def test_invite_block_and_workspace_switcher(hub):
    node = shutil.which("node")
    assert node, "Node is required for the invite block checks"
    sources = {name: _served(hub, name) for name in ("invites", "admin")}
    result = subprocess.run(
        [node, str(Path(__file__).with_name("invites_ui.mjs"))],
        input=json.dumps(sources),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr[-3000:]


def test_app_wires_invites_and_the_switcher(hub):
    app, palette = _served(hub, "app"), _served(hub, "palette")
    assert 'import { createInviteBlock, openWorkspaces } from "./invites.js";' in app
    assert "invites: createInviteBlock({ api, e })" in app
    assert 'action === "workspaces") openWorkspaces({ api, e, onError: showError })' in app
    assert "add('workspaces', 'Switch workspace', 'Workspace', { action: 'workspaces'" in palette
