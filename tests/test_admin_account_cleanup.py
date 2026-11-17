"""Destructive admin CLI operations use isolated databases and workspace files."""

import json
import sqlite3
from types import SimpleNamespace

import pytest

from commonplace import backup_pause, sessions
from commonplace.backup import data_lock, fence_lock
from commonplace.cli import build_parser, main
from commonplace.control_db import ControlDB, ControlError
from commonplace.oauth import store as oauth
from commonplace.policy import Policy


@pytest.fixture
def account(tmp_path, monkeypatch):
    monkeypatch.setenv("COMMONPLACE_ADMIN_SOCKET", str(tmp_path / "unused.sock"))
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    admin = db.create_user("admin@example.invalid", is_admin=True)
    user = db.create_user("test@example.invalid")
    workspace = db.create_workspace("team", admin)
    db.add_member(workspace, user, "member", actor=admin)
    sessions.ensure_schema(db)
    oauth.ensure_schema(db)
    yield SimpleNamespace(db=db, admin=admin, user=user, workspace=workspace, root=tmp_path)
    db.close()


def invoke(account, *args):
    try:
        return main(["--data", str(account.root), *args])
    except SystemExit as exc:
        return exc.code


def remove(account, *flags, user=None, workspace=None):
    return invoke(
        account,
        "member",
        "remove",
        workspace or account.workspace,
        user or account.user,
        "--by",
        account.admin,
        *flags,
    )


def delete(account, *flags, user=None):
    return invoke(account, "user", "delete", user or account.user, "--by", account.admin, *flags)


def dump(db):
    return list(db._conn.iterdump())


def credentials(account, workspace):
    db = account.db
    cid, pat = db.create_token(
        workspace_ref=workspace,
        user_ref=account.user,
        name="fixture",
        client="test",
        policy=Policy.build(scopes=["memory:read"]),
        actor=account.admin,
    )
    session = sessions.start_session(db, account.user, workspace)
    link = sessions.create_login_link(db, account.user, workspace_ref=workspace)
    db._conn.execute("INSERT OR IGNORE INTO oauth_clients VALUES ('client','test','[]',0)")
    for kind in ("access", "refresh"):
        db._conn.execute(
            "INSERT INTO oauth_tokens VALUES (?,?, 'client',?, '', '',0,9999999999,NULL,NULL)",
            (cid + kind, kind, cid),
        )
    db._conn.execute(
        "INSERT INTO oauth_codes VALUES (?, 'client',?, '', '', '', '',9999999999,NULL)",
        (cid, cid),
    )
    db._conn.execute(
        "INSERT INTO oauth_person_grants(connection_id,capability,user_id,workspace_id,"
        "client_id,created_at) VALUES (?,'test',?,?,'client',0)",
        (cid, account.user, workspace),
    )
    db._conn.execute(
        "INSERT INTO oauth_requests VALUES (?, '{}','', '', '',?,?,9999999999)",
        (cid, account.user, workspace),
    )
    return cid, pat, session, link


def test_member_remove_command_exists_and_removes(account):
    assert remove(account, "--yes", "--reason", "test cleanup") == 0
    assert account.db.role(account.workspace, account.user) is None
    row = account.db.audit_entries()[0]
    assert row["action"] == "member.remove"
    assert row["user_id"] == account.admin and row["workspace_id"] == account.workspace
    assert json.loads(row["target_ids"]) == [account.user]
    assert row["reason"] == "test cleanup" and row["at"] and row["auth"] == "cli"


def test_user_delete_command_exists_and_deletes(account):
    account.db._conn.execute("DELETE FROM memberships WHERE user_id=?", (account.user,))
    assert delete(account, "--yes", "--reason", "test cleanup") == 0
    with pytest.raises(ControlError, match="no such user"):
        account.db.user(account.user)
    row = account.db.audit_entries()[0]
    assert row["action"] == "user.delete" and row["user_id"] == account.admin
    assert json.loads(row["target_ids"]) == [account.user]
    assert row["reason"] == "test cleanup" and row["at"] and row["auth"] == "cli"


def test_removal_only_revokes_selected_workspace_and_keeps_content(account):
    db = account.db
    other = db.create_workspace("other", account.admin)
    db.add_member(other, account.user, "member", actor=account.admin)
    gone = credentials(account, account.workspace)
    kept = credentials(account, other)
    # Workspace stores are independent databases. Keep exact bytes and attribution.
    content = account.root / "workspaces" / account.workspace
    content.mkdir(parents=True)
    memory = content / "memory.txt"
    memory.write_bytes(b"verbatim\x00 memory\n")
    with sqlite3.connect(content / "boards.sqlite3") as cards:
        cards.execute("CREATE TABLE tasks(id INTEGER PRIMARY KEY, content TEXT, actor TEXT)")
        cards.execute("INSERT INTO tasks VALUES (1,'verbatim card',?)", ("user:" + account.user,))
    before = {p: p.read_bytes() for p in content.iterdir()}
    old_audit = [tuple(r) for r in db.audit_entries()]
    assert remove(account, "--yes") == 0
    assert db.authenticate_token(gone[1]) is None
    assert sessions.session_principal(db, gone[2]) is None
    assert db.authenticate_token(kept[1]).user_id == account.user
    assert sessions.session_principal(db, kept[2]).workspace_id == other
    for table in ("oauth_codes", "oauth_tokens", "oauth_person_grants"):
        assert (
            db._conn.execute(
                f"SELECT count(*) FROM {table} WHERE connection_id=?", (gone[0],)
            ).fetchone()[0]
            == 0
        )
        assert (
            db._conn.execute(
                f"SELECT count(*) FROM {table} WHERE connection_id=?", (kept[0],)
            ).fetchone()[0]
            > 0
        )
    for table in ("sessions", "login_links", "oauth_requests"):
        assert (
            db._conn.execute(
                f"SELECT count(*) FROM {table} WHERE user_id=? AND workspace_id=?",
                (account.user, account.workspace),
            ).fetchone()[0]
            == 0
        )
        assert (
            db._conn.execute(
                f"SELECT count(*) FROM {table} WHERE user_id=? AND workspace_id=?",
                (account.user, other),
            ).fetchone()[0]
            > 0
        )
    assert db.user(account.user)["id"] == account.user
    assert {p: p.read_bytes() for p in content.iterdir()} == before
    assert [tuple(r) for r in db.audit_entries()][1:] == old_audit
    assert db._conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_last_owner_and_force_owner_rules(account, capsys):
    assert remove(account, "--yes", "--force-owner", user=account.admin) == 2
    assert "last owner" in capsys.readouterr().err
    account.db._conn.execute(
        "UPDATE memberships SET role='owner' WHERE workspace_id=? AND user_id=?",
        (account.workspace, account.user),
    )
    assert remove(account, "--yes") == 2
    assert "--force-owner" in capsys.readouterr().err
    assert remove(account, "--yes", "--force-owner") == 0
    assert account.db.role(account.workspace, account.admin) == "owner"


@pytest.mark.parametrize("operation", ["member", "user"])
def test_explicit_active_instance_admin_required(account, operation, capsys):
    command = (
        ["member", "remove", account.workspace, account.user]
        if operation == "member"
        else ["user", "delete", account.user]
    )
    before = dump(account.db)
    assert invoke(account, *command, "--yes") == 2
    assert "--by" in capsys.readouterr().err
    assert invoke(account, *command, "--by", account.user, "--yes") == 2
    assert "admin" in capsys.readouterr().err.lower()
    assert dump(account.db) == before
    account.db._conn.execute("UPDATE users SET disabled_at='disabled' WHERE id=?", (account.admin,))
    assert invoke(account, *command, "--by", account.admin, "--yes") == 2
    assert "admin" in capsys.readouterr().err.lower()


def test_default_dry_runs_and_idempotence(account, capsys):
    credentials(account, account.workspace)
    before = dump(account.db)
    assert remove(account) == 0
    assert "dry-run" in capsys.readouterr().out.lower()
    assert dump(account.db) == before
    assert remove(account, "--yes") == 0
    after = dump(account.db)
    assert remove(account, "--yes") == 0
    assert dump(account.db) == after
    assert delete(account) == 0
    out = capsys.readouterr().out.lower()
    assert "dry-run" in out and "users" in out and "sessions" in out and "login_links" in out
    assert dump(account.db) == after
    assert delete(account, "--yes") == 0
    after = dump(account.db)
    assert delete(account, "--yes") == 0
    assert delete(account, "--yes", user="test@example.invalid") == 0
    assert dump(account.db) == after


@pytest.mark.parametrize("email", ["reserved@example.test", "reserved@EXAMPLE.INVALID"])
def test_reserved_domains_allow_delete(account, email):
    uid = account.db.create_user(email)
    assert delete(account, "--yes", user=uid) == 0


@pytest.mark.parametrize(
    "email", ["test@example.org", "test@example.invalid.attacker.test", "test@sub.example.invalid"]
)
def test_non_disposable_refused(account, capsys, email):
    uid = account.db.create_user(email)
    before = dump(account.db)
    assert delete(account, "--yes", user=uid) == 2
    assert "account-deletion" in capsys.readouterr().err
    assert dump(account.db) == before


def test_disposable_marker_is_explicit_at_user_add(account):
    assert (
        invoke(account, "user", "add", "marked@example.org", "--disposable", "--no-workspace") == 0
    )
    uid = account.db.user("marked@example.org")["id"]
    assert account.db.user(uid)["is_disposable"] == 1
    assert delete(account, "--yes", user=uid) == 0
    assert account.db.user(account.admin)["is_disposable"] == 0


def test_removal_accepts_email_and_preserves_other_members_credentials(account):
    db = account.db
    _, token = db.create_token(
        workspace_ref=account.workspace,
        user_ref=account.admin,
        name="admin-fixture",
        client="test",
        policy=Policy.build(scopes=["memory:read"]),
        actor=account.admin,
    )
    assert remove(account, "--yes", user="TEST@EXAMPLE.INVALID") == 0
    assert db.authenticate_token(token).user_id == account.admin


def test_unjoined_disposable_user_removes_signin_and_plan_records(account):
    db = account.db
    uid = db.create_user("cleanup@example.test")
    db._conn.executescript(
        "CREATE TABLE user_identities(provider TEXT, subject TEXT, user_id TEXT REFERENCES users(id));"
        "CREATE TABLE email_challenges(token_hash TEXT, email TEXT);"
        "CREATE TABLE ingest_cadence(account_id TEXT REFERENCES users(id));"
    )
    db._conn.execute("INSERT INTO user_identities VALUES ('test','fixture',?)", (uid,))
    db._conn.execute("INSERT INTO email_challenges VALUES ('fixture','CLEANUP@EXAMPLE.TEST')")
    db._conn.execute("INSERT INTO ingest_cadence VALUES (?)", (uid,))
    db._conn.execute("INSERT INTO account_plans VALUES (?, 'free', ?, 'now')", (uid, account.admin))
    db._conn.execute("INSERT INTO transcription_usage VALUES (?, 'today', 1)", (uid,))
    assert delete(account, "--yes", user="CLEANUP@EXAMPLE.TEST") == 0
    for table in (
        "user_identities",
        "email_challenges",
        "ingest_cadence",
        "account_plans",
        "transcription_usage",
    ):
        assert db._conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_empty_retained_personal_workspace_keeps_content(account, capsys):
    db = account.db
    uid = db.create_user("empty@example.test")
    wid = db.create_workspace("retained", uid, personal=True)
    # Legacy empty workspace with no membership: its contents must still survive.
    db._conn.execute("DELETE FROM memberships WHERE user_id=?", (uid,))
    assert delete(account, user=uid) == 0
    assert json.loads(capsys.readouterr().out)["detach_personal_workspaces"] == 1
    assert db.workspace(wid)["personal_of"] == uid
    assert delete(account, "--yes", user=uid) == 0
    assert db.workspace(wid)["personal_of"] is None


def test_transferred_personal_workspace_with_members_refuses_user_deletion(account, capsys):
    db = account.db
    wid = db.create_workspace("retained personal", account.user, personal=True)
    db.add_member(wid, account.admin, "owner", actor=account.user)
    assert remove(account, "--yes", "--force-owner", workspace=wid) == 0
    assert remove(account, "--yes") == 0
    assert delete(account, "--yes") == 2
    assert "owns a workspace" in capsys.readouterr().err
    assert db.workspace(wid)["personal_of"] == account.user


def test_last_instance_admin_cannot_be_deleted(account, capsys):
    db = account.db
    db._conn.execute("UPDATE memberships SET role='owner' WHERE user_id=?", (account.user,))
    assert remove(account, "--yes", "--force-owner", user=account.admin) == 0
    assert delete(account, "--yes", user=account.admin) == 2
    assert "last instance admin" in capsys.readouterr().err


def test_owner_with_other_members_and_remaining_membership_refused(account, capsys):
    account.db._conn.execute(
        "UPDATE memberships SET role='owner' WHERE workspace_id=? AND user_id=?",
        (account.workspace, account.user),
    )
    before = dump(account.db)
    assert delete(account, "--yes") == 2
    error = capsys.readouterr().err
    assert "owns" in error and "account-deletion" in error
    assert dump(account.db) == before
    account.db._conn.execute(
        "UPDATE memberships SET role='member' WHERE workspace_id=? AND user_id=?",
        (account.workspace, account.user),
    )
    assert delete(account, "--yes") == 2
    error = capsys.readouterr().err
    assert "membership" in error and "account-deletion" in error


def test_user_delete_removes_credentials_in_one_transaction(account, monkeypatch):
    db = account.db
    creds = credentials(account, account.workspace)
    # Model a legacy database with dangling connections. Only fixture construction
    # disables foreign keys; the deletion must enable and satisfy every check.
    db._conn.execute("PRAGMA foreign_keys=OFF")
    db._conn.execute("DELETE FROM memberships WHERE user_id=?", (account.user,))
    db._conn.execute("PRAGMA foreign_keys=ON")
    db._conn.execute(
        "INSERT INTO sessions VALUES ('stale',?,?, '', '',NULL)", (account.user, account.workspace)
    )
    db._conn.execute(
        "INSERT INTO login_links VALUES ('stale',?,?, '', '',NULL)",
        (account.user, account.workspace),
    )
    before = dump(db)
    original = ControlDB.audit

    def crash(self, **fields):
        if fields["action"] == "user.delete":
            assert self._conn.in_transaction
            assert (
                self._conn.execute("SELECT 1 FROM users WHERE id=?", (account.user,)).fetchone()
                is None
            )
            raise RuntimeError("simulated crash")
        return original(self, **fields)

    with monkeypatch.context() as patch:
        patch.setattr(ControlDB, "audit", crash)
        with pytest.raises(RuntimeError, match="simulated crash"):
            delete(account, "--yes")
    assert dump(db) == before
    assert not db._conn.in_transaction
    assert delete(account, "--yes") == 0
    for table in ("users", "sessions", "login_links", "oauth_requests", "oauth_person_grants"):
        column = "id" if table == "users" else "user_id"
        assert (
            db._conn.execute(f"SELECT 1 FROM {table} WHERE {column}=?", (account.user,)).fetchone()
            is None
        )
    assert db._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db._conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert db.authenticate_token(creds[1]) is None
    for table in ("oauth_tokens", "oauth_codes", "connections"):
        assert db._conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_member_remove_rolls_back_on_audit_failure(account, monkeypatch):
    credentials(account, account.workspace)
    before = dump(account.db)

    def crash(self, **fields):
        assert fields["action"] == "member.remove"
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(ControlDB, "audit", crash)
    with pytest.raises(RuntimeError, match="simulated crash"):
        remove(account, "--yes")
    assert dump(account.db) == before


def test_unknown_foreign_key_dependencies_refuse_and_rollback(account, capsys):
    assert remove(account, "--yes") == 0
    account.db._conn.execute(
        "CREATE TABLE retained(user_id TEXT REFERENCES users(id), content TEXT)"
    )
    account.db._conn.execute("INSERT INTO retained VALUES (?, 'keep verbatim')", (account.user,))
    before = dump(account.db)
    assert delete(account, "--yes") == 2
    assert "account-deletion" in capsys.readouterr().err
    assert dump(account.db) == before


def test_parent_connections_and_runner_bindings_cleaned_atomically(account):
    db = account.db
    cid, *_ = credentials(account, account.workspace)
    child, _ = db.create_token(
        workspace_ref=account.workspace,
        user_ref=account.user,
        name="child",
        client="test",
        policy=Policy.build(scopes=["memory:read"]),
        actor=account.admin,
        parent=cid,
    )
    db._conn.execute("INSERT INTO runner_connections VALUES (?, 'fixture', 'worker')", (child,))
    assert remove(account, "--yes") == 0
    assert db._conn.execute("SELECT count(*) FROM connections").fetchone()[0] == 0
    assert db._conn.execute("SELECT count(*) FROM runner_connections").fetchone()[0] == 0
    assert db._conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_other_users_child_connection_is_not_detached_or_deleted(account, capsys):
    db = account.db
    cid, *_ = credentials(account, account.workspace)
    db.create_token(
        workspace_ref=account.workspace,
        user_ref=account.admin,
        name="other-child",
        client="test",
        policy=Policy.build(scopes=["memory:read"]),
        actor=account.admin,
        parent=cid,
    )
    before = dump(db)
    assert remove(account, "--yes") == 2
    assert "account-deletion" in capsys.readouterr().err
    assert dump(db) == before


def test_disabled_second_owner_does_not_allow_removal(account, capsys):
    account.db._conn.execute("UPDATE memberships SET role='owner' WHERE user_id=?", (account.user,))
    account.db._conn.execute("UPDATE users SET disabled_at='disabled' WHERE id=?", (account.user,))
    assert remove(account, "--yes", "--force-owner", user=account.admin) == 2
    assert "last owner" in capsys.readouterr().err


def test_foreign_keys_may_not_be_disabled_for_cleanup(account):
    account.db._conn.execute("PRAGMA foreign_keys=OFF")
    before = dump(account.db)
    with pytest.raises(ControlError, match="foreign-key"):
        account.db.remove_member(
            account.workspace, account.user, actor=account.admin, dry_run=False
        )
    assert dump(account.db) == before
    account.db._conn.execute("PRAGMA foreign_keys=ON")


def test_backup_fence_and_offline_backup_refuse_delete(account, capsys):
    for lock in (fence_lock, data_lock):
        before = dump(account.db)
        with lock(account.root, exclusive=True):
            assert delete(account, "--yes") == 2
        assert "backup" in capsys.readouterr().err.lower()
        assert dump(account.db) == before


def test_pause_signal_refuses_delete_even_without_fence(account, monkeypatch, capsys):
    before = dump(account.db)
    monkeypatch.setattr(
        backup_pause, "answering", lambda **kw: {"busy": True, "data_dir": str(account.root)}
    )
    assert delete(account, "--yes") == 2
    assert "backup" in capsys.readouterr().err.lower()
    assert dump(account.db) == before


def test_audit_log_stays_append_only(account):
    before = [tuple(r) for r in account.db.audit_entries()]
    assert remove(account, "--yes") == 0
    assert delete(account, "--yes") == 0
    assert [tuple(r) for r in account.db.audit_entries()][2:] == before
    for sql in ("UPDATE audit_log SET reason='changed'", "DELETE FROM audit_log"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            account.db._conn.execute(sql)


def test_existing_database_marker_migration_is_additive_and_idempotent(tmp_path):
    path = str(tmp_path / "control.sqlite3")
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE users(id TEXT PRIMARY KEY,email TEXT UNIQUE COLLATE NOCASE,name TEXT NOT NULL DEFAULT '',is_admin INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,disabled_at TEXT)"
        )
        conn.execute(
            "INSERT INTO users VALUES ('legacy','legacy@example.org','legacy',0,'before',NULL)"
        )
    for _ in range(2):
        db = ControlDB(path)
        try:
            assert db.user("legacy")["is_disposable"] == 0
            assert db.user("legacy")["name"] == "legacy"
            assert db._conn.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            db.close()


def test_cleanup_cli_help_pins_deliberate_subcommands(capsys):
    parser = build_parser()
    for command, tokens in [
        (["user", "delete"], ["--by", "--yes", "--reason"]),
        (["member", "remove"], ["--force-owner", "--by", "--yes", "--reason"]),
        (["user", "add"], ["--disposable"]),
    ]:
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(command + ["--help"])
        assert exc.value.code == 0
        help_text = capsys.readouterr().out
        assert all(token in help_text for token in tokens)
