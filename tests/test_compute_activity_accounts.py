"""The Activity privacy boundary also applies to account export and purge."""

import io
import json
import zipfile

from test_compute_activity_api import activity_hub, enrolled, request  # noqa: F401

from commonplace import sessions
from commonplace.account import deletion, export, tables
from commonplace.runner import registry


def seed_two_owners(enrollment):
    hub, machine, path, snapshot = enrollment
    request(hub, "POST", path + "/consent", {"enabled": True})
    assert (
        request(
            hub,
            "POST",
            "/api/runner/activity",
            snapshot,
            cookie=False,
            token=machine["machine_token"],
        )[0]
        == 200
    )
    other = hub.db.create_user("other-owner@example.test")
    hub.db.add_member(hub.workspace, other, "owner", actor=hub.user)
    cookie = sessions.start_session(hub.db, other, hub.workspace)
    second = registry.enroll(
        hub.db,
        sessions.person_principal(hub.db, other, hub.workspace),
        {"label": "second", "projects": ["sample"], "topics": ["work"], "wings": ["shared"]},
    )
    assert (
        request(
            hub,
            "POST",
            "/api/compute/activity/" + second["machine_id"] + "/consent",
            {"enabled": True},
            cookie=cookie,
        )[0]
        == 200
    )
    assert (
        request(
            hub,
            "POST",
            "/api/runner/activity",
            snapshot,
            cookie=False,
            token=second["machine_token"],
        )[0]
        == 200
    )
    return hub, other


def test_account_export_keeps_each_owners_aggregate_rows_private(enrolled):  # noqa: F811
    hub, other = seed_two_owners(enrolled)
    with hub.db._lock:
        assert tables.unclassified(tables.present(hub.db._conn)) == []
    for user in (hub.user, other):
        buffer = io.BytesIO()
        manifest = export.write(hub.gateway, export.plan(hub.gateway, user), buffer)
        assert manifest["complete"] is True
        with zipfile.ZipFile(io.BytesIO(buffer.getvalue())) as archive:
            activity_files = [
                name
                for name in archive.namelist()
                if name.endswith(
                    ("compute_activity_consent.ndjson", "compute_activity_snapshots.ndjson")
                )
            ]
            assert len(activity_files) == 4  # Account and owned workspace, both tables.
            for name in activity_files:
                rows = [json.loads(line) for line in archive.read(name).splitlines()]
                assert len(rows) == 1 and rows[0]["owner_id"] == user


def test_owner_purge_removes_only_their_activity_from_shared_workspace(enrolled, monkeypatch):  # noqa: F811
    hub, other = seed_two_owners(enrolled)
    hub.db.create_user("admin@example.test", is_admin=True)
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION", "on")
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION_GRACE_DAYS", "0")
    assert (
        request(hub, "POST", "/api/account/deletion", {"confirm": "activity@example.test"})[0]
        == 200
    )
    result = deletion.purge_due(hub.gateway)
    assert result == [{"user": hub.user, "state": "purged", "deleted_workspaces": []}]
    with hub.db._lock:
        for table in ("compute_activity_consent", "compute_activity_snapshots"):
            rows = hub.db._conn.execute(f"SELECT owner_id FROM {table}").fetchall()
            assert [row[0] for row in rows] == [other]


def test_workspace_purge_removes_activity_rows_including_former_owner(enrolled, monkeypatch):  # noqa: F811
    hub, other = seed_two_owners(enrolled)
    admin = hub.db.create_user("admin@example.test", is_admin=True)
    # A former coowner's retained rows must also leave when the workspace goes.
    hub.db.remove_member(
        hub.workspace, other, actor=admin, force_owner=True, dry_run=False, reason="fixture"
    )
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION", "on")
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION_GRACE_DAYS", "0")
    assert (
        request(hub, "POST", "/api/account/deletion", {"confirm": "activity@example.test"})[0]
        == 200
    )
    assert deletion.purge_due(hub.gateway) == [
        {"user": hub.user, "state": "purged", "deleted_workspaces": [hub.workspace]}
    ]
    with hub.db._lock:
        assert (
            hub.db._conn.execute(
                "SELECT id FROM workspaces WHERE id=?", (hub.workspace,)
            ).fetchone()
            is None
        )
        for table in ("compute_activity_consent", "compute_activity_snapshots"):
            assert hub.db._conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
