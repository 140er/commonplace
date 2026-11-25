"""The read-only capabilities and status page: ``GET /api/status`` and its permission rules."""

import json
import os
import re

import pytest

from commonplace.chat_bridge import adapter, sealing
from commonplace.meetings import store as meeting_store
from commonplace.runner import costs_api
from tests._commonplace_helpers import Hub, InProcessLauncher
from tests._console_helpers import ConsoleHub
from tests._mcp_server_helpers import _patch_mcp_server
from tests.runner_harness import (
    cli_token,
    http,
    member,
    owners,  # noqa: F401
    signup_hub,  # noqa: F401
)
from tests.test_chat_bridge_foundation import SENTINEL, FakeAdapter
from tests.test_cost_ledger import _machine, _started, _usage

SIGNING = "SENTINEL-signing-secret-9f8e7d"

SECTIONS = ("skills", "bridge", "meetings", "costs")
STATES = {"ready", "empty", "off", "restricted", "error"}


@pytest.fixture(name="hub")
def hub_fixture(tmp_path, monkeypatch, config, kg):
    monkeypatch.setenv("COMMONPLACE_RATE_LIMITS", "off")
    _patch_mcp_server(monkeypatch, config, kg)
    key = tmp_path / "keys" / "wrap.key"
    key.parent.mkdir()
    key.write_bytes(os.urandom(32))
    key.chmod(0o600)
    monkeypatch.setenv(sealing.KEY_FILE_ENV, str(key))
    monkeypatch.setitem(adapter.ADAPTERS, "fake", FakeAdapter())
    raw = Hub(tmp_path / "data", InProcessLauncher(monkeypatch))
    try:
        yield ConsoleHub(raw)
    finally:
        raw.close()


def status(hub, auth):
    code, body, raw = hub.get(auth, "/api/status")
    assert code == 200, (code, body)
    return body, raw


def skill(hub, name, markdown):
    code, body, _ = hub.post(hub.owner, "/api/skills", {"name": name, "markdown": markdown})
    assert code == 201, body
    return body["skill"]["skill_id"]


def assert_explained(body):
    """Never an empty panel: every section that is not ready says why."""
    for name in SECTIONS:
        section = body[name]
        assert section["state"] in STATES, (name, section)
        if section["state"] != "ready":
            assert isinstance(section["reason"], str) and len(section["reason"]) > 20, name


def test_only_a_signed_in_person_reads_status(hub):
    assert hub.request(None, "GET", "/api/status")[0] == 401
    assert hub.get(hub.t1, "/api/status")[0] == 403
    assert hub.get(hub.t2, "/api/status")[0] == 403


def test_status_has_no_write_path(hub):
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        code, _, _ = hub.request(hub.owner, method, "/api/status", {})
        assert code in (404, 405), (method, code)


def test_a_fresh_workspace_explains_every_section(hub):
    body, _ = status(hub, hub.owner)
    assert_explained(body)
    assert body["viewer"] == {"role": "owner"}
    assert body["skills"]["state"] == "empty" and body["skills"]["items"] == []
    assert body["bridge"]["state"] == "off" and "chat" in body["bridge"]["reason"].lower()
    assert body["meetings"]["state"] == "off"
    assert "switched off" in body["meetings"]["reason"]
    costs = body["costs"]
    assert costs["state"] == "empty" and costs["scope"] == "workspace"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:]+Z", costs["window"]["start"])
    assert costs["window"]["end"] > costs["window"]["start"]
    assert "month" in costs["period"].lower() and "UTC" in costs["period"]
    assert "every run" in costs["scope_text"].lower()


def test_skills_show_name_one_line_description_and_current_scope(hub):
    framed = (
        "---\nname: Release\ndescription: Ship a release safely\n---\n# Body\nSecret body line.\n"
    )
    first = skill(hub, "Release checks", framed)
    plain = skill(hub, "Plain note", "# Heading\n\nFirst real sentence here.\nSecond line.\n")
    bare = skill(hub, "Bare", "# Only a heading\n")
    private = skill(hub, "Private one", "Owner only text.\n")
    assert hub.request(hub.owner, "DELETE", f"/api/skills/{private}/shares")[0] == 200
    project = skill(hub, "Project one", "A project skill.\n")
    assert hub.request(hub.owner, "DELETE", f"/api/skills/{project}/shares")[0] == 200
    code, body, _ = hub.post(hub.owner, f"/api/skills/{project}/shares", {"target": "project:work"})
    assert code in (200, 201), body

    body, raw = status(hub, hub.owner)
    items = {row["name"]: row for row in body["skills"]["items"]}
    assert body["skills"]["state"] == "ready"
    assert items["Release checks"]["description"] == "Ship a release safely"
    assert items["Plain note"]["description"] == "First real sentence here."
    assert items["Bare"]["description"] == ""
    assert items["Bare"]["description_note"]
    assert items["Release checks"]["scope"] == ["Whole workspace"]
    assert items["Private one"]["scope"] == ["Not shared: only you, as owner, can see it"]
    assert items["Project one"]["scope"] == ["Project work"]
    assert "Secret body line" not in raw.decode()
    assert "markdown" not in items["Release checks"]
    assert {first, plain, bare} <= {row["id"] for row in items.values()}


def test_a_member_sees_only_skills_shared_with_them(hub):
    shared = skill(hub, "Shared", "Shared with everyone.\n")
    hidden = skill(hub, "Hidden", "Owner only text.\n")
    assert hub.request(hub.owner, "DELETE", f"/api/skills/{hidden}/shares")[0] == 200
    body, raw = status(hub, hub.member)
    assert body["viewer"] == {"role": "member"}
    assert [row["name"] for row in body["skills"]["items"]] == ["Shared"]
    assert body["skills"]["items"][0]["scope"] == ["Shared with you"]
    assert "Hidden" not in raw.decode() and shared
    assert_explained(body)


def test_other_workspaces_skills_do_not_leak(hub):
    skill(hub, "Ours", "Ours.\n")
    body, raw = status(hub, hub.other)
    assert body["skills"]["state"] == "empty"
    assert "Ours" not in raw.decode()


def test_bridge_is_owner_only_with_last_seen_and_no_credentials(hub):
    code, body, _ = hub.post(
        hub.owner,
        "/api/bridge/installs",
        {
            "platform": "fake",
            "ext_team": "T-ONE",
            "label": "Team chat",
            "secrets": {"token": SENTINEL, "signing_secret": SIGNING},
        },
    )
    assert code == 201, body
    page, raw = status(hub, hub.owner)
    bridge = page["bridge"]
    assert bridge["state"] == "ready"
    (install,) = bridge["installs"]
    assert install["platform"] == "fake" and install["label"] == "Team chat"
    assert install["enabled"] is False and install["reason"]
    assert install["last_seen_at"] is None and "no event" in install["last_seen_text"].lower()
    assert "not proof" in bridge["last_seen_note"].lower()
    assert SENTINEL not in raw.decode() and SIGNING not in raw.decode()
    assert "last_intake" not in raw.decode()
    # A member never learns whether a bridge exists.
    seen, raw = status(hub, hub.member)
    assert seen["bridge"]["state"] == "restricted"
    assert "owner" in seen["bridge"]["reason"] and "T-ONE" not in raw.decode()
    assert "installs" not in seen["bridge"]


def test_meetings_off_on_and_member_view(hub):
    off, _ = status(hub, hub.member)
    assert off["meetings"]["state"] == "off" or off["meetings"]["state"] == "restricted"
    assert off["meetings"]["reason"]
    code, body, _ = hub.request(hub.owner, "PUT", "/api/meetings/settings", {"enabled": True})
    assert code == 200, body
    owner, _ = status(hub, hub.owner)
    assert owner["meetings"]["state"] == "empty"
    assert "no meetings" in owner["meetings"]["reason"].lower()
    assert owner["meetings"]["overview"] == {
        "states": {},
        "meetings": 0,
        "connections": 0,
        "connections_enabled": 0,
    }
    mine = meeting_store.open_meetings(hub.hub.gateway, hub.hub.workspace)
    assert mine.enabled()
    again, _ = status(hub, hub.member)
    assert again["meetings"]["state"] in ("empty", "ready")
    assert "overview" not in again["meetings"]
    assert_explained(again)


def test_a_slow_section_failure_does_not_blank_the_page(hub, monkeypatch):
    def broken(ctx, args):
        raise RuntimeError("sentinel-internal-detail")

    monkeypatch.setattr(costs_api, "costs_summary", broken)
    body, raw = status(hub, hub.owner)
    assert body["costs"]["state"] == "error" and body["costs"]["reason"]
    assert "sentinel-internal-detail" not in raw.decode()
    assert body["skills"]["state"] == "empty"
    assert_explained(body)


def test_costs_state_period_scope_unknown_and_member_scope(signup_hub, owners):  # noqa: F811
    hub, owner = signup_hub, owners["A"]
    machine = _machine(hub, owner)
    _, job = _started(hub, owner, machine)
    assert _usage(hub, machine, job, cost_micros=123456, currency="USD")[0] == 200
    _, unknown_job = _started(hub, owner, machine)
    assert _usage(hub, machine, unknown_job, cost_unknown=True)[0] == 200

    code, body = http(hub.port, "GET", "/api/status", cookie=owner["cookie"])
    assert code == 200, body
    costs = body["costs"]
    assert costs["state"] == "ready" and costs["scope"] == "workspace"
    assert costs["totals"]["runs"] == 2
    assert costs["totals"]["cost"] == [{"currency": "USD", "micros": 123456, "display": "0.123456"}]
    assert costs["totals"]["unknown_cost_runs"] == 1
    assert "not counted as zero" in costs["unknown_text"]
    assert costs["by_project"] and costs["by_project"][0]["project"] == "demo"
    # The sums are exactly what the costs route reports; the page adds nothing.
    direct = http(hub.port, "GET", "/api/costs", cookie=owner["cookie"])[1]
    assert direct["workspace"]["cost_micros"] == {"USD": 123456}
    assert direct["window"]["start"] == costs["window"]["start"]

    # A person who is not the owner reads only the runs of projects they can read.
    peer = member(hub, owner, owners["B"])
    code, theirs = http(hub.port, "GET", "/api/status", cookie=peer["cookie"])
    assert code == 200 and theirs["costs"]["scope"] == "readable"
    assert "can read" in theirs["costs"]["scope_text"]
    # A token, even a workspace one, reads no status page.
    _, token = cli_token(owner, "reader", profile="standard", read_wings="shared")
    assert http(hub.port, "GET", "/api/status", token=token)[0] == 403
    assert json.dumps(body)  # serialisable
