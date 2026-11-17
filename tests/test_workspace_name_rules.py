"""The browser uses the authoritative name rule, including Unicode semantics."""

import json
import os
import random
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from mempalace.config import name_validation_rules, sanitize_name
from tests.test_commonplace_invites import world  # noqa: F401
from tests.test_workspace_creation import create


def test_workspace_payload_uses_authoritative_rules(world):  # noqa: F811
    response = world.owner.browser.get("/api/workspaces")
    assert response.status == 200
    assert response.json()["name_rules"] == name_validation_rules()


def test_browser_decisions_equal_server_rules():
    # Python and JS have different Unicode meanings for \\w, \\s, trim and length.
    # Exercise boundary codepoints, astral letters/digits, whitespace and limits.
    values = [
        "",
        " ",
        "A",
        "UX-TEST leave",
        "My team",
        "Anna's space",
        "a_b.c-d e'f",
        "bad/..<script>x</script>",
        "UX-TEST (leave)",
        "[Research]",
        "a\\b",
        "a..b",
        "<script>",
        "bad\x00name",
        "bad\nname",
        "_leading",
        "trailing_",
        "e\u0301",
        "é",
        "日本語",
        "١٢٣",
        "²",
        "𐐀",
        "𐐀" * 128,
        "𐐀" * 129,
        "A" * 128,
        "A" * 129,
        "\x85My team\x85",
        "\x1cA\x1f",
        "\ufeffA\ufeff",
    ]
    rng = random.Random(580)
    for _ in range(256):
        char = chr(rng.randrange(0x110000))
        values.extend([char, f"A{char}B", char + "My team" + char])
    expected = []
    for value in values:
        try:
            normalized = sanitize_name(value, "Workspace name")
        except ValueError:
            expected.append({"valid": False})
        else:
            expected.append({"valid": True, "normalized": normalized})
    script = """
      import { workspaceNameValidator } from './commonplace/web/static/invites.js';
      const { rules, values } = JSON.parse(process.env.WORKSPACE_NAME_DRIFT);
      const validator = workspaceNameValidator(rules);
      console.log(JSON.stringify(values.map(value => validator.issue(value)
        ? { valid: false } : { valid: true, normalized: validator.clean(value) })));
    """
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module", "-e", script],
        cwd=Path(__file__).resolve().parents[1],
        env={
            **os.environ,
            "WORKSPACE_NAME_DRIFT": json.dumps(
                {"rules": name_validation_rules(), "values": values}
            ),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected


@pytest.mark.parametrize("name", ["UX-TEST leave", "My team", "Anna's space"])
def test_valid_examples_keep_the_post_contract(world, monkeypatch, name):  # noqa: F811
    monkeypatch.setenv("COMMONPLACE_SIGNUP", "open")
    response = create(world, body={"name": name, "request_id": str(uuid.uuid4())})
    assert response.status == 201, response.text
    assert response.json()["workspace"]["name"] == name


@pytest.mark.parametrize("name", ["UX-TEST (leave)", "[Research]", "bad/..<script>x</script>"])
def test_server_remains_authoritative_for_invalid_names(world, monkeypatch, name):  # noqa: F811
    monkeypatch.setenv("COMMONPLACE_SIGNUP", "open")
    response = create(world, body={"name": name, "request_id": str(uuid.uuid4())})
    assert response.status == 400
    assert response.json()["error"] == (
        "Workspace name contains invalid path characters"
        if "/" in name
        else "Workspace name contains invalid characters"
    )
