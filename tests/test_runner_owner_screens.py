"""Owner runner screens: refusal wording, owner-only routes and the page's token hygiene."""

import re
from pathlib import Path

import pytest

from tests.runner_harness import (
    card,
    cli_token,
    http,
    member,
    owners as owners,
    signup_hub as signup_hub,
)
from tests.test_runner_start import _start
from tests.test_runner_write_wings import WINGS, _granted, _patch

STATIC = Path(__file__).resolve().parents[1] / "commonplace" / "web" / "static"


def test_refused_start_names_the_board_wing_and_the_granted_wings(signup_hub, owners):
    hub, owner = signup_hub, owners["A"]
    machine = _granted(hub, owner)
    code, body = _start(hub, machine, card(hub, owner, board="notes"), owner=owner)
    error = body["error"]
    assert code == 403 and error.endswith("outside_worker_wing"), (code, body)
    assert "board wing 'notes'" in error and "write wings: platform, compute" in error, error


@pytest.mark.parametrize("caller", ["member", "trusted", "worker"])
def test_enroll_attest_and_wings_edit_are_owner_only(signup_hub, owners, caller):
    hub, owner = signup_hub, owners["A"]
    machine = _granted(hub, owner)
    if caller == "member":
        auth = {"cookie": member(hub, owner, owners["B"])["cookie"]}
    elif caller == "worker":
        auth = {"token": machine["worker_token"]}
    else:
        _, token = cli_token(owner, "admin-agent", profile="standard", trust="trusted")
        auth = {"token": token}
    before = http(hub.port, "GET", "/api/runners", cookie=owner["cookie"])[1]
    enroll = {"label": "extra-machine", "projects": ["demo"], "topics": ["worker"], "wings": WINGS}
    attest = {"topics": ["worker"], "provider_instance_id": "p", "worker_connection_id": "x"}
    for method, path, body in (
        ("POST", "/api/runners", enroll),
        ("POST", f"/api/runners/{machine['machine_id']}/attest", attest),
    ):
        status, result = http(hub.port, method, path, body=body, **auth)
        assert status == 403, (method, path, status, result)
    status, result = _patch(hub, machine, {"wings": ["notes"]}, **auth)
    assert status == 403, (status, result)
    assert http(hub.port, "GET", "/api/runners", cookie=owner["cookie"])[1] == before


def test_runner_page_never_stores_or_logs_a_token():
    source = (STATIC / "runners.js").read_text(encoding="utf-8")
    assert not re.search(r"localStorage|sessionStorage|console\.|location\.|history\.", source)
    # The owner screens exist: enroll form, attest form and a read-wings editor.
    for marker in ("runner-enroll", "runner-attest", "runner-wings"):
        assert marker in source, marker
