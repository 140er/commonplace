"""Account deletion: off by default, explicit, and it never touches another person's data."""

import calendar
import json
import sqlite3
import time

import pytest

from commonplace import sessions, tokens
from commonplace.account import deletion
from tests._commonplace_account_helpers import site_fixture, wait_for  # noqa: F401

DAY = 86400


@pytest.fixture
def enabled(site, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION", "on")
    return site


def ask(site, user, workspace, confirm, session=None):
    return site.json(
        "POST",
        "/api/account/deletion",
        session=session or site.session(user, workspace),
        body={"confirm": confirm},
    )


def state(site, user):
    rows = site.rows("SELECT * FROM account_deletions WHERE user_id = ?", user)
    return rows[0] if rows else None


def give_alice_a_footprint(site):
    """Rows about Alice in every kind of table: her own, a child, a credential, by email."""
    db = site.db
    connection = db.connection("alice-agent", site.team)["id"]
    with db._lock:
        run = db._conn.execute
        run(
            "INSERT INTO user_identities(provider, subject, user_id, created_at, last_used_at) "
            "VALUES ('github', 'alice-subject', ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (site.alice,),
        )
        run(
            "INSERT INTO account_plans VALUES (?, 'paid', 'root', '2026-01-01T00:00:00Z')",
            (site.alice,),
        )
        run("INSERT INTO transcription_usage VALUES (?, '2026-01-01', 12)", (site.alice,))
        run(
            "INSERT INTO email_challenges(token_hash, email, next, expires_at) "
            "VALUES ('h', 'Alice@Example.Test', '', '2099-01-01T00:00:00Z')"
        )
        run(
            "INSERT INTO phone_push_subscriptions(id, workspace_id, user_id, topic, active, "
            "from_seq, created_at) VALUES ('sub_a', ?, ?, 'cp-topic', 1, 0, "
            "'2026-01-01T00:00:00Z')",
            (site.team, site.alice),
        )
        run(
            "INSERT INTO phone_push_deliveries(id, subscription_id, workspace_id, number, "
            "category, status, attempt, created_at) VALUES ('del_a', 'sub_a', ?, 1, 'test', "
            "'accepted', 1, '2026-01-01T00:00:00Z')",
            (site.team,),
        )
        run(
            "INSERT INTO oauth_clients(client_id, client_name, redirect_uris, created_at) "
            "VALUES ('client', 'Client', '[]', '2026-01-01T00:00:00Z')"
        )
        run(
            "INSERT INTO oauth_tokens(token_hash, kind, client_id, connection_id, resource, "
            "scope, created_at, expires_at) VALUES ('oauth-hash', 'access', 'client', ?, '', "
            "'', 1, 2)",
            (connection,),
        )
        # In the shared team: one row that is hers, and rows that are the team's.
        run(
            "INSERT INTO answer_holds(id, workspace_id, user_id, card, kind, body, release_at, "
            "state, created_at, updated_at) VALUES ('hold_a', ?, ?, 1, 'decide', 'hers', 0, "
            "'released', 0, 0)",
            (site.team, site.alice),
        )
        run(
            "INSERT INTO webhooks(id, workspace_id, connection_id, owner_actor, url, "
            "secret_hash, event_id_key, kinds, active, start_seq, created_at) VALUES "
            "('hook_team', ?, ?, 'conn:alice-agent', 'https://example.test/hook', 'h', 'k', "
            "'[]', 1, 0, '2026-01-01T00:00:00Z')",
            (site.team, connection),
        )
        run(
            "INSERT INTO events(at, workspace_id, kind, subject, actor, data) VALUES "
            "('2026-01-01T00:00:00Z', ?, 'card.created', '1', 'conn:alice-agent', '{}')",
            (site.team,),
        )
    sessions.create_login_link(db, "alice@example.test")


def others_snapshot(site):
    """Everything that is not Alice's: control rows and the other workspaces' files."""
    mine = {site.alice, site.alice_home, "alice@example.test", "Alice@Example.Test"}
    mine |= {
        row["id"] for row in site.rows("SELECT id FROM connections WHERE user_id = ?", site.alice)
    }
    mine |= {"sub_a"}
    marks = {repr(value) for value in mine}
    dump = site.control_dump(skip=("audit_log", "account_deletions"))
    control = {table: [row for row in rows if not marks & set(row)] for table, rows in dump.items()}
    return control, site.tree(site.bob_home), site.tree(site.team)


# ── the switch and the request ──


def test_deletion_is_off_by_default(site):
    status, body = ask(site, site.alice, site.alice_home, "alice@example.test")
    assert status == 404 and body["code"] == "disabled"
    assert state(site, site.alice) is None
    assert getattr(site.gateway, "account_sweeper", None) is None


def test_request_needs_a_person_a_recent_sign_in_and_the_typed_address(enabled):
    site = enabled
    status, _body = site.json(
        "POST",
        "/api/account/deletion",
        token=site.alice_home_token,
        body={"confirm": "alice@example.test"},
    )
    assert status == 403

    status, body = ask(site, site.alice, site.alice_home, "bob@example.test")
    assert status == 400 and body["code"] == "confirm_mismatch"
    status, body = ask(site, site.alice, site.alice_home, None)
    assert status == 400

    old = site.session(site.alice, site.alice_home)
    site.advance(deletion.REAUTH_MINUTES * 60 + 5)
    status, body = ask(site, site.alice, site.alice_home, "alice@example.test", session=old)
    assert status == 403 and body["code"] == "reauth_required"
    assert state(site, site.alice) is None


def test_request_starts_a_grace_period_and_can_be_withdrawn(enabled):
    site = enabled
    session = site.session(site.alice, site.alice_home)
    status, body = ask(site, site.alice, site.alice_home, " Alice@example.test ", session=session)
    assert status == 200 and body["state"] == "pending"
    requested = time.strptime(body["requested_at"], "%Y-%m-%dT%H:%M:%SZ")
    due = time.strptime(body["purge_after"], "%Y-%m-%dT%H:%M:%SZ")
    assert calendar.timegm(due) - calendar.timegm(requested) == deletion.DEFAULT_GRACE_DAYS * DAY

    # Asking twice changes nothing, and the account keeps working during the grace period.
    assert ask(site, site.alice, site.alice_home, "alice@example.test", session=session)[1] == body
    status, shown = site.json("GET", "/api/account", session=session)
    assert shown["deletion"]["request"]["state"] == "pending"
    assert shown["deletion"]["delete_workspaces"] == [{"id": site.alice_home, "name": "alice home"}]
    assert shown["deletion"]["leave_workspaces"] == [{"id": site.team, "name": "team"}]
    assert site.db.authenticate_token(site.alice_home_token) is not None

    site.advance(deletion.DEFAULT_GRACE_DAYS * DAY - 60)
    assert deletion.purge_due(site.gateway) == []
    assert site.directory(site.alice_home).is_dir()

    assert site.json("DELETE", "/api/account/deletion", session=session) == (200, {"ok": True})
    assert site.json("DELETE", "/api/account/deletion", session=session)[0] == 404
    site.advance(2 * DAY)
    assert deletion.purge_due(site.gateway) == []
    assert state(site, site.alice) is None
    assert site.rows("SELECT disabled_at FROM users WHERE id = ?", site.alice) == [
        {"disabled_at": None}
    ]


def test_grace_days_setting(monkeypatch):
    for value, days in (("", 14), ("0", 0), ("30", 30), ("91", 14), ("-1", 14), ("soon", 14)):
        monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION_GRACE_DAYS", value)
        assert deletion.grace_days() == days


# ── the purge ──


def test_purge_removes_the_account_and_leaves_everyone_else_intact(enabled):
    site = enabled
    give_alice_a_footprint(site)
    home = site.data_dir / "workspaces" / site.alice_home
    generation = home.parent / (site.alice_home + ".generation-" + "0" * 24)
    generation.mkdir()
    (generation / "old.sqlite3").write_bytes(b"retired copy")
    neighbour = home.parent / (site.bob_home + ".generation-" + "1" * 24)
    neighbour.mkdir()
    (neighbour / "kept.txt").write_text("bob's retired generation", encoding="utf-8")
    stopped = []

    class Worker:
        def stop(self):
            stopped.append(site.alice_home)

    site.supervisor._workers[site.alice_home] = Worker()
    bob_session = site.session(site.bob, site.bob_home)
    alice_session = site.session(site.alice, site.alice_home)
    assert ask(site, site.alice, site.alice_home, "alice@example.test", alice_session)[0] == 200
    before = others_snapshot(site)
    audit_before = site.rows("SELECT count(*) AS n FROM audit_log")[0]["n"]

    site.advance(deletion.DEFAULT_GRACE_DAYS * DAY + 1)
    assert deletion.purge_due(site.gateway) == [
        {"user": site.alice, "state": "purged", "deleted_workspaces": [site.alice_home]}
    ]

    # Her own workspace is gone from disk, with its retired generation and its worker.
    assert not home.exists() and not generation.exists()
    assert stopped == [site.alice_home]
    # Nothing in the control database names her workspace, her address or her name any more.
    dump = site.control_dump(skip=("audit_log", "account_deletions"))
    text = repr(dump)
    assert site.alice_home not in text
    assert "alice@example.test" not in text.lower() and "Alice Example" not in text
    assert "alice@example.test" not in repr(site.control_dump()).lower()
    for table in (
        "sessions",
        "login_links",
        "user_identities",
        "account_plans",
        "transcription_usage",
        "email_challenges",
        "phone_push_subscriptions",
        "phone_push_deliveries",
        "oauth_tokens",
    ):
        assert dump[table] == [row for row in dump[table] if repr(site.bob) in row], table
    # In the shared team her own row is gone; what belongs to the team stays, also
    # where it names her connection.
    assert dump["answer_holds"] == []
    assert [row[0] for row in dump["webhooks"]] == [repr("hook_team")]
    assert site.rows("SELECT actor FROM events WHERE workspace_id = ?", site.team) == [
        {"actor": "conn:alice-agent"}
    ]
    # What is left of her is a tombstone that can do nothing.
    assert site.rows("SELECT email, name, is_admin FROM users WHERE id = ?", site.alice) == [
        {"email": f"deleted-{site.alice}@account.invalid", "name": "", "is_admin": 0}
    ]
    assert site.rows("SELECT disabled_at FROM users WHERE id = ?", site.alice)[0]["disabled_at"]
    assert site.rows(
        "SELECT workspace_id, role FROM memberships WHERE user_id = ?", site.alice
    ) == [{"workspace_id": site.team, "role": "member"}]
    (connection,) = site.rows("SELECT * FROM connections WHERE user_id = ?", site.alice)
    assert connection["name"] == "alice-agent" and connection["revoked_at"]
    assert connection["token_hash"] is None and connection["token_hint"] == ""
    assert site.db.authenticate_token(site.alice_home_token) is None
    assert site.db.authenticate_token(site.alice_team_token) is None
    assert site.request("GET", "/api/me", session=alice_session)[0] == 401
    assert state(site, site.alice)["state"] == "purged"
    assert json.loads(state(site, site.alice)["workspaces"]) == [site.alice_home]
    # The audit log is append-only: it only grew.
    assert site.rows("SELECT count(*) AS n FROM audit_log")[0]["n"] > audit_before
    assert site.rows("SELECT decision FROM audit_log WHERE action = 'account.purge'") == [
        {"decision": "ok"}
    ]

    # Bob, the team and every other row and file are exactly as they were.
    assert others_snapshot(site) == before
    assert (neighbour / "kept.txt").read_text(encoding="utf-8") == "bob's retired generation"
    assert site.db.authenticate_token(site.bob_team_token) is not None
    status, me = site.json("GET", "/api/me", session=bob_session)
    assert status == 200 and me["email"] == "bob@example.test"
    conn = sqlite3.connect(site.directory(site.team) / "boards.sqlite3")
    assert conn.execute("SELECT count(*) FROM tasks").fetchone() == (2,)
    conn.close()

    # The address is free again: the same person can start over with a new account.
    again = site.db.create_user("alice@example.test")
    assert again != site.alice
    assert deletion.purge_due(site.gateway) == []


def test_a_workspace_is_deleted_only_when_no_live_member_is_left(enabled):
    """Alice's tombstone in the team does not keep Bob from deleting it later."""
    site = enabled
    assert ask(site, site.alice, site.alice_home, "alice@example.test")[0] == 200
    site.advance(15 * DAY)
    deletion.purge_due(site.gateway)

    status, shown = site.json("GET", "/api/account", session=site.session(site.bob, site.bob_home))
    assert {w["id"] for w in shown["deletion"]["delete_workspaces"]} == {site.bob_home, site.team}
    recent = site.session_now(site.bob, site.bob_home)
    assert ask(site, site.bob, site.bob_home, "bob@example.test", recent)[0] == 200
    site.advance(15 * DAY)
    (result,) = deletion.purge_due(site.gateway)
    assert result["state"] == "purged"
    assert set(result["deleted_workspaces"]) == {site.bob_home, site.team}
    assert not (site.data_dir / "workspaces" / site.team).exists()
    assert site.rows("SELECT id FROM workspaces") == []
    assert site.rows("SELECT * FROM memberships") == []
    assert site.rows("SELECT * FROM connections") == []
    assert {row["id"] for row in site.rows("SELECT id FROM users")} == {
        site.root,
        site.alice,
        site.bob,
    }


def test_sole_owner_of_a_shared_workspace_cannot_leave(enabled):
    site = enabled
    status, body = ask(site, site.bob, site.bob_home, "bob@example.test")
    assert status == 409 and body["code"] == "blocked"
    assert body["blockers"] == [
        {"code": "sole_owner", "workspace": {"id": site.team, "name": "team"}}
    ]
    assert state(site, site.bob) is None

    # A second owner lifts the block; Bob then leaves the team without deleting it.
    site.db.add_member(site.team, site.root, "owner", actor=site.bob)
    assert ask(site, site.bob, site.bob_home, "bob@example.test")[0] == 200
    site.advance(15 * DAY)
    (result,) = deletion.purge_due(site.gateway)
    assert result["deleted_workspaces"] == [site.bob_home]
    assert (site.data_dir / "workspaces" / site.team).is_dir()
    assert site.db.authenticate_token(site.alice_team_token) is not None


def test_last_administrator_and_github_grant_block(enabled):
    site = enabled
    home = site.db.create_workspace("root home", site.root, personal=True)
    status, body = ask(site, site.root, home, "root@example.test")
    assert status == 409 and body["blockers"] == [{"code": "last_admin"}]

    with site.db._lock:
        site.db._conn.execute(
            "INSERT INTO github_grants(user_id, workspace_id, subject, login, expires_at, secret) "
            "VALUES (?, ?, 's', 'alice', 0, x'00')",
            (site.alice, site.alice_home),
        )
    status, body = ask(site, site.alice, site.alice_home, "alice@example.test")
    assert status == 409 and body["blockers"] == [{"code": "github_connected"}]


def test_a_block_that_appears_during_the_grace_period_stops_the_purge(enabled):
    site = enabled
    assert ask(site, site.alice, site.alice_home, "alice@example.test")[0] == 200
    carol = site.db.create_user("carol@example.test")
    site.db.add_member(site.alice_home, carol, "member", actor=site.alice)
    before = site.tree(site.alice_home)

    site.advance(15 * DAY)
    for _ in range(2):  # a retry records nothing new
        (result,) = deletion.purge_due(site.gateway)
        assert result["state"] == "pending"
        assert [b["code"] for b in result["blocked"]] == ["sole_owner"]
    assert site.tree(site.alice_home) == before
    assert site.rows("SELECT disabled_at FROM users WHERE id = ?", site.alice) == [
        {"disabled_at": None}
    ]
    assert site.db.authenticate_token(site.alice_home_token) is not None
    assert site.rows("SELECT decision FROM audit_log WHERE action = 'account.purge'") == [
        {"decision": "blocked"}
    ]
    status, shown = site.json(
        "GET", "/api/account", session=site.session(site.alice, site.alice_home)
    )
    assert shown["deletion"]["request"]["blocked"][0]["code"] == "sole_owner"


def test_unclassified_control_table_blocks_instead_of_guessing(enabled):
    site = enabled
    with site.db._lock:
        site.db._conn.execute("CREATE TABLE later_feature (user_id TEXT, note TEXT)")
    status, body = ask(site, site.alice, site.alice_home, "alice@example.test")
    assert status == 409
    assert body["blockers"] == [{"code": "unsupported_data", "tables": ["later_feature"]}]


def test_an_interrupted_purge_is_resumed(enabled, monkeypatch):
    site = enabled
    assert ask(site, site.alice, site.alice_home, "alice@example.test")[0] == 200
    site.advance(15 * DAY)
    real = deletion.remove_workspace

    def crash(gateway, workspace_id):
        raise OSError("disk went away")

    monkeypatch.setattr(deletion, "remove_workspace", crash)
    assert deletion.purge_due(site.gateway) == [{"user": site.alice, "state": "error"}]
    assert state(site, site.alice)["state"] == "purging"
    # Already locked out, nothing deleted yet, and no way back in.
    assert site.db.authenticate_token(site.alice_home_token) is None
    assert site.directory(site.alice_home).is_dir()
    assert deletion.cancel(site.gateway, site.alice) is False

    monkeypatch.setattr(deletion, "remove_workspace", real)
    (result,) = deletion.purge_due(site.gateway)
    assert result["state"] == "purged"
    assert not (site.data_dir / "workspaces" / site.alice_home).exists()


def test_a_member_who_joins_as_the_purge_starts_keeps_the_workspace(enabled, monkeypatch):
    site = enabled
    assert ask(site, site.alice, site.alice_home, "alice@example.test")[0] == 200
    site.advance(15 * DAY)
    carol = site.db.create_user("carol@example.test")
    real, calls, removed = deletion.effects, [], []

    def joined_meanwhile(conn, present, user_id):
        calls.append(user_id)
        if len(calls) == 2:  # after the account was disabled, before any file is removed
            site.db.add_member(site.alice_home, carol, "member", actor=site.alice)
        return real(conn, present, user_id)

    monkeypatch.setattr(deletion, "effects", joined_meanwhile)
    monkeypatch.setattr(deletion, "remove_workspace", lambda *args: removed.append(args))
    before = site.tree(site.alice_home)
    (result,) = deletion.purge_due(site.gateway)
    assert removed == [] and site.tree(site.alice_home) == before
    assert result["state"] == "purging"
    assert [b["code"] for b in result["blocked"]] == ["sole_owner"]
    assert site.rows("SELECT id FROM workspaces WHERE id = ?", site.alice_home)


def test_with_the_switch_off_only_started_purges_are_finished(enabled, monkeypatch):
    site = enabled
    assert ask(site, site.alice, site.alice_home, "alice@example.test")[0] == 200
    recent = site.session(site.root, site.db.create_workspace("root home", site.root))
    site.db.add_member(site.team, site.root, "owner", actor=site.bob)
    assert ask(site, site.bob, site.bob_home, "bob@example.test")[0] == 200
    assert recent
    site.advance(15 * DAY)
    with site.db._lock:
        site.db._conn.execute(
            "UPDATE account_deletions SET state = 'purging' WHERE user_id = ?", (site.alice,)
        )
    monkeypatch.delenv("COMMONPLACE_ACCOUNT_DELETION")
    assert deletion.unfinished(site.db)
    results = deletion.purge_due(site.gateway, resume_only=not deletion.enabled())
    assert [(r["user"], r["state"]) for r in results] == [(site.alice, "purged")]
    assert state(site, site.bob)["state"] == "pending"
    assert not deletion.unfinished(site.db)


def test_remove_workspace_refuses_other_paths(site, tmp_path):
    with pytest.raises(ValueError):
        deletion.remove_workspace(site.gateway, "../" + site.bob_home)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    root = site.data_dir / "workspaces" / site.alice_home
    deletion.remove_workspace(site.gateway, site.alice_home)
    root.symlink_to(outside, target_is_directory=True)
    deletion.remove_workspace(site.gateway, site.alice_home)
    assert not root.is_symlink() and (outside / "keep.txt").is_file()
    assert site.directory(site.bob_home).is_dir()


def test_sweeper_purges_due_accounts_and_stops(enabled):
    site = enabled
    assert ask(site, site.alice, site.alice_home, "alice@example.test")[0] == 200
    site.advance(15 * DAY)
    sweeper = deletion.Sweeper(site.gateway, interval_s=0.02)
    sweeper.start()
    try:
        assert wait_for(lambda: (state(site, site.alice) or {}).get("state") == "purged")
    finally:
        sweeper.stop()
    assert not sweeper._thread.is_alive()
    assert tokens.looks_like_token(site.alice_home_token)


def test_gateway_starts_the_sweeper_only_when_enabled(tmp_path, monkeypatch):
    from tests._commonplace_account_helpers import Site

    monkeypatch.setenv("COMMONPLACE_ACCOUNT_DELETION", "on")
    made = Site(tmp_path / "enabled")
    try:
        assert made.gateway.account_sweeper._thread.is_alive()
    finally:
        made.close()
    assert not made.gateway.account_sweeper._thread.is_alive()
