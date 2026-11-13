"""Chat-platform bridge, foundation slice: installs, sealed credentials, owner routes, the kill
switch and ``bridge_status``, over real HTTP with an in-memory fake platform."""

import json
import logging
import os
import sqlite3
from pathlib import Path

import pytest

from commonplace.chat_bridge import adapter, cli as bridge_cli, index, sealing, service, store
from tests._commonplace_helpers import Hub, InProcessLauncher
from tests._console_helpers import ConsoleHub
from tests._mcp_server_helpers import _patch_mcp_server
from tests.test_commonplace_boards import mcp

SENTINEL = "xoxb-SENTINEL-0123456789-ABCDEF"
SIGNING = "SENTINEL-signing-secret-9f8e7d"


class FakeAdapter(adapter.Adapter):
    platform = "fake"
    secret_fields = ("token", "signing_secret")

    def __init__(self):
        self.result = adapter.TestResult(True)
        self.seen = []
        self.boom = None

    def test(self, install):
        self.seen.append(install)
        if self.boom:
            raise RuntimeError(self.boom)
        return self.result


class BridgeHub(ConsoleHub):
    def installs(self, auth=None):
        status, body, _ = self.get(auth or self.owner, "/api/bridge/installs")
        assert status == 200, (status, body)
        return body["installs"]

    def create(self, auth=None, team="T-ONE", **extra):
        body = {
            "platform": "fake",
            "ext_team": team,
            "label": "Team chat",
            "secrets": {"token": SENTINEL, "signing_secret": SIGNING},
            **extra,
        }
        return self.post(auth or self.owner, "/api/bridge/installs", body)

    def file(self, workspace=None):
        return Path(self.hub.supervisor.workspace_dir(workspace or self.hub.workspace)) / (
            "chat_bridge.sqlite3"
        )


@pytest.fixture(name="bridge")
def bridge_fixture(tmp_path, monkeypatch, config, kg):
    monkeypatch.setenv("COMMONPLACE_RATE_LIMITS", "off")
    _patch_mcp_server(monkeypatch, config, kg)
    keys = tmp_path / "keys"
    keys.mkdir()
    key_file = keys / "wrap.key"
    key_file.write_bytes(os.urandom(32))
    key_file.chmod(0o600)
    monkeypatch.setenv(sealing.KEY_FILE_ENV, str(key_file))
    hub = Hub(tmp_path / "data", InProcessLauncher(monkeypatch))
    try:
        yield BridgeHub(hub)
    finally:
        hub.close()


@pytest.fixture(name="fake")
def fake_fixture(monkeypatch):
    platform = FakeAdapter()
    monkeypatch.setitem(adapter.ADAPTERS, "fake", platform)
    return platform


def audit_rows(hub):
    rows = hub.db.audit_entries(None, 500)
    return [dict(r) for r in reversed(rows) if r["action"] == service.AUDIT]


def everything_on_disk(hub):
    data = Path(hub.hub.supervisor.root)
    blobs = []
    for path in data.rglob("*"):
        if path.is_file() and (path.name.startswith(("chat_bridge.sqlite3", "control.sqlite3"))):
            blobs.append(path.read_bytes())
    return b"".join(blobs)


# --- default off: nothing exists until an owner creates an install -----------------------------


def test_no_install_means_nothing_answers_and_nothing_is_created(bridge):
    hub = bridge
    ghost = "0" * 32
    routes = [
        ("GET", "/api/bridge/installs"),
        ("POST", "/api/bridge/installs"),
        ("PATCH", f"/api/bridge/installs/{ghost}"),
        ("DELETE", f"/api/bridge/installs/{ghost}"),
        ("POST", f"/api/bridge/installs/{ghost}/test"),
        ("GET", "/api/bridge/links"),
    ]
    for caller in (hub.member, hub.t1, hub.tm):
        for method, path in routes:
            status, body, _ = hub.request(caller, method, path, {} if method != "GET" else None)
            assert (status, body["code"]) == (404, "not_found"), (caller, method, path)
    # The intake is public (no session) and a 404 for any caller and any install id.
    for method, path in (
        ("POST", f"/api/bridge/in/{ghost}/events"),
        ("POST", f"/api/bridge/in/{ghost}/interact"),
        ("GET", f"/api/bridge/in/{ghost}/oauth"),
    ):
        for caller in (None, hub.owner, hub.t1):
            status, body, _ = hub.request(caller, method, path, {"type": "event"})
            assert (status, body["code"]) == (404, "not_found"), (caller, method, path)
    # The owner sees an empty list; a foreign id is 404 for them too. Nothing was written.
    assert hub.installs() == []
    for method, path in routes[2:5]:
        status, body, _ = hub.request(hub.owner, method, path, {"label": "x"})
        assert (status, body["code"]) == (404, "not_found")
    assert not hub.file().exists()
    assert audit_rows(hub) == []
    assert index.workspaces(hub.db) == []


def test_without_an_adapter_an_install_cannot_be_made(bridge):
    status, body, _ = bridge.create()
    assert (status, body["code"]) == (400, "unsupported_platform")
    assert not bridge.file().exists()
    assert index.workspaces(bridge.db) == []


def test_new_install_is_off_untested_and_has_the_default_cap(bridge, fake):
    status, body, _ = bridge.create()
    assert status == 201
    view = body["install"]
    assert (view["enabled"], view["tested_at"], view["daily_cap"]) == (False, None, 200)
    assert view["installed_by"] == bridge.hub.admin
    assert not ({"secrets", "token", "signing_secret"} & set(view))
    assert bridge.installs() == [view]


# --- secrets are write-only ---------------------------------------------------------------------


def test_secrets_never_appear_in_any_response_log_audit_row_or_file(bridge, fake, caplog):
    caplog.set_level(logging.DEBUG)
    hub = bridge
    seen = []

    def keep(result):
        status, body, raw = result
        seen.append(raw)
        return status, body

    status, body = keep(hub.create())
    assert status == 201
    install = body["install"]["id"]
    seen.append(json.dumps(hub.installs()).encode())
    keep(hub.get(hub.owner, "/api/bridge/installs"))
    assert keep(hub.post(hub.owner, f"/api/bridge/installs/{install}/test", {}))[0] == 200
    assert (
        keep(hub.request(hub.owner, "PATCH", f"/api/bridge/installs/{install}", {"enabled": True}))[
            0
        ]
        == 200
    )
    rotated = {"token": SENTINEL + "-two", "signing_secret": SIGNING + "-two"}
    assert (
        keep(
            hub.request(hub.owner, "PATCH", f"/api/bridge/installs/{install}", {"secrets": rotated})
        )[0]
        == 200
    )
    # A rejected request echoes nothing either.
    keep(
        hub.request(
            hub.owner, "PATCH", f"/api/bridge/installs/{install}", {"secrets": {"token": SENTINEL}}
        )
    )
    keep(hub.create(team="T-TWO", extra_field=SENTINEL))
    assert (
        keep(
            hub.post(
                hub.owner,
                "/api/bridge/installs",
                {
                    "platform": "fake",
                    "ext_team": "T-ONE",
                    "label": "x",
                    "secrets": {"token": SENTINEL, "signing_secret": SIGNING},
                },
            )
        )[0]
        == 409
    )
    # A tool call by an owner agent.
    ok, text = mcp(hub, hub.hub.owner_token("agent-owner"), "bridge_status")
    assert ok
    seen.append(text.encode())
    # A test whose adapter fails with the secret in its own error text.
    fake.boom = f"platform said: bad token {SENTINEL}"
    status, body = keep(hub.post(hub.owner, f"/api/bridge/installs/{install}/test", {}))
    assert (status, body["ok"], body["code"]) == (200, False, "failed")

    needles = (SENTINEL.encode(), SIGNING.encode(), b"SENTINEL")
    for raw in seen:
        assert not any(n in raw for n in needles), raw
    for row in audit_rows(hub):
        assert not any(n.decode() in json.dumps(row) for n in needles)
    assert not any("SENTINEL" in record.getMessage() for record in caplog.records)
    assert not any(n in everything_on_disk(hub) for n in needles)
    assert audit_rows(hub)  # the leak check above had rows to look at


def test_the_adapter_receives_the_unsealed_secrets_and_repr_hides_them(bridge, fake):
    install = bridge.create()[1]["install"]["id"]
    bridge.post(bridge.owner, f"/api/bridge/installs/{install}/test", {})
    (ref,) = fake.seen
    assert ref.secrets == {"token": SENTINEL, "signing_secret": SIGNING}
    assert ref.id == install and ref.ext_team == "T-ONE"
    assert SENTINEL not in repr(ref)


def test_a_sealed_blob_is_bound_to_its_install_and_key(tmp_path):
    key = os.urandom(32)
    blob = sealing.seal(key, "install-a", {"token": SENTINEL})
    assert SENTINEL.encode() not in blob
    assert sealing.open_blob(key, "install-a", blob) == {"token": SENTINEL}
    for wrong in (
        lambda: sealing.open_blob(key, "install-b", blob),
        lambda: sealing.open_blob(os.urandom(32), "install-a", blob),
        lambda: sealing.open_blob(key, "install-a", blob[:-1] + bytes([blob[-1] ^ 1])),
        lambda: sealing.open_blob(key, "install-a", b""),
    ):
        with pytest.raises(sealing.Unavailable):
            wrong()


def test_keys_differ_per_workspace_and_a_missing_or_unsafe_key_refuses(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    key = tmp_path / "wrap.key"
    key.write_bytes(os.urandom(32))
    key.chmod(0o600)
    monkeypatch.setenv(sealing.KEY_FILE_ENV, str(key))
    assert sealing.key_for(data, "ws-1") != sealing.key_for(data, "ws-2")
    assert sealing.key_for(data, "ws-1") == sealing.key_for(data, "ws-1")
    key.chmod(0o644)  # readable by others
    with pytest.raises(sealing.Unavailable):
        sealing.key_for(data, "ws-1")
    key.chmod(0o600)
    inside = data / "wrap.key"
    inside.write_bytes(key.read_bytes())
    inside.chmod(0o600)
    monkeypatch.setenv(sealing.KEY_FILE_ENV, str(inside))  # inside the data tree
    with pytest.raises(sealing.Unavailable):
        sealing.key_for(data, "ws-1")
    monkeypatch.delenv(sealing.KEY_FILE_ENV)
    with pytest.raises(sealing.Unavailable):
        sealing.key_for(data, "ws-1")


def test_without_a_sealing_key_an_install_is_refused_not_stored_in_the_clear(
    bridge, fake, monkeypatch
):
    monkeypatch.delenv(sealing.KEY_FILE_ENV)
    status, body, _ = bridge.create()
    assert (status, body["code"]) == (503, "sealing_unavailable")
    assert not bridge.file().exists()
    assert index.workspaces(bridge.db) == []
    assert audit_rows(bridge)[-1]["reason"] == "sealing_unavailable"


# --- enabling needs a passing test --------------------------------------------------------------


def test_a_never_tested_install_cannot_be_enabled(bridge, fake):
    install = bridge.create()[1]["install"]["id"]
    path = f"/api/bridge/installs/{install}"
    status, body, _ = bridge.request(bridge.owner, "PATCH", path, {"enabled": True})
    assert (status, body["code"]) == (409, "not_tested")
    assert bridge.installs()[0]["enabled"] is False
    # A failed test does not count.
    fake.result = adapter.TestResult(False, "credentials")
    status, body, _ = bridge.post(bridge.owner, path + "/test", {})
    assert (status, body["ok"], body["code"]) == (200, False, "credentials")
    assert body["text"] == adapter.TEST_TEXT["credentials"]
    status, body, _ = bridge.request(bridge.owner, "PATCH", path, {"enabled": True})
    assert (status, body["code"]) == (409, "not_tested")
    # A passing one does.
    fake.result = adapter.TestResult(True)
    status, body, _ = bridge.post(bridge.owner, path + "/test", {})
    assert (status, body["ok"]) == (200, True)
    status, body, _ = bridge.request(bridge.owner, "PATCH", path, {"enabled": True})
    assert (status, body["install"]["enabled"]) == (200, True)
    assert body["install"]["tested_at"]
    status, body, _ = bridge.request(bridge.owner, "PATCH", path, {"enabled": False})
    assert (status, body["install"]["enabled"]) == (200, False)
    assert body["install"]["disabled_at"]


def test_an_adapter_answer_outside_the_known_codes_is_a_plain_failure(bridge, fake):
    install = bridge.create()[1]["install"]["id"]
    for answer in (
        adapter.TestResult(True, "credentials"),
        adapter.TestResult(False, "A platform sentence " + SENTINEL),
        "not a result",
    ):
        fake.result = answer
        status, body, _ = bridge.post(bridge.owner, f"/api/bridge/installs/{install}/test", {})
        assert (status, body["ok"]) == (200, False)
        assert body["code"] in adapter.TEST_CODES + ("failed",)
        assert SENTINEL not in json.dumps(body)
    assert bridge.installs()[0]["tested_at"] is None


def test_new_credentials_switch_the_install_off_and_need_a_new_test(bridge, fake):
    install = bridge.create()[1]["install"]["id"]
    path = f"/api/bridge/installs/{install}"
    bridge.post(bridge.owner, path + "/test", {})
    bridge.request(bridge.owner, "PATCH", path, {"enabled": True})
    before = store.open_bridge(bridge.hub.gateway, bridge.hub.workspace).secrets(install)
    status, body, _ = bridge.request(
        bridge.owner,
        "PATCH",
        path,
        {"secrets": {"token": "new-token", "signing_secret": "new-sign"}},
    )
    assert status == 200
    assert (body["install"]["enabled"], body["install"]["tested_at"]) == (False, None)
    assert store.open_bridge(bridge.hub.gateway, bridge.hub.workspace).secrets(install) != before
    status, body, _ = bridge.request(bridge.owner, "PATCH", path, {"enabled": True})
    assert (status, body["code"]) == (409, "not_tested")
    bridge.post(bridge.owner, path + "/test", {})
    assert fake.seen[-1].secrets == {"token": "new-token", "signing_secret": "new-sign"}


# --- one platform team, one install per hub -----------------------------------------------------


def test_a_platform_team_is_installed_once_per_hub(bridge, fake):
    first = bridge.create()[1]["install"]["id"]
    status, body, _ = bridge.create(team="T-ONE")
    assert (status, body["code"]) == (409, "team_taken")
    # Another workspace's owner cannot install the same team either, and learns nothing more.
    status, body, _ = bridge.create(auth=bridge.other, team="T-ONE")
    assert (status, body["code"]) == (409, "team_taken")
    assert not bridge.file(bridge.other_workspace).exists()
    assert bridge.create(team="T-TWO")[0] == 201
    assert bridge.create(auth=bridge.other, team="T-THREE")[0] == 201
    # An install is only visible to its own workspace's owner.
    assert {i["ext_team"] for i in bridge.installs()} == {"T-ONE", "T-TWO"}
    assert {i["ext_team"] for i in bridge.installs(bridge.other)} == {"T-THREE"}
    path = f"/api/bridge/installs/{first}"
    for method, suffix in (("PATCH", ""), ("DELETE", ""), ("POST", "/test")):
        status, body, _ = bridge.request(bridge.other, method, path + suffix, {"label": "x"})
        assert (status, body["code"]) == (404, "not_found")
    # Deleting frees the team.
    assert bridge.request(bridge.owner, "DELETE", path)[0] == 200
    assert bridge.create(auth=bridge.other, team="T-ONE")[0] == 201


def test_the_store_enforces_the_team_too(bridge, fake):
    bridge.create()
    db = sqlite3.connect(bridge.file())
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO bridge_installs (id,platform,ext_team,label,secrets,installed_by,created_at)"
            " VALUES ('x','fake','T-ONE','l',x'00','u','now')"
        )
    db.close()


# --- who may do what ---------------------------------------------------------------------------


def test_only_the_signed_in_owner_reaches_the_install_routes(bridge, fake):
    install = bridge.create()[1]["install"]["id"]
    path = f"/api/bridge/installs/{install}"
    for caller in (bridge.member, bridge.t1, bridge.tm, bridge.hub.owner_token("owner-agent")):
        for method, target in (
            ("GET", "/api/bridge/installs"),
            ("POST", "/api/bridge/installs"),
            ("PATCH", path),
            ("DELETE", path),
            ("POST", path + "/test"),
        ):
            status, body, _ = bridge.request(caller, method, target, {"label": "x"})
            assert (status, body["code"]) == (404, "not_found"), (method, target)
    assert bridge.request(None, "GET", "/api/bridge/installs")[0] == 401
    assert bridge.installs()[0]["label"] == "Team chat"


def test_unknown_bridge_paths_are_404_for_the_owner(bridge, fake):
    install = bridge.create()[1]["install"]["id"]
    for method, target in (
        ("GET", "/api/bridge/links"),
        ("GET", f"/api/bridge/installs/{install}"),
        ("PUT", "/api/bridge/installs"),
        ("POST", f"/api/bridge/installs/{install}/test/extra"),
        ("GET", f"/api/bridge/installs/{install}/channels/C1"),
    ):
        status, body, _ = bridge.request(bridge.owner, method, target, {})
        assert status == 404, (method, target)


def test_intake_answers_404_even_for_an_existing_install(bridge, fake):
    install = bridge.create()[1]["install"]["id"]
    for method, tail in (("POST", "events"), ("POST", "interact"), ("GET", "oauth")):
        status, body, _ = bridge.request(None, method, f"/api/bridge/in/{install}/{tail}", {"x": 1})
        assert (status, body["code"]) == (404, "not_found")


def test_input_is_validated(bridge, fake):
    bad = [
        {"platform": "fake"},  # (label dropped below: a missing field)
        {"ext_team": "bad team!"},
        {"ext_team": "T" * 200},
        {"label": ""},
        {"label": "a\nb"},
        {"label": "x" * 81},
        {"daily_cap": 0},
        {"daily_cap": store.DAILY_CAP_MAX + 1},
        {"daily_cap": True},
        {"daily_cap": "5"},
        {"secrets": {"token": SENTINEL}},
        {"secrets": {"token": SENTINEL, "signing_secret": SIGNING, "extra": "x"}},
        {"secrets": {"token": "", "signing_secret": SIGNING}},
        {"secrets": {"token": 5, "signing_secret": SIGNING}},
        {"secrets": {"token": "a\x00b", "signing_secret": SIGNING}},
        {"secrets": "string"},
        {"unknown": 1},
    ]
    for change in bad:
        body = {
            "platform": "fake",
            "ext_team": "T-ONE",
            "label": "Team chat",
            "secrets": {"token": SENTINEL, "signing_secret": SIGNING},
            **change,
        }
        if change == {"platform": "fake"}:
            body.pop("label")
        status, _, raw = bridge.post(bridge.owner, "/api/bridge/installs", body)
        assert status == 400, (change, status, raw)
        assert SENTINEL not in raw.decode()
    assert bridge.installs() == []
    install = bridge.create()[1]["install"]["id"]
    path = f"/api/bridge/installs/{install}"
    for change in (
        {"enabled": "yes"},
        {"label": ""},
        {"daily_cap": -1},
        {"platform": "x"},
        {"secrets": {}},
    ):
        assert bridge.request(bridge.owner, "PATCH", path, change)[0] == 400, change
    status, body, _ = bridge.request(
        bridge.owner, "PATCH", path, {"label": " New name ", "daily_cap": 7}
    )
    assert (body["install"]["label"], body["install"]["daily_cap"]) == ("New name", 7)


def test_delete_removes_the_install_and_everything_of_it(bridge, fake):
    install = bridge.create()[1]["install"]["id"]
    db = sqlite3.connect(bridge.file())
    for sql in (
        "INSERT INTO bridge_channels VALUES (?, 'C1','work','titles',1,'u','t')",
        "INSERT INTO bridge_links (id,install_id,ext_user,user_id,created_at) VALUES ('l',?,'U1','u','t')",
        "INSERT INTO bridge_events VALUES (?, 'e1','received',NULL,NULL,NULL,NULL,NULL,'t','t')",
        "INSERT INTO bridge_outbox (id,install_id,ext_event,part,target,ref,state) VALUES ('o',?,'e1',0,'thread','r','queued')",
        "INSERT INTO bridge_prompts (pending,install_id,ext_user,ext_channel) VALUES ('p',?,'U1','C1')",
    ):
        db.execute(sql, (install,))
    db.commit()
    db.close()
    assert bridge.request(bridge.owner, "DELETE", f"/api/bridge/installs/{install}")[0] == 200
    assert bridge.installs() == []
    db = sqlite3.connect(bridge.file())
    for table in (
        "bridge_installs",
        "bridge_channels",
        "bridge_links",
        "bridge_events",
        "bridge_outbox",
        "bridge_prompts",
    ):
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    db.close()
    assert index.workspace_of(bridge.db, install) is None


# --- audit --------------------------------------------------------------------------------------


def test_audit_rows_carry_ids_and_fixed_codes_only(bridge, fake):
    install = bridge.create()[1]["install"]["id"]
    path = f"/api/bridge/installs/{install}"
    bridge.request(bridge.owner, "PATCH", path, {"enabled": True})  # refused: not tested
    bridge.post(bridge.owner, path + "/test", {})
    bridge.request(bridge.owner, "PATCH", path, {"enabled": True, "label": "Renamed team"})
    bridge.request(bridge.owner, "PATCH", path, {"enabled": False})
    bridge.request(bridge.owner, "DELETE", path)
    rows = audit_rows(bridge)
    assert [(r["tool"], r["decision"], r["reason"]) for r in rows] == [
        ("create", "ok", ""),
        ("enable", "refused", "not_tested"),
        ("test", "ok", "ok"),
        ("update", "ok", ""),
        ("enable", "ok", ""),
        ("disable", "ok", ""),
        ("delete", "ok", ""),
    ]
    assert {r["workspace_id"] for r in rows} == {bridge.hub.workspace}
    assert all(r["user_id"] == bridge.hub.admin for r in rows)
    assert all(json.loads(r["target_ids"]) == [install] for r in rows)
    assert "Renamed team" not in json.dumps(rows) and "Team chat" not in json.dumps(rows)


# --- the kill switch and status -----------------------------------------------------------------


def _enabled_install(bridge, team, auth=None):
    install = bridge.create(auth=auth, team=team)[1]["install"]["id"]
    path = f"/api/bridge/installs/{install}"
    auth = auth or bridge.owner
    bridge.post(auth, path + "/test", {})
    assert bridge.request(auth, "PATCH", path, {"enabled": True})[0] == 200
    return install


def test_the_kill_switch_turns_installs_off_and_cancels_queued_posts(bridge, fake):
    one = _enabled_install(bridge, "T-ONE")
    two = _enabled_install(bridge, "T-TWO", bridge.other)
    db = sqlite3.connect(bridge.file())
    db.execute(
        "INSERT INTO bridge_outbox (id,install_id,ext_event,part,target,ref,state)"
        " VALUES ('o1',?,'e1',0,'thread','r','queued'), ('o2',?,'e1',1,'thread','r','sent')",
        (one, one),
    )
    db.execute(
        "INSERT INTO bridge_prompts (pending,install_id,ext_user,ext_channel) VALUES ('p',?,'U','C')",
        (one,),
    )
    db.commit()
    db.close()
    # One workspace first.
    done = service.kill_switch(bridge.db, bridge.hub.supervisor, [bridge.hub.workspace])
    assert done == [one]
    assert [i["enabled"] for i in bridge.installs()] == [False]
    assert bridge.installs(bridge.other)[0]["enabled"] is True
    db = sqlite3.connect(bridge.file())
    assert dict(db.execute("SELECT id,state FROM bridge_outbox")) == {
        "o1": "cancelled",
        "o2": "sent",
    }
    assert db.execute("SELECT COUNT(*) FROM bridge_prompts").fetchone()[0] == 0
    db.close()
    # Then everything, through the command.
    args = type("A", (), {"all": True, "workspace": None})()
    assert bridge_cli.cmd_disable(args, bridge.db, Path(bridge.hub.supervisor.root).parent) == 0
    assert [i["enabled"] for i in bridge.installs(bridge.other)] == [False]
    assert two in {r["target_ids"] and json.loads(r["target_ids"])[0] for r in audit_rows(bridge)}
    # A disabled install cannot be turned straight back on by anything but the owner's route
    # (its test stays recorded): turning it on again works exactly as before.
    path = f"/api/bridge/installs/{one}"
    assert bridge.request(bridge.owner, "PATCH", path, {"enabled": True})[0] == 200


def test_the_kill_switch_creates_nothing_on_a_clean_hub(bridge, fake):
    assert service.kill_switch(bridge.db, bridge.hub.supervisor, [bridge.hub.workspace]) == []
    assert not bridge.file().exists()
    assert audit_rows(bridge) == []


def test_the_command_line_status_and_disable(tmp_path, capsys):
    from commonplace import cli

    data = tmp_path / "cli-data"
    assert cli.main(["--data", str(data), "init", "--admin-email", "admin@example.com"]) == 0
    capsys.readouterr()
    assert cli.main(["--data", str(data), "bridge", "status"]) == 0
    assert "No chat-platform installs." in capsys.readouterr().out
    assert cli.main(["--data", str(data), "bridge", "disable"]) == 2  # names nothing
    assert "--all" in capsys.readouterr().err
    assert cli.main(["--data", str(data), "bridge", "disable", "--all"]) == 0
    assert "Nothing was on." in capsys.readouterr().out
    assert not list(data.rglob("chat_bridge.sqlite3"))
    for action in ("status", "disable"):
        with pytest.raises(SystemExit):  # credentials are never accepted on a command line
            cli.main(["--data", str(data), "bridge", action, "--token", "x"])


def test_status_shows_installs_without_credentials(bridge, fake, capsys):
    install = _enabled_install(bridge, "T-ONE")
    db = sqlite3.connect(bridge.file())
    db.execute("INSERT INTO bridge_channels VALUES (?, 'C1','work','titles',1,'u','t')", (install,))
    db.execute("INSERT INTO bridge_channels VALUES (?, 'C2','work','titles',0,'u','t')", (install,))
    db.execute(
        "INSERT INTO bridge_events VALUES (?, 'e1','replied',NULL,NULL,NULL,NULL,NULL,'2026-11-01T10:00:00Z','t')",
        (install,),
    )
    db.commit()
    db.close()
    args = type("A", (), {"all": False, "workspace": None})()
    assert bridge_cli.cmd_status(args, bridge.db, Path(bridge.hub.supervisor.root).parent) == 0
    out = capsys.readouterr().out
    assert install in out and "on" in out and "tested=yes" in out and "channels=1" in out
    assert "2026-11-01T10:00:00Z" in out
    assert "SENTINEL" not in out
    ok, text = mcp(bridge.hub, bridge.hub.owner_token("agent-owner"), "bridge_status")
    assert ok
    (row,) = json.loads(text)["installs"]
    assert (row["id"], row["platform"], row["enabled"], row["channels"], row["last_intake"]) == (
        install,
        "fake",
        True,
        1,
        "2026-11-01T10:00:00Z",
    )
    assert "secrets" not in row


def test_bridge_status_tool_is_read_only_and_owner_only(bridge, fake):
    owner_agent = bridge.hub.owner_token("agent-owner")
    assert "bridge_status" in bridge.hub.tool_names(owner_agent)
    assert "bridge_status" not in bridge.hub.tool_names(bridge.tm)  # a member's agent
    ok, text = mcp(bridge.hub, owner_agent, "bridge_status")
    assert ok and json.loads(text) == {"installs": []}
    assert not bridge.file().exists()
    ok, text = mcp(bridge.hub, bridge.tm, "bridge_status")
    assert not ok
    names = [t for t in bridge.hub.tool_names(owner_agent) if t.startswith("bridge")]
    assert names == ["bridge_status"]  # nothing that installs, maps, links or reads secrets


def test_a_workspace_without_the_file_is_never_given_one_by_a_read(bridge, fake):
    store.open_bridge(bridge.hub.gateway, bridge.hub.workspace).status()
    store.open_bridge(bridge.hub.gateway, bridge.hub.workspace).installs()
    with pytest.raises(store.Missing):
        store.open_bridge(bridge.hub.gateway, bridge.hub.workspace).get("nope")
    assert not bridge.file().exists()


# --- account export ----------------------------------------------------------------------------


def test_the_account_export_keeps_sealed_credentials_out(bridge, fake):
    import io
    import zipfile

    from commonplace.account import export

    install = bridge.create()[1]["install"]["id"]
    sealed = store.open_bridge(bridge.hub.gateway, bridge.hub.workspace).secrets(install)
    planned = export.plan(bridge.hub.gateway, bridge.hub.admin)
    out = io.BytesIO()
    manifest = export.write(bridge.hub.gateway, planned, out)
    assert "bridge_install_index" not in manifest["unclassified_tables"]
    archive = zipfile.ZipFile(io.BytesIO(out.getvalue()))
    prefix = f"workspaces/{bridge.hub.workspace}"
    (row,) = [
        json.loads(line)
        for line in archive.read(
            f"{prefix}/stores/chat_bridge.sqlite3/bridge_installs.ndjson"
        ).splitlines()
    ]
    assert row["id"] == install and row["secrets"] == "[redacted]"
    assert f"{prefix}/control/bridge_install_index.ndjson" in archive.namelist()
    blob = b"".join(archive.read(name) for name in archive.namelist())
    assert sealed not in blob and SENTINEL.encode() not in blob
