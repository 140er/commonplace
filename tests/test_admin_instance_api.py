"""Instance operational reads: server authority, packaged identity and hostile receipts."""

import json
import os
from contextlib import contextmanager

import pytest

from tests._commonplace_helpers import hub_fixture as hub_fixture
from tests.test_admin_api import agent, clock, cookie, get, member
from tests.test_commonplace_web import request

DENIED = {"error": "Instance administrator access required"}
SHA = "0123456789abcdef" * 2 + "01234567"
STAMP = "2026-11-07T12:00:00Z"


def receipt(mode="online", result="success"):
    return dict(
        schema=1,
        run_id="12345678-1234-4234-8234-123456789abc",
        mode=mode,
        result=result,
        started_at=STAMP,
        finished_at=STAMP,
        stage="complete" if result == "success" else "backup_failed",
        scope="instance_snapshot",
    )


def instance(hub, section, headers, query=""):
    code, headers, body = request(hub, "/api/admin/instance/" + section + query, headers=headers)
    return code, json.loads(body), headers


def test_instance_authority(hub, monkeypatch):
    clock(hub)
    owner = cookie(hub)
    for section in ("deploy", "backups"):
        assert instance(hub, section, owner)[0] == 200
    from commonplace.web import admin

    reads = []
    flag_selects = []
    spied_select = admin.select

    def select_spy(conn, context, sql, parameters=()):
        if "is_admin" in sql:
            flag_selects.append(sql)
        return spied_select(conn, context, sql, parameters)

    monkeypatch.setattr(admin, "select", select_spy)
    real_read = admin.read_control

    @contextmanager
    def spy(db, context):
        reads.append(True)
        with real_read(db, context) as conn:
            assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
            yield conn

    monkeypatch.setattr(admin, "read_control", spy)

    opened = []

    def forbidden(*args, **kwargs):
        opened.append(True)
        return {}

    monkeypatch.setattr(admin, "deploy", forbidden)
    from commonplace import backup_status

    monkeypatch.setattr(backup_status, "read_receipts", forbidden)
    member_id = member(hub)
    hub.db._conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (member_id,))
    _, pat = agent(hub, "owner-pat")
    from tests.test_commonplace_oauth import Client

    client = Client(hub)
    grant = client.grant(client.register())
    hub.db._conn.execute("UPDATE connections SET expires_at=NULL WHERE auth='oauth'")
    for headers in (
        cookie(hub, "member@example.com"),
        pat,
        {"Authorization": "Bearer " + grant["access_token"]},
    ):
        for section in ("deploy", "backups"):
            assert instance(hub, section, headers)[:2] == (403, DENIED)
        assert (
            json.loads(request(hub, "/api/me", headers=headers)[2])["can_admin_instance"] is False
        )
    assert len(reads) == 1, "Only the admin-member navigation capability needs a flag read"
    assert flag_selects == ["SELECT is_admin FROM users WHERE id=?"]
    assert json.loads(request(hub, "/api/me", headers=owner)[2])["can_admin_instance"] is True
    hub.db._conn.execute("UPDATE users SET is_admin=0 WHERE id=?", (hub.admin,))
    for section in ("deploy", "backups"):
        assert instance(hub, section, {**owner, "X-Can-Admin-Instance": "true"})[:2] == (
            403,
            DENIED,
        )
    assert json.loads(request(hub, "/api/me", headers=owner)[2])["can_admin_instance"] is False
    hub.db._conn.execute("UPDATE users SET is_admin=1 WHERE id=?", (hub.admin,))
    other = hub.db.create_workspace("also-owned", hub.admin)
    assert get(hub, "users", owner, other)[:2] == (404, {"error": "not found"})

    # Missing server row denies; read errors fail closed without operational reads.
    class Missing:
        def fetchone(self):
            return None

    real_select = admin.select
    monkeypatch.setattr(
        admin,
        "select",
        lambda conn, context, sql, parameters=(): (
            Missing()
            if sql.startswith("SELECT is_admin")
            else real_select(conn, context, sql, parameters)
        ),
    )
    assert instance(hub, "deploy", owner)[:2] == (403, DENIED)

    def broken(*args):
        raise OSError("private-source")

    monkeypatch.setattr(admin, "read_control", broken)
    for section in ("deploy", "backups"):
        code, data, _ = instance(hub, section, owner)
        assert code == 503 and "private-source" not in json.dumps(data)
    code, _, raw = request(hub, "/api/me", headers=owner)
    assert code == 200 and json.loads(raw)["can_admin_instance"] is False
    assert json.loads(raw)["can_manage_instance"] is False
    assert not opened, "Denied/read-error paths must not open operational sources"


def test_running_version_and_commit(hub, monkeypatch):
    clock(hub)
    headers = cookie(hub)
    code, data, response = instance(hub, "deploy", headers)
    assert code == 200
    from commonplace import __version__ as cp_version
    from commonplace.web import admin
    from mempalace.version import __version__ as mp_version

    assert data["commonplace_version"] == cp_version
    assert data["mempalace_version"] == mp_version
    assert response["Cache-Control"] == "no-store"

    class Resource:
        def joinpath(self, name):
            assert name == "_build_commit.txt"
            return self

        def read_text(self, **kwargs):
            if marker is None:
                raise FileNotFoundError()
            return marker

    monkeypatch.setattr(admin.resources, "files", lambda package: Resource())
    for marker in (SHA, "a" * 64, "bad", "../unsafe", "a" * 41, None):
        _, data, _ = instance(hub, "deploy", headers)
        valid = marker in (SHA, "a" * 64)
        assert data["commit"] == (marker if valid else None)
        assert data["commit_source"] == ("build" if valid else "unknown")
    assert instance(hub, "deploy", headers, "?path=/tmp/forbidden")[0] == 400


def test_commit_falls_back_to_the_environment_and_names_its_source(hub, monkeypatch):
    clock(hub)
    headers = cookie(hub)
    from commonplace.web import admin

    marker = {"value": None}

    class Resource:
        def joinpath(self, name):
            return self

        def read_text(self, **kwargs):
            if marker["value"] is None:
                raise FileNotFoundError()
            return marker["value"]

    def identity():
        _, data, _ = instance(hub, "deploy", headers)
        return data["commit"], data["commit_source"], data.get("commit_short")

    monkeypatch.setattr(admin.resources, "files", lambda package: Resource())
    monkeypatch.setenv("COMMONPLACE_COMMIT", SHA)
    assert identity() == (SHA, "env", SHA[:12])
    # The build marker wins over the environment.
    marker["value"] = "b" * 40 + "\n"
    assert identity() == ("b" * 40, "build", "b" * 12)
    # An empty marker is the failing deploy: the environment still names the commit.
    marker["value"] = "\n"
    assert identity() == (SHA, "env", SHA[:12])
    # An invalid environment value is ignored, never echoed.
    monkeypatch.setenv("COMMONPLACE_COMMIT", "../unsafe")
    assert identity() == (None, "unknown", None)
    monkeypatch.delenv("COMMONPLACE_COMMIT")
    assert identity() == (None, "unknown", None)


def test_deploy_shows_free_data_space_and_a_low_disk(hub, monkeypatch):
    clock(hub)
    headers = cookie(hub)
    monkeypatch.setenv("MEMPALACE_MIN_FREE_BYTES", "1")
    code, data, _ = instance(hub, "deploy", headers)
    assert code == 200
    assert isinstance(data["data_free_bytes"], int) and data["data_free_bytes"] > 0
    assert data["disk_low"] is False
    monkeypatch.setenv("MEMPALACE_MIN_FREE_BYTES", str(2**62))
    assert instance(hub, "deploy", headers)[1]["disk_low"] is True


def test_deploy_shows_where_the_memory_sits(hub, monkeypatch):
    clock(hub)
    headers = cookie(hub)
    from mempalace import worker_memory

    cgroup = {
        "current_bytes": 6500,
        "limit_bytes": 8000,
        "anon_bytes": 1000,
        "file_bytes": 5000,
        "shmem_bytes": 40,
        "inactive_file_bytes": 3000,
        "working_set_bytes": 3500,
        "oom_kill": 0,
    }
    monkeypatch.setattr(worker_memory, "read_cgroup_memory", lambda: cgroup)
    rows = [
        {
            "workspace_id": "ws_aaaaaaaaaaaaaaaa",
            "pid": 11,
            "uptime_s": 5,
            "rss_bytes": 100,
            "anon_bytes": 60,
        },
        {
            "workspace_id": "ws_bbbbbbbbbbbbbbbb",
            "pid": 12,
            "uptime_s": 900,
            "rss_bytes": 900,
            "anon_bytes": 800,
        },
    ]
    monkeypatch.setattr(hub.gateway.supervisor, "worker_snapshot", lambda: rows, raising=False)
    code, data, _ = instance(hub, "deploy", headers)
    assert code == 200
    memory = data["memory"]
    assert memory["cgroup"] == cgroup
    assert set(memory["gateway"]) == {
        "rss_bytes",
        "anon_bytes",
        "file_bytes",
        "thread_count",
        "open_fd_count",
    }
    assert memory["gateway"]["thread_count"] >= 1
    assert memory["gateway"]["open_fd_count"] >= 1
    assert memory["gateway"]["rss_bytes"] > 0 and memory["gateway"]["anon_bytes"] > 0
    assert memory["worker_count"] == 2
    assert (memory["workers_rss_bytes"], memory["workers_anon_bytes"]) == (1000, 860)
    # Largest first; each row says whose worker it is, how long it has run and what it holds.
    assert [w["workspace_id"] for w in memory["workers"]] == [
        "ws_bbbbbbbbbbbbbbbb",
        "ws_aaaaaaaaaaaaaaaa",
    ]
    assert set(memory["workers"][0]) == {
        "workspace_id",
        "pid",
        "uptime_s",
        "rss_bytes",
        "anon_bytes",
        "file_bytes",
    }


def test_deploy_memory_view_never_breaks_the_deploy_view(hub, monkeypatch):
    clock(hub)
    headers = cookie(hub)
    from mempalace import worker_memory

    def broken(*args, **kwargs):
        raise OSError("fixture failure")

    monkeypatch.setattr(worker_memory, "read_cgroup_memory", broken)
    code, data, _ = instance(hub, "deploy", headers)
    assert code == 200 and data["memory"] is None
    assert data["commit_source"] and "data_free_bytes" in data
    # Without a cgroup (a laptop, another OS) the process figures still show.
    monkeypatch.setattr(worker_memory, "read_cgroup_memory", lambda: None)
    memory = instance(hub, "deploy", headers)[1]["memory"]
    assert memory["cgroup"] is None and memory["gateway"]["rss_bytes"] > 0


def test_deploy_memory_lists_a_bounded_number_of_workers(hub, monkeypatch):
    clock(hub)
    from commonplace.web import admin

    rows = [
        {"workspace_id": f"ws_{i:016x}", "pid": i, "uptime_s": i, "rss_bytes": i, "anon_bytes": i}
        for i in range(1, 200)
    ]
    monkeypatch.setattr(hub.gateway.supervisor, "worker_snapshot", lambda: rows, raising=False)
    memory = admin.memory_view(hub.gateway)
    assert memory["worker_count"] == 199 and len(memory["workers"]) == admin.MEMORY_WORKER_ROWS
    assert memory["workers"][0]["rss_bytes"] == 199


def test_backup_receipts_projection(hub, tmp_path, monkeypatch):
    clock(hub)
    monkeypatch.setenv("COMMONPLACE_BACKUP_STATUS_DIR", str(tmp_path))
    for mode, result in (("online", "success"), ("offline", "failure")):
        (tmp_path / (mode + ".json")).write_text(json.dumps(receipt(mode, result)))
    code, data, _ = instance(hub, "backups", cookie(hub))
    assert code == 200
    for mode, result in (("online", "success"), ("offline", "failure")):
        assert data[mode] == {"state": "ready", **receipt(mode, result)}
    assert set(data) == {"online", "offline"}
    assert str(tmp_path) not in json.dumps(data)
    assert instance(hub, "backups", cookie(hub), "?directory=/tmp")[0] == 400


def test_backup_missing_and_malformed(hub, tmp_path, monkeypatch):
    clock(hub)
    headers = cookie(hub)
    monkeypatch.delenv("COMMONPLACE_BACKUP_STATUS_DIR", raising=False)
    code, data, _ = instance(hub, "backups", headers)
    assert code == 200
    assert data == {
        m: {"state": "unknown", "reason": "not_configured"} for m in ("online", "offline")
    }
    monkeypatch.setenv("COMMONPLACE_BACKUP_STATUS_DIR", str(tmp_path))
    assert instance(hub, "backups", headers)[1]["online"] == {
        "state": "unknown",
        "reason": "no_record",
    }
    good = receipt("offline")
    (tmp_path / "offline.json").write_text(json.dumps(good))
    path = tmp_path / "online.json"
    invalid = [
        "{",
        "x" * 16385,
        *[
            json.dumps({**receipt(), **update})
            for update in (
                {"schema": True},
                {"schema": 2},
                {"mode": "offline"},
                {"result": "running"},
                {"stage": "secret"},
                {"stage": "backup_failed"},
                {"scope": "remote_upload"},
                {"run_id": "../path"},
                {"started_at": "2026-11-07T12:00:00"},
                {"finished_at": "2026-11-06T12:00:00Z"},
                {"token": "hidden"},
            )
        ],
    ]
    for raw in invalid:
        path.write_text(raw)
        _, data, _ = instance(hub, "backups", headers)
        assert data["online"] == {"state": "error", "error": "Backup status unavailable"}
        assert data["offline"] == {"state": "ready", **good}
    path.unlink()
    target = tmp_path / "private"
    target.write_text(json.dumps(receipt()))
    path.symlink_to(target)
    assert instance(hub, "backups", headers)[1]["online"]["state"] == "error"
    path.unlink()
    os.mkfifo(path)
    # No timing threshold: the no-follow/nonblocking flags are asserted before open.
    from commonplace import backup_status

    real_open = os.open
    opened_flags = []

    def checked_open(name, flags, *args, **kwargs):
        if str(name) == "online.json":
            opened_flags.append((flags, kwargs.get("dir_fd")))
            # Keep the hostile FIFO harmless even when testing a broken flag guard.
            return real_open(name, flags | os.O_NONBLOCK, *args, **kwargs)
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(backup_status.os, "open", checked_open)
    assert instance(hub, "backups", headers)[1]["online"]["state"] == "error"
    assert opened_flags and all(
        flags & os.O_NOFOLLOW and flags & os.O_NONBLOCK and directory is not None
        for flags, directory in opened_flags
    )
    path.unlink()
    path.mkdir()
    assert instance(hub, "backups", headers)[1]["online"]["state"] == "error"
    missing = tmp_path / "not-created"
    monkeypatch.setenv("COMMONPLACE_BACKUP_STATUS_DIR", str(missing))
    assert instance(hub, "backups", headers)[1]["online"]["state"] == "error"
    assert not missing.exists()


def test_instance_transport_controls(hub):
    for section in ("deploy", "backups"):
        code, data, headers = instance(hub, section, {})
        assert code == 401 and data == {"error": "unauthorized"}
        assert "Bearer" in headers["WWW-Authenticate"]
        assert (
            request(
                hub,
                "/api/admin/instance/" + section,
                method="POST",
                headers={**cookie(hub), "Content-Type": "application/json"},
            )[0]
            == 404
        )
    assert json.loads(request(hub, "/healthz")[2]) == {"ok": True}


def test_me_capability_read_failure_keeps_bootstrap(hub, monkeypatch):
    from commonplace.web import admin

    clock(hub)
    headers = cookie(hub)
    code, _, raw = request(hub, "/api/me", headers=headers)
    assert code == 200
    expected = json.loads(raw)
    real_select = admin.select

    def select(conn, context, sql, parameters=()):
        if sql.startswith("SELECT is_admin"):
            raise OSError("private-capability-read")
        return real_select(conn, context, sql, parameters)

    monkeypatch.setattr(admin, "select", select)
    code, _, raw = request(hub, "/api/me", headers=headers)
    assert code == 200
    assert json.loads(raw) == {
        **expected,
        "can_admin_instance": False,
        "can_manage_instance": False,
    }
    for section in ("deploy", "backups"):
        assert instance(hub, section, headers)[0] == 503


@pytest.mark.parametrize("field", ["started_at", "finished_at"])
@pytest.mark.parametrize("stamp", ["2026-11-07T12Z", "2026-11-07 12:00:00Z"])
def test_receipt_rejects_noncanonical_timestamps(hub, tmp_path, monkeypatch, field, stamp):
    clock(hub)
    monkeypatch.setenv("COMMONPLACE_BACKUP_STATUS_DIR", str(tmp_path))
    (tmp_path / "online.json").write_text(json.dumps({**receipt(), field: stamp}))
    (tmp_path / "offline.json").write_text(json.dumps(receipt("offline")))
    code, data, _ = instance(hub, "backups", cookie(hub))
    assert code == 200
    assert data["online"] == {"state": "error", "error": "Backup status unavailable"}
    assert data["offline"] == {"state": "ready", **receipt("offline")}


def test_receipt_open_flags_are_observable(hub, tmp_path, monkeypatch):
    from commonplace import backup_status

    clock(hub)
    monkeypatch.setenv("COMMONPLACE_BACKUP_STATUS_DIR", str(tmp_path))
    (tmp_path / "online.json").write_text(json.dumps(receipt()))
    seen = []
    real_open = os.open

    def observe(name, flags, *args, **kwargs):
        seen.append((str(name), flags, kwargs.get("dir_fd")))
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(backup_status.os, "open", observe)
    code, data, _ = instance(hub, "backups", cookie(hub))
    assert code == 200 and data["online"]["state"] == "ready"
    receipt_opens = [item for item in seen if item[0] == "online.json"]
    assert receipt_opens
    assert all(
        flags & os.O_NOFOLLOW and flags & os.O_NONBLOCK and directory is not None
        for _, flags, directory in receipt_opens
    )


def test_instance_read_error_never_opens_operational_sources(hub, monkeypatch):
    from commonplace import backup_status
    from commonplace.web import admin

    clock(hub)
    headers = cookie(hub)
    opened = []
    real_select = admin.select

    def select(conn, context, sql, parameters=()):
        if sql.startswith("SELECT is_admin"):
            raise OSError("private-capability-read")
        return real_select(conn, context, sql, parameters)

    def deploy():
        opened.append("deploy")
        return {}

    def receipts(context):
        opened.append("backups")
        return {mode: {"state": "unknown", "reason": "no_record"} for mode in ("online", "offline")}

    monkeypatch.setattr(admin, "select", select)
    monkeypatch.setattr(admin, "deploy", deploy)
    monkeypatch.setattr(backup_status, "read_receipts", receipts)
    for section in ("deploy", "backups"):
        assert instance(hub, section, headers)[0] == 503
        assert not opened, "The instance handler must deny before any operational source"


def test_operational_authority_is_not_limited_to_browser_sessions(hub):
    """The console is session-only; the existing deploy/backup predicate is not."""
    from types import SimpleNamespace

    from commonplace import instance_admin
    from commonplace.web import admin

    def principal(auth, role="owner", person=True):
        return SimpleNamespace(person=person, role=role, auth=auth, user_id=hub.admin)

    def authorized(who):
        return admin.instance_authorized(
            hub.gateway, who, admin.ReadContext(hub.gateway.admin_clock)
        )

    def console(who):
        return instance_admin.flag(hub.gateway, who, admin.ReadContext(hub.gateway.admin_clock))

    for auth in ("session", "pat", "oauth"):
        assert authorized(principal(auth)) is True
    assert console(principal("session")) is True
    assert console(principal("pat")) is console(principal("oauth")) is False
    assert authorized(principal("pat", role="member")) is False
    assert authorized(principal("session", person=False)) is False
