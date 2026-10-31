"""Hosted plans through the real gateway and account usage meters."""

import json
import logging
import os
import shutil
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from commonplace import plans
from commonplace.cli import main
from commonplace.control_db import ControlError
from commonplace.social import accounts
from commonplace.social.providers import Profile
from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.test_commonplace_boards import api
from tests.test_commonplace_oauth import Client as OAuthClient
from tests.test_commonplace_web import login, request

STORAGE_MESSAGE = (
    "Je opslag is vol: het gratis plan heeft 250 MB. Verwijder iets om weer ruimte te maken. / "
    "Your storage is full: the free plan includes 250 MB. Delete something to make room."
)
VOICE_MESSAGE = (
    "Je hebt de 10 minuten spraak-naar-tekst van vandaag gebruikt. "
    "Morgen (00:00 UTC) kan het weer. / "
    "You've used today's 10 minutes of voice transcription. It resets tomorrow at 00:00 UTC."
)


@pytest.fixture(autouse=True)
def plans_off(monkeypatch):
    monkeypatch.delenv("COMMONPLACE_PLANS", raising=False)
    monkeypatch.setattr(plans, "_warned_invalid", False)


@pytest.fixture
def clock(hub):
    clock = SimpleNamespace(now=0.0)
    hub.gateway.storage_meter = plans.StorageMeter(clock=lambda: clock.now)
    return clock


def assign(hub, name="free"):
    plans.set_plan(hub.db, hub.admin, name, actor=hub.admin)


def fill(hub, size=None, workspace=None):
    directory = Path(hub.gateway.workspace_dir(workspace or hub.workspace))
    with (directory / "data.bin").open("wb") as file:
        file.truncate(plans.FREE.storage_bytes if size is None else size)
    return directory


def audits(hub, action):
    return [row for row in hub.db.audit_entries(limit=1000) if row["action"] == action]


def tool(hub, token, name, **arguments):
    return hub.rpc(token, "tools/call", {"name": name, "arguments": arguments})


@pytest.mark.parametrize("switch", [None, "", "off"])
def test_off_has_no_assignment_enforcement_or_plan_payload(hub, monkeypatch, switch):
    if switch is not None:
        monkeypatch.setenv("COMMONPLACE_PLANS", switch)
    plans.assign_signup_plan(hub.db, hub.admin, hub.workspace, via="google")
    assert plans.get_plan(hub.db, hub.admin) is None
    assert not audits(hub, "plan.set")
    assign(hub)
    fill(hub)
    token = hub.owner_token()
    result = tool(hub, token, "board_add", slug="work", wing="shared")
    assert not result["result"].get("isError")
    assert api(hub, token, "GET", "/api/plan") == (200, {"enabled": False})
    assert not audits(hub, "plan.limit")


def test_invalid_switch_warns_once_and_stays_off(hub, monkeypatch, caplog):
    monkeypatch.setenv("COMMONPLACE_PLANS", "invalid")
    with caplog.at_level(logging.WARNING, logger="commonplace.plans"):
        for _ in range(3):
            assert not plans.enabled()
            plans.assign_signup_plan(hub.db, hub.admin, hub.workspace, via="google")
    assert len(caplog.records) == 1
    assert "COMMONPLACE_PLANS" in caplog.text and "off" in caplog.text
    assert plans.get_plan(hub.db, hub.admin) is None


def test_signup_assigns_once_and_never_overwrites(hub, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    accounts.ensure_schema(hub.db)
    profile = Profile("signup-subject", "", ("signup+" + hub.db.user(hub.admin)["email"],))
    uid = accounts.resolve(hub.db, "google", profile, signup=True)
    row = hub.db._conn.execute("SELECT * FROM account_plans WHERE user_id = ?", (uid,)).fetchone()
    assert row["plan"] == "free" and row["set_by"] == "signup:google" and row["set_at"]
    workspace = next(ws["id"] for ws in hub.db.workspaces() if ws["personal_of"] == uid)
    plans.assign_signup_plan(hub.db, uid, workspace, via="github")
    assert len(audits(hub, "plan.set")) == 1
    entry = audits(hub, "plan.set")[0]
    assert entry["reason"] == "free" and entry["workspace_id"] == workspace
    plans.set_plan(hub.db, uid, "paid", actor=hub.admin)
    plans.assign_signup_plan(hub.db, uid, workspace, via="google")
    assert accounts.resolve(hub.db, "google", profile, signup=True) == uid
    assert plans.get_plan(hub.db, uid) == plans.PAID
    assert len(audits(hub, "plan.set")) == 2


def test_plan_schema_rejects_unknown_plans_and_users(hub):
    with pytest.raises(sqlite3.IntegrityError):
        hub.db._conn.execute(
            "INSERT INTO account_plans VALUES (?, 'other', 'admin', 'now')", (hub.admin,)
        )
    with pytest.raises(sqlite3.IntegrityError):
        hub.db._conn.execute("INSERT INTO account_plans VALUES ('missing', 'free', 'admin', 'now')")
    with pytest.raises(ControlError):
        assign(hub, "other")


def test_under_storage_limit_allows_worker_local_and_rest_writes(hub, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    assign(hub)
    fill(hub, plans.FREE.storage_bytes - 1)
    token = hub.owner_token()
    result = tool(
        hub, token, "mempalace_add_drawer", wing="shared", room="r", content="exact words"
    )
    assert not result["result"].get("isError")
    assert not tool(hub, token, "board_add", slug="work", wing="shared")["result"].get("isError")
    assert api(hub, token, "POST", "/api/boards/work/cards", {"title": "Ready"})[0] == 201
    assert not audits(hub, "plan.limit")


@pytest.mark.parametrize("extra", [0, 1])
def test_full_storage_refuses_mcp_and_rest_with_exact_messages(hub, clock, monkeypatch, extra):
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    assign(hub)
    fill(hub, plans.FREE.storage_bytes + extra)
    token = hub.owner_token()
    for name in ("mempalace_add_drawer", "board_add"):
        response = tool(hub, token, name)
        assert response["result"] == {
            "content": [{"type": "text", "text": STORAGE_MESSAGE}],
            "isError": True,
        }
    assert api(hub, token, "POST", "/api/boards", {"slug": "blocked", "wing": "shared"}) == (
        507,
        {"error": STORAGE_MESSAGE, "limit": "storage"},
    )
    assert api(hub, token, "PATCH", "/api/cards/1", {})[0] == 507
    assert len(audits(hub, "plan.limit")) == 1
    entry = audits(hub, "plan.limit")[0]
    assert (entry["decision"], entry["reason"], entry["workspace_id"]) == (
        "deny",
        "storage",
        hub.workspace,
    )
    assert (
        Path(hub.gateway.workspace_dir(hub.workspace)) / "data.bin"
    ).stat().st_size == plans.FREE.storage_bytes + extra
    assert all(
        board["slug"] != "blocked" for board in api(hub, token, "GET", "/api/boards")[1]["boards"]
    )
    clock.now = 59.999
    tool(hub, token, "board_add")
    assert len(audits(hub, "plan.limit")) == 1
    clock.now = 60
    tool(hub, token, "board_add")
    assert len(audits(hub, "plan.limit")) == 2
    assert not hub.supervisor.launcher.started


def test_full_storage_allows_reads_worker_deletions_and_local_removals(hub, monkeypatch):
    token = hub.owner_token()
    drawer, _ = hub.call(
        token, "mempalace_add_drawer", wing="shared", room="r", content="remove me"
    )
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    assign(hub)
    fill(hub)
    result = tool(hub, token, "mempalace_delete_drawer", drawer_id=drawer["drawer_id"])
    assert not result["result"].get("isError")
    result = tool(hub, token, "webhook_remove", id="missing")
    assert STORAGE_MESSAGE not in json.dumps(result, ensure_ascii=False)
    assert api(hub, token, "DELETE", "/api/webhooks/missing")[0] != 507
    assert api(hub, token, "GET", "/api/boards")[0] == 200
    assert not tool(hub, token, "mempalace_list_wings")["result"].get("isError")
    assert "tools" in hub.rpc(token, "tools/list")["result"]
    assert not audits(hub, "plan.limit")


def test_full_storage_allows_logout_and_ends_session(hub, monkeypatch):
    cookie, _, _ = login(hub)
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    assign(hub)
    fill(hub, plans.FREE.storage_bytes + 1)
    status, _, _ = request(
        hub, "/app/logout", "POST", headers={**cookie, "Content-Type": "application/json"}
    )
    assert status == 204
    assert api(hub, cookie, "GET", "/api/me")[0] == 401
    assert not audits(hub, "plan.limit")


def test_full_storage_allows_oauth_consent(hub, monkeypatch):
    client = OAuthClient(hub)
    client_id = client.register(client_name="test")
    client.login(email=hub.db.user(hub.admin)["email"])
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    assign(hub)
    fill(hub, plans.FREE.storage_bytes + 1)
    assert client.consent(client_id)
    assert not audits(hub, "plan.limit")


def test_full_storage_allows_signed_in_provider_callback(hub, monkeypatch):
    cookie, _, _ = login(hub)
    monkeypatch.setitem(hub.gateway.sign_in.providers, "apple", SimpleNamespace(id="apple"))
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    assign(hub)
    fill(hub, plans.FREE.storage_bytes + 1)
    status, headers, _ = request(
        hub,
        "/app/auth/apple/callback",
        "POST",
        body="state=missing",
        headers={**cookie, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert status == 303
    assert "signin=expired" in headers["Location"]
    assert not audits(hub, "plan.limit")


def test_storage_cache_expires_and_deleted_files_make_room(hub, clock, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    assign(hub)
    path = fill(hub)
    token = hub.owner_token()
    assert tool(hub, token, "board_add")["result"]["isError"]
    (path / "data.bin").unlink()
    clock.now = 59.999
    assert tool(hub, token, "board_add")["result"]["isError"]
    clock.now = 60
    result = tool(hub, token, "board_add", slug="work", wing="shared")
    assert not result["result"].get("isError")


def test_storage_counts_regular_files_only_and_caches_per_workspace(tmp_path):
    clock = SimpleNamespace(now=0.0)
    meter = plans.StorageMeter(clock=lambda: clock.now)
    root = tmp_path / "workspace"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (root / "one").write_bytes(b"123")
    (nested / "two").write_bytes(b"45678")
    external = tmp_path / "models"
    external.mkdir()
    (external / "model").write_bytes(b"x" * 100)
    try:
        (root / "cache").symlink_to(external, target_is_directory=True)
        (root / "file-link").symlink_to(external / "model")
        (root / "broken").symlink_to(external / "missing")
        (nested / "cycle").symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    if hasattr(os, "mkfifo"):
        os.mkfifo(root / "pipe")
    assert meter.used("one", root) == 8
    (nested / "two").write_bytes(b"1")
    clock.now = 59.999
    assert meter.used("one", root) == 8
    assert meter.used("two", external) == 100
    clock.now = 60
    assert meter.used("one", root) == 4
    assert meter.used("missing", tmp_path / "missing") == 0
    assert meter.used("symlink", root / "cache") == 0


@pytest.mark.parametrize("name", ["paid", "none"])
def test_unlimited_accounts_are_not_storage_limited(hub, monkeypatch, name):
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    monkeypatch.setattr(plans, "load_ratio", lambda: 0)
    assign(hub, name)
    fill(hub, plans.FREE.storage_bytes + 1)
    result = tool(hub, hub.owner_token(), "board_add", slug="work", wing="shared")
    assert not result["result"].get("isError")


def test_shared_workspace_ignores_members_plan_and_usage(hub, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    assign(hub)
    shared = hub.db.create_workspace("shared", hub.admin)
    fill(hub, workspace=shared)
    token = hub.token(
        "shared",
        workspace=shared,
        profile="full",
        trust="trusted",
        read_wings=["*"],
        write_wings=["*"],
    )
    assert not tool(hub, token, "board_add", slug="work", wing="shared")["result"].get("isError")
    status, payload = api(hub, token, "GET", "/api/plan")
    assert status == 200 and payload["plan"] is None
    assert payload["storage"] == {"used": 0, "limit": None}
    assert payload["transcription"] == {"used_today": 0, "limit": None}
    assert payload["sync"] == {"interval_seconds": 0}


def test_transcription_boundaries_utc_reset_and_pruning(hub, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    assign(hub)
    now = SimpleNamespace(value=datetime(2026, 1, 1, 23, 59, 59, tzinfo=timezone.utc))
    monkeypatch.setattr(plans, "_now", lambda: now.value)
    assert plans.transcription_used(hub.db, hub.admin) == 0
    plans.use_transcription(hub.db, hub.admin, 599)
    plans.use_transcription(hub.db, hub.admin, 1)
    plans.use_transcription(hub.db, hub.admin, 0)
    assert plans.transcription_used(hub.db, hub.admin) == 600
    with pytest.raises(plans.LimitReached) as exc:
        plans.use_transcription(hub.db, hub.admin, 1)
    assert str(exc.value) == VOICE_MESSAGE
    assert plans.transcription_used(hub.db, hub.admin) == 600
    now.value += timedelta(seconds=1)
    assert plans.transcription_used(hub.db, hub.admin) == 0
    plans.use_transcription(hub.db, hub.admin, 15)
    now.value += timedelta(days=40)
    plans.use_transcription(hub.db, hub.admin, 1)
    days = [r[0] for r in hub.db._conn.execute("SELECT day FROM transcription_usage ORDER BY day")]
    assert days == ["2026-01-02", "2026-02-11"]


@pytest.mark.parametrize("seconds", [-1, 1.5, True, "1"])
def test_invalid_transcription_cannot_reduce_usage(hub, monkeypatch, seconds):
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    assign(hub)
    with pytest.raises(ValueError):
        plans.use_transcription(hub.db, hub.admin, seconds)
    assert plans.transcription_used(hub.db, hub.admin) == 0


def test_transcription_is_atomic_across_concurrent_clips(hub, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    assign(hub)

    def use(_):
        try:
            plans.use_transcription(hub.db, hub.admin, 100)
            return True
        except plans.LimitReached:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(use, range(20))) == 6
    assert plans.transcription_used(hub.db, hub.admin) == 600


@pytest.mark.parametrize("switch,name", [("off", "free"), ("on", "paid"), ("on", "none")])
def test_unlimited_transcription_records_nothing(hub, monkeypatch, switch, name):
    monkeypatch.setenv("COMMONPLACE_PLANS", switch)
    assign(hub, name)
    plans.use_transcription(hub.db, hub.admin, 10000)
    assert hub.db._conn.execute("SELECT * FROM transcription_usage").fetchall() == []


@pytest.mark.parametrize("switch", ["off", "on"])
@pytest.mark.parametrize("name", ["free", "paid", "none"])
def test_sync_interval_matrix(hub, monkeypatch, switch, name):
    monkeypatch.setenv("COMMONPLACE_PLANS", switch)
    assign(hub, name)
    limited = switch == "on" and name == "free"
    assert plans.sync_interval(hub.db, hub.admin) == (900 if limited else 0)


@pytest.mark.parametrize("ratio,delay", [(0, 0), (0.6, 0), (0.7, 0.5), (0.9, 1.5), (1, 2), (4, 2)])
def test_load_delay_formula(monkeypatch, ratio, delay):
    monkeypatch.setattr(plans, "load_ratio", lambda: ratio)
    assert plans.load_delay(plans.PAID) == pytest.approx(delay)
    assert plans.load_delay(plans.FREE) == 0
    assert plans.load_delay(None) == 0


def test_load_ratio_uses_cpu_count_and_handles_unavailable(monkeypatch):
    monkeypatch.setattr(plans.os, "getloadavg", lambda: (4, 3, 2), raising=False)
    monkeypatch.setattr(plans.os, "cpu_count", lambda: 8)
    assert plans.load_ratio() == 0.5
    monkeypatch.delattr(plans.os, "getloadavg")
    assert plans.load_ratio() == 0

    def unavailable():
        raise OSError

    monkeypatch.setattr(plans.os, "getloadavg", unavailable, raising=False)
    assert plans.load_ratio() == 0


@pytest.mark.parametrize(
    "switch,name", [("on", "free"), ("on", "paid"), ("on", "none"), ("off", "paid")]
)
def test_gateway_sleeps_only_for_paid_account_mcp_and_api(hub, monkeypatch, switch, name):
    monkeypatch.setenv("COMMONPLACE_PLANS", switch)
    assign(hub, name)
    delays = []
    monkeypatch.setattr(plans, "load_ratio", lambda: 0.8)
    monkeypatch.setattr("commonplace.gateway.sleep", delays.append)
    token = hub.owner_token()
    assert api(hub, token, "GET", "/api/plan")[0] == 200
    assert api(hub, token, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"})[0] == 200
    assert delays == pytest.approx([1, 1] if switch == "on" and name == "paid" else [])
    delays.clear()
    assert hub.raw(token, None, method="GET", path="/app/")[0] == 200
    assert hub.raw(token, None, method="GET", path="/health")[0] == 200
    assert api(hub, None, "GET", "/api/plan")[0] == 401
    shared = hub.db.create_workspace("shared", hub.admin)
    shared_token = hub.token("shared", workspace=shared, trust="trusted")
    assert api(hub, shared_token, "GET", "/api/plan")[0] == 200
    assert delays == []


@pytest.mark.parametrize("name", ["free", "paid", "none"])
def test_plan_api_payload_for_tokens_and_people(hub, monkeypatch, name):
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    monkeypatch.setattr(plans, "load_ratio", lambda: 0)
    assign(hub, name)
    fill(hub, 12)
    if name == "free":
        plans.use_transcription(hub.db, hub.admin, 123)
    expected = {
        "enabled": True,
        "plan": name if name != "none" else None,
        "storage": {"used": 12, "limit": plans.FREE.storage_bytes if name == "free" else None},
        "transcription": {
            "used_today": 123 if name == "free" else 0,
            "limit": 600 if name == "free" else None,
        },
        "sync": {"interval_seconds": 900 if name == "free" else 0},
    }
    assert api(hub, hub.owner_token(), "GET", "/api/plan") == (200, expected)
    cookie, _, _ = login(hub)
    assert api(hub, cookie, "GET", "/api/plan") == (200, expected)
    assert api(hub, None, "GET", "/api/plan")[0] == 401


def test_api_uses_workspace_account_instead_of_token_holder(hub, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    monkeypatch.setattr(plans, "load_ratio", lambda: 0)
    member = hub.db.create_user("member+" + hub.db.user(hub.admin)["email"])
    hub.db.add_member(hub.workspace, member, "member", actor=hub.admin)
    assign(hub)
    plans.set_plan(hub.db, member, "paid", actor=hub.admin)
    token = hub.token("member", user=member, profile="full", trust="trusted", write_wings=["*"])
    fill(hub)
    assert api(hub, token, "GET", "/api/plan")[1]["plan"] == "free"
    assert tool(hub, token, "board_add")["result"]["isError"]


def test_cli_set_show_none_off_and_admin_audits(hub, monkeypatch, capsys):
    data = ["--data", str(Path(hub.db.path).parent)]
    member = hub.db.create_user("member+" + hub.db.user(hub.admin)["email"])
    assert main(data + ["plan", "set", member, "free"]) == 0
    assert capsys.readouterr().out == "plan  free\nPlans are off.\n"
    row = hub.db._conn.execute(
        "SELECT * FROM account_plans WHERE user_id = ?", (member,)
    ).fetchone()
    assert row["set_by"] == hub.admin
    monkeypatch.setenv("COMMONPLACE_PLANS", "on")
    assert main(data + ["plan", "set", hub.admin, "free", "--by", member]) == 2
    assert "admin access required" in capsys.readouterr().err
    assert main(data + ["plan", "set", hub.db.user(hub.admin)["email"], "free"]) == 0
    fill(hub, 17)
    plans.use_transcription(hub.db, hub.admin, 2)
    capsys.readouterr()
    assert main(data + ["plan", "show", hub.admin]) == 0
    assert capsys.readouterr().out == (
        "plan  free\nstorage  17 / 262144000 bytes\ntranscription today  2 / 600 seconds\nsync interval  900 seconds\n"
    )
    assert main(data + ["plan", "set", hub.admin, "paid", "--by", hub.admin]) == 0
    capsys.readouterr()
    assert main(data + ["plan", "show", hub.db.user(hub.admin)["email"]]) == 0
    out = capsys.readouterr().out
    assert out.count("unlimited**") == 2 and plans.PAID_FOOTNOTE in out
    assert "sync interval  0 seconds" in out
    assert main(data + ["plan", "set", hub.admin, "none"]) == 0
    capsys.readouterr()
    assert main(data + ["plan", "show", hub.admin]) == 0
    assert "plan  none" in capsys.readouterr().out
    assert plans.get_plan(hub.db, hub.admin) is None
    entries = audits(hub, "plan.set")
    assert [e["reason"] for e in entries] == ["none", "paid", "free", "free"]
    assert all(e["user_id"] == hub.admin for e in entries)
    assert json.loads(entries[0]["target_ids"]) == [hub.admin]


def test_plan_renders_free_paid_hidden_and_escaped():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is needed for the plan render check")
    subprocess.run(
        [node, str(Path(__file__).with_name("commonplace_plan_render.mjs"))], check=True, timeout=10
    )
