"""Consent revisions through the real HTTP server and independent SQLite writers.

The pinned base has no endpoint (404). These are new-core acceptance tests,
not proof of an Activity navigation, stop or remount interaction.
"""

from __future__ import annotations

import http.client
import io
import json
import os
import sqlite3
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from commonplace import sessions
from commonplace.account import deletion, export, tables
from commonplace.compute import consent
from commonplace.control_db import ControlDB
from commonplace.gateway import Gateway
from commonplace.handoff.wiring import HandoffContext
from commonplace.runner import registry
from commonplace.webhooks import Dispatcher
from commonplace.workers import WorkerSupervisor

PREFIX = "/api/compute/consent/"
LABEL = "synthetic"
GRANT = {"label": LABEL, "projects": ["demo"], "topics": ["worker"], "wings": ["shared"]}


@contextmanager
def serving(db, directory):
    supervisor = WorkerSupervisor(str(directory))
    gateway = Gateway(db, supervisor)
    server = gateway.build_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
    thread.start()
    try:
        yield gateway, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        handoff: HandoffContext | None = getattr(gateway, "handoff", None)
        webhooks: Dispatcher | None = getattr(gateway, "webhooks", None)
        if handoff is not None:
            handoff.close()
        if webhooks is not None:
            webhooks.stop()
        supervisor.stop_all()


class API:
    def __init__(self, db, gateway, port, directory):
        self.db, self.gateway, self.port, self.directory = db, gateway, port, directory
        self.users = {
            name: db.create_user(name + "@example.invalid", is_admin=name in ("owner", "root"))
            for name in ("owner", "coowner", "member", "foreign", "root")
        }
        self.workspace = db.create_workspace("synthetic", self.users["owner"])
        self.foreign_workspace = db.create_workspace("foreign", self.users["foreign"])
        for name, role in (("coowner", "owner"), ("member", "member")):
            db.add_member(self.workspace, self.users[name], role, actor=self.users["owner"])
        self.cookies = {
            name: sessions.start_session(
                db,
                self.users[name],
                self.foreign_workspace if name == "foreign" else self.workspace,
            )
            for name in ("owner", "coowner", "member", "foreign")
        }
        self.machine = self.enroll()

    def principal(self, who="owner"):
        principal = sessions.session_principal(self.db, self.cookies[who])
        assert principal is not None
        return principal

    def enroll(self, who="owner", label="synthetic"):
        return registry.enroll(self.db, self.principal(who), {**GRANT, "label": label})[
            "machine_id"
        ]

    def request(
        self, method="GET", path=None, body=None, *, who="owner", headers=None, raw=None, port=None
    ):
        port = port or self.port
        values = {"Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"}
        if who is not None:
            values["Cookie"] = f"{sessions.COOKIE}={self.cookies[who]}"
        values.update(headers or {})
        client = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        try:
            client.request(
                method,
                path or PREFIX + self.machine,
                raw if raw is not None else json.dumps(body) if body is not None else None,
                values,
            )
            response = client.getresponse()
            result = json.loads(response.read())
            assert response.getheader("Cache-Control") == "no-store"
            return response.status, result
        finally:
            client.close()

    def write(self, allowed, revision, **kwargs):
        return self.request(
            "POST", body={"allowed": allowed, "expected_revision": revision}, **kwargs
        )

    def stored(self, machine=None):
        with self.db._lock:
            return dict(
                self.db._conn.execute(
                    "SELECT * FROM compute_consents WHERE machine_id=?", (machine or self.machine,)
                ).fetchone()
            )


@pytest.fixture
def api(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("COMMONPLACE_"):
            monkeypatch.delenv(key)
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    try:
        with serving(db, tmp_path) as (gateway, port):
            yield API(db, gateway, port, tmp_path)
    finally:
        db.close()


def test_initial_reads_do_not_grant_or_advance_and_install_is_idempotent(api):
    before = api.stored()
    assert api.request() == (
        200,
        {"consent": {"machine_id": api.machine, "allowed": False, "revision": 0}},
    )
    assert api.request() == api.request()
    assert api.stored() == before
    assert api.write(True, 0)[0] == 200
    allowed = api.stored()
    consent.ensure(api.db)
    consent.ensure(api.db)
    assert api.stored() == allowed
    assert api.request()[1]["consent"]["allowed"] is True


def test_allow_revoke_delayed_allow_conflict_and_fresh_explicit_allow(api):
    assert api.write(True, 0) == (
        200,
        {"consent": {"machine_id": api.machine, "allowed": True, "revision": 1}},
    )
    revoked = {"machine_id": api.machine, "allowed": False, "revision": 2}
    assert api.write(False, 1) == (200, {"consent": revoked})
    assert api.write(True, 0) == (409, {"error": "stale_revision", "consent": revoked})
    assert api.write(True, 1) == (409, {"error": "stale_revision", "consent": revoked})
    assert api.request() == (200, {"consent": revoked})
    assert api.write(True, 2)[1]["consent"] == {**revoked, "allowed": True, "revision": 3}
    assert api.write(True, 3)[1]["consent"]["revision"] == 4


def test_same_revision_concurrent_http_requests_on_independent_sqlite_connections(api):
    peer_db = ControlDB(api.db.path)
    try:
        with serving(peer_db, api.directory) as (_, peer_port):
            barrier = threading.Barrier(2)

            def write(port, allowed):
                barrier.wait()
                return api.write(allowed, 0, port=port)

            with ThreadPoolExecutor(max_workers=2) as pool:
                calls = [pool.submit(write, api.port, True), pool.submit(write, peer_port, False)]
                results = [call.result() for call in calls]
            assert sorted(status for status, _ in results) == [200, 409]
            committed = next(body["consent"] for status, body in results if status == 200)
            conflict = next(body for status, body in results if status == 409)
            assert conflict == {"error": "stale_revision", "consent": committed}
            assert api.request() == (200, {"consent": committed})
            assert api.stored()["revision"] == 1
    finally:
        peer_db.close()


@pytest.mark.parametrize("revision", [None, True, False, -1, 0.0, "0", [], {}, 2**63])
def test_invalid_revisions_never_write(api, revision):
    before = api.stored()
    assert api.write(True, revision) == (400, {"error": "invalid_request"})
    assert api.stored() == before


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"allowed": True},
        {"expected_revision": 0},
        {"allowed": 1, "expected_revision": 0},
        {"allowed": "false", "expected_revision": 0},
        {"allowed": True, "expected_revision": 0, "user_id": "other"},
    ],
)
def test_missing_revision_invalid_value_and_client_authority_are_refused(api, body):
    before = api.stored()
    assert api.request("POST", body=body)[0] == 400
    assert api.stored() == before


def test_json_duplicates_and_cross_origin_cannot_mutate(api):
    before = api.stored()
    assert (
        api.request("POST", raw=b'{"allowed":true,"expected_revision":0,"expected_revision":1}')[0]
        == 400
    )
    assert api.write(True, 0, headers={"Origin": "https://foreign.example.invalid"})[0] == 403
    assert api.stored() == before


@pytest.mark.parametrize(
    "who,status", [(None, 401), ("member", 403), ("coowner", 404), ("foreign", 404)]
)
def test_other_principals_cannot_read_write_or_receive_stale_state(api, who, status):
    assert api.write(True, 0)[0] == 200
    before = api.stored()
    for method, body in (("GET", None), ("POST", {"allowed": False, "expected_revision": 0})):
        actual, result = api.request(method, body=body, who=who)
        assert actual == status, result
        assert "consent" not in result
    assert api.stored() == before


@pytest.mark.parametrize("kind", ["machine", "worker"])
def test_runner_tokens_cannot_impersonate_the_person_owner(api, kind):
    made = registry.enroll(api.db, api.principal(), {**GRANT, "label": "token-control"})
    before = api.stored()
    for method in ("GET", "POST"):
        status, body = api.request(
            method,
            body={"allowed": True, "expected_revision": 0} if method == "POST" else None,
            headers={"Authorization": "Bearer " + made[kind + "_token"]},
        )
        assert status == (404 if kind == "machine" else 403) and "consent" not in body
    assert api.stored() == before


@pytest.mark.parametrize("change", ["grant", "machine", "worker"])
def test_current_grant_and_connection_rotation_revoke_and_advance_atomically(api, change):
    assert api.write(True, 0)[0] == 200
    if change == "grant":
        registry.patch(api.db, api.principal(), api.machine, {"max_in_flight": 2})
    else:
        registry.rotate(api.db, api.principal(), api.machine, change)
    row = api.stored()
    assert row["allowed"] == 0 and row["revision"] > 1
    assert api.write(True, 1)[0] == 409
    assert api.write(True, row["revision"])[0] == 200


@pytest.mark.parametrize("kind", ["machine", "worker"])
def test_revoked_or_expired_connection_denies_access_and_preserves_revision(api, kind):
    assert api.write(True, 0)[0] == 200
    machine = registry.get_machine(api.db, api.workspace, api.machine)
    connection = machine[kind + "_connection_id"]
    with api.db._tx() as conn:
        conn.execute(
            "UPDATE connections SET expires_at='2000-01-01T00:00:00Z' WHERE id=?", (connection,)
        )
    row = api.stored()
    assert row["allowed"] == 0 and row["revision"] > 1
    assert api.request()[0] == api.write(True, row["revision"])[0] == 409
    with api.db._tx() as conn:
        conn.execute(
            "UPDATE connections SET expires_at='2099-01-01T00:00:00Z' WHERE id=?", (connection,)
        )
    assert api.write(True, 1)[0] == 409
    current = api.stored()
    assert api.write(True, current["revision"])[0] == 200
    with api.db._tx() as conn:
        conn.execute(
            "UPDATE connections SET revoked_at='2026-01-01T00:00:00Z' WHERE id=?", (connection,)
        )
    assert api.stored()["allowed"] == 0
    assert api.request()[0] == 409


def test_machine_revocation_is_not_a_revision_reset(api):
    assert api.write(True, 0)[0] == 200
    registry.revoke(api.db, api.principal(), api.machine)
    assert api.stored()["allowed"] == 0 and api.stored()["revision"] > 1
    assert api.write(True, 0)[0] == api.request()[0] == 409
    consent.ensure(api.db)
    assert api.stored()["revision"] > 1


def test_labels_cannot_retarget_a_delayed_request_to_a_reenrolled_machine(api):
    assert api.request(path=PREFIX + LABEL)[0] == 404
    registry.revoke(api.db, api.principal(), api.machine)
    replacement = api.enroll()
    assert replacement != api.machine
    assert api.write(True, 0, path=PREFIX + LABEL)[0] == 404
    assert api.write(True, 0)[0] == 409
    assert api.stored(replacement)["allowed"] == 0


@pytest.mark.parametrize("change", ["membership", "account"])
def test_owner_authority_loss_and_restoration_never_revive_an_old_allow(api, change):
    assert api.write(True, 0)[0] == 200
    with api.db._tx() as conn:
        if change == "membership":
            conn.execute(
                "UPDATE memberships SET role='member' WHERE user_id=? AND workspace_id=?",
                (api.users["owner"], api.workspace),
            )
        else:
            conn.execute(
                "UPDATE users SET disabled_at='2026-01-01T00:00:00Z' WHERE id=?",
                (api.users["owner"],),
            )
    assert api.request()[0] in (401, 403)
    assert api.stored()["allowed"] == 0 and api.stored()["revision"] > 1
    with api.db._tx() as conn:
        if change == "membership":
            conn.execute(
                "UPDATE memberships SET role='owner' WHERE user_id=? AND workspace_id=?",
                (api.users["owner"], api.workspace),
            )
        else:
            conn.execute("UPDATE users SET disabled_at=NULL WHERE id=?", (api.users["owner"],))
    assert api.write(True, 1)[0] == 409
    assert api.write(True, api.stored()["revision"])[0] == 200


def test_revoked_session_cannot_use_current_revision(api):
    sessions.end_session(api.db, api.cookies["owner"])
    assert api.write(True, 0)[0] == api.request()[0] == 401
    assert api.stored()["revision"] == 0


def test_failed_audit_rolls_back_the_conditional_write(api):
    before = api.stored()
    with api.db._lock:
        api.db._conn.execute("""CREATE TRIGGER synthetic_consent_audit_failure BEFORE INSERT ON audit_log
            WHEN NEW.action='compute.consent.allow' BEGIN SELECT RAISE(ABORT,'synthetic refusal'); END""")
    assert api.write(True, 0) == (500, {"error": "internal error"})
    assert api.stored() == before
    assert api.request()[1]["consent"]["revision"] == 0


def test_failed_authority_mutation_rolls_back_its_consent_invalidation(api):
    assert api.write(True, 0)[0] == 200
    before = api.stored()
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        with api.db._tx() as conn:
            conn.execute(
                "UPDATE runner_machines SET grant_version=grant_version+1 WHERE id=?",
                (api.machine,),
            )
            assert api.stored()["allowed"] == 0
            conn.execute("UPDATE runner_machines SET enabled=2 WHERE id=?", (api.machine,))
    assert api.stored() == before


def test_new_table_migration_seeds_denied_and_preserves_existing_machine_data(tmp_path):
    db = ControlDB(str(tmp_path / "migration.sqlite3"))
    try:
        registry.ensure(db)
        sessions.ensure_schema(db)
        owner = db.create_user("migration@example.invalid")
        workspace = db.create_workspace("legacy", owner)
        principal = sessions.person_principal(db, owner, workspace)
        assert principal is not None
        machine = registry.enroll(db, principal, GRANT)["machine_id"]
        before = dict(registry.get_machine(db, workspace, machine))
        consent.ensure(db)
        with db._lock:
            row = dict(
                db._conn.execute(
                    "SELECT * FROM compute_consents WHERE machine_id=?", (machine,)
                ).fetchone()
            )
        assert row == {
            "machine_id": machine,
            "workspace_id": workspace,
            "user_id": owner,
            "allowed": 0,
            "revision": 0,
        }
        assert dict(registry.get_machine(db, workspace, machine)) == before
    finally:
        db.close()


def archive(api, who):
    sink = io.BytesIO()
    manifest = export.write(api.gateway, export.plan(api.gateway, api.users[who]), sink)
    assert manifest["complete"] is True and manifest["unclassified_tables"] == []
    with zipfile.ZipFile(io.BytesIO(sink.getvalue())) as zipped:
        return {
            name: [json.loads(line) for line in zipped.read(name).splitlines()]
            for name in zipped.namelist()
            if name.endswith("compute_consents.ndjson")
        }


def test_export_classifies_workspace_lifecycle_without_leaking_another_owners_consent(api):
    coowned = api.enroll("coowner", "coowned")
    member_machine = api.enroll("foreign", "foreign")
    assert api.write(True, 0)[0] == 200
    assert tables.RULES["compute_consents"].workspace == ("workspace_id",)
    assert tables.RULES["compute_consents"].user == ("user_id",)
    for who, machine in (("owner", api.machine), ("coowner", coowned)):
        exported = archive(api, who)
        assert exported
        for rows in exported.values():
            assert {row["machine_id"] for row in rows} == {machine}
            assert all(row["user_id"] == api.users[who] for row in rows)
            assert member_machine not in {row["machine_id"] for row in rows}
    assert archive(api, "member") == {}


def test_purge_deletes_only_the_leaving_owners_consent_in_a_retained_workspace(api, monkeypatch):
    other_machine = api.enroll("coowner", "coowned")
    other_before = api.stored(other_machine)
    assert api.write(True, 0)[0] == 200
    alone = api.db.create_workspace("owned alone", api.users["owner"])
    alone_owner = sessions.person_principal(api.db, api.users["owner"], alone)
    alone_machine = registry.enroll(api.db, alone_owner, GRANT)["machine_id"]
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION", "on")
    assert (
        api.request("POST", "/api/account/deletion", {"confirm": "owner@example.invalid"})[0] == 200
    )
    result = deletion.purge(api.gateway, api.users["owner"])
    assert result["state"] == "purged" and alone in result["deleted_workspaces"]
    with api.db._lock:
        assert (
            api.db._conn.execute(
                "SELECT * FROM compute_consents WHERE user_id=?", (api.users["owner"],)
            ).fetchall()
            == []
        )
        assert (
            api.db._conn.execute("SELECT 1 FROM workspaces WHERE id=?", (api.workspace,)).fetchone()
            is not None
        )
        assert (
            api.db._conn.execute(
                "SELECT 1 FROM compute_consents WHERE machine_id=?", (alone_machine,)
            ).fetchone()
            is None
        )
    assert api.stored(other_machine) == other_before
    assert api.request(who="owner")[0] == 401
    assert api.request(path=PREFIX + other_machine, who="coowner")[0] == 200
