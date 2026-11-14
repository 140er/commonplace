"""Keyslot check, credential rotation and the recovery drill, on real SQLCipher."""

import hashlib
import json
import os
import sqlite3
from contextlib import closing

import pytest

from commonplace.backup import DataDirInUse, backup, data_lock, restore
from commonplace.cli import main
from commonplace.control_db import ControlDB
from commonplace.handoff.models import ThreadMessage
from commonplace.workspace_encryption import encrypt_workspace
from commonplace.workspace_keyslots import check_keyslot, rewrap_keyslot
from commonplace.workspace_store import open_threads, workspace_path
from mempalace import sqlite_crypto as crypto

TEXT = "verbatim snowman ☃\x00"


def credential(path):
    path.write_bytes(os.urandom(32))
    path.chmod(0o600)
    return path


def authorizer(data, user):
    def authorize(ref):
        with closing(ControlDB(str(data / "control.sqlite3"))) as db:
            row = db.workspace(ref)
            db.require_owner(row["id"], user)
            return row["id"]

    return authorize


@pytest.fixture
def encrypted(tmp_path, monkeypatch):
    """An encrypted workspace holding one verbatim message."""
    pytest.importorskip("sqlcipher3")
    data = tmp_path / "data"
    with closing(ControlDB(str(data / "control.sqlite3"))) as db:
        owner = db.create_user("owner@example.test")
        wid = db.create_workspace("Synthetic workspace", owner)
    server, recovery = credential(tmp_path / "server.key"), credential(tmp_path / "recovery.key")
    slots = tmp_path / "slots"
    monkeypatch.setenv("COMMONPLACE_KEY_STORE", str(slots))
    monkeypatch.setenv("COMMONPLACE_WRAPPING_KEY_FILE", str(server))
    monkeypatch.delenv("COMMONPLACE_KEY_SLOT", raising=False)
    encrypt_workspace(
        data,
        wid,
        backup_dir=tmp_path / "before-encryption",
        key_store=slots,
        server_key_file=server,
        recovery_key_file=recovery,
        authorize=authorizer(data, owner),
    )
    with closing(open_threads(workspace_path(data, wid))) as store:
        thread = store.store(
            wid,
            "synthetic-source",
            [ThreadMessage(0, "user", "message", TEXT, "raw:" + TEXT)],
            actor="person:synthetic",
        ).id
    return {
        "data": data,
        "owner": owner,
        "wid": wid,
        "server": server,
        "recovery": recovery,
        "slots": slots,
        "record": slots / (wid + ".json"),
        "thread": thread,
        "tmp": tmp_path,
    }


def rewrap(ws, slot, new, unlock_slot, unlock, *, data=None, by=None):
    data = data or ws["data"]
    return rewrap_keyslot(
        data,
        ws["wid"],
        key_store=ws["slots"],
        slot=slot,
        new_key_file=new,
        unlock_slot=unlock_slot,
        unlock_key_file=unlock,
        authorize=authorizer(data, by or ws["owner"]),
    )


def check(ws, slot, key_file, *, data=None):
    return check_keyslot(
        data or ws["data"],
        ws["wid"],
        key_store=ws["slots"],
        slot=slot,
        key_file=key_file,
        authorize=lambda ref: ref,
    )


def message(ws, data=None):
    with closing(open_threads(workspace_path(data or ws["data"], ws["wid"]))) as store:
        return store.messages(ws["thread"])[0].text


def database_digests(data):
    """Encrypted workspace files only; opening the plaintext control database rewrites it."""
    digests = {
        path.relative_to(data).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((data / "workspaces").rglob("*.sqlite3"))
    }
    assert digests
    return digests


def test_check_proves_each_credential_against_its_own_slot(encrypted):
    ws = encrypted
    record = ws["record"].read_bytes()
    for slot in ("server", "recovery"):
        result = check(ws, slot, ws[slot])
        assert result["state"] == "ok"
        assert result["slots"] == ["recovery", "server"]
        assert result["databases"] >= 1
    assert ws["record"].read_bytes() == record
    # A credential is bound to its slot; a stranger's credential opens nothing.
    for slot, key_file in (
        ("recovery", ws["server"]),
        ("server", ws["recovery"]),
        ("server", credential(ws["tmp"] / "stranger.key")),
    ):
        with pytest.raises(crypto.EncryptionError, match="keyslot could not be unlocked"):
            check(ws, slot, key_file)


def test_recovery_drill_restores_then_recreates_the_server_slot(encrypted, monkeypatch):
    """Server credential and slot are lost; a restored backup comes back on recovery only."""
    ws = encrypted
    snapshot, restored = ws["tmp"] / "snapshot", ws["tmp"] / "restored"
    backup(ws["data"], snapshot)
    restore(snapshot, restored)
    record = json.loads(ws["record"].read_text())
    del record["slots"]["server"]
    ws["record"].write_text(json.dumps(record))
    ws["server"].unlink()
    with pytest.raises(crypto.EncryptionError):
        message(ws, restored)
    assert check(ws, "recovery", ws["recovery"], data=restored)["slots"] == ["recovery"]

    before = database_digests(restored)
    recovery_slot = record["slots"]["recovery"]
    new_server = credential(ws["tmp"] / "server-new.key")
    result = rewrap(ws, "server", new_server, "recovery", ws["recovery"], data=restored)
    assert result["state"] == "rewrapped"
    assert result["slots"] == ["recovery", "server"]
    # The data key is unchanged: no database byte moved, the recovery slot is untouched.
    assert database_digests(restored) == before
    assert json.loads(ws["record"].read_text())["slots"]["recovery"] == recovery_slot
    assert ws["record"].stat().st_mode & 0o777 == 0o600

    monkeypatch.setenv("COMMONPLACE_WRAPPING_KEY_FILE", str(new_server))
    assert message(ws, restored) == TEXT
    # The original data directory and the older backup open with the new slot too.
    assert message(ws) == TEXT
    assert check(ws, "server", new_server)["state"] == "ok"
    assert check(ws, "recovery", ws["recovery"], data=restored)["state"] == "ok"


def test_rotation_retires_the_old_credential_for_the_current_record(encrypted, monkeypatch):
    ws = encrypted
    new_server = credential(ws["tmp"] / "server-new.key")
    new_recovery = credential(ws["tmp"] / "recovery-new.key")
    rewrap(ws, "server", new_server, "server", ws["server"])
    rewrap(ws, "recovery", new_recovery, "server", new_server)
    for slot, old in (("server", ws["server"]), ("recovery", ws["recovery"])):
        with pytest.raises(crypto.EncryptionError, match="keyslot could not be unlocked"):
            check(ws, slot, old)
    with pytest.raises(crypto.EncryptionError, match="keyslot could not be unlocked"):
        message(ws)
    monkeypatch.setenv("COMMONPLACE_WRAPPING_KEY_FILE", str(new_server))
    assert message(ws) == TEXT
    monkeypatch.setenv("COMMONPLACE_WRAPPING_KEY_FILE", str(new_recovery))
    monkeypatch.setenv("COMMONPLACE_KEY_SLOT", "recovery")
    assert message(ws) == TEXT


@pytest.mark.parametrize(
    "case, match",
    [
        ("wrong-unlock", "keyslot could not be unlocked"),
        ("same-credential", "must differ from every current slot key"),
        ("other-slot-credential", "must differ from every current slot key"),
        ("short-credential", "exactly 32 random bytes"),
        ("public-credential", "private regular file"),
        ("missing-credential", "Wrapping key file is unavailable"),
        ("credential-in-data", "outside the data directory"),
        ("missing-record", "Workspace keyslot is unavailable"),
        ("symlinked-record", "Keyslot file cannot be a symlink"),
        ("unknown-record-field", "unsupported format"),
        ("unknown-slot", "unsupported format"),
        ("non-owner", "owner"),
    ],
)
def test_rewrap_refusals_leave_the_record_untouched(encrypted, case, match):
    ws = encrypted
    original = ws["record"].read_bytes()
    new = credential(ws["tmp"] / "new.key")
    unlock, by = ws["recovery"], None
    error = crypto.EncryptionError
    if case == "wrong-unlock":
        unlock = credential(ws["tmp"] / "stranger.key")
    elif case == "same-credential":
        new, unlock = ws["server"], ws["server"]
    elif case == "other-slot-credential":
        new = ws["recovery"]
    elif case == "short-credential":
        new.write_bytes(os.urandom(31))
    elif case == "public-credential":
        new.chmod(0o644)
    elif case == "missing-credential":
        new.unlink()
    elif case == "credential-in-data":
        new = credential(ws["data"] / "new.key")
    elif case == "missing-record":
        ws["record"].unlink()
    elif case == "symlinked-record":
        moved = ws["tmp"] / "moved.json"
        ws["record"].rename(moved)
        ws["record"].symlink_to(moved)
    elif case in ("unknown-record-field", "unknown-slot"):
        record = json.loads(original)
        if case == "unknown-slot":
            record["slots"]["escrow"] = record["slots"]["recovery"]
        else:
            record["key_mode"] = "token-v2"
        ws["record"].write_text(json.dumps(record))
    elif case == "non-owner":
        with closing(ControlDB(str(ws["data"] / "control.sqlite3"))) as db:
            by = db.create_user("other@example.test")
        error = Exception
    current = None if case == "missing-record" else ws["record"].read_bytes()
    unlock_slot = "server" if unlock == ws["server"] else "recovery"
    with pytest.raises(error, match=match):
        rewrap(ws, "server", new, unlock_slot, unlock, by=by)
    if current is not None:
        assert ws["record"].read_bytes() == current
    else:
        assert not ws["record"].exists()
    if case in ("missing-record", "symlinked-record", "unknown-record-field", "unknown-slot"):
        return
    assert message(ws) == TEXT


def test_interrupted_publish_keeps_the_previous_record(encrypted, monkeypatch):
    from commonplace import workspace_keyslots as keyslots

    ws = encrypted
    original = ws["record"].read_bytes()

    def fail(*args, **kwargs):
        raise OSError("synthetic interruption")

    monkeypatch.setattr(keyslots, "atomic_write_json", fail)
    with pytest.raises(OSError, match="synthetic"):
        rewrap(ws, "server", credential(ws["tmp"] / "new.key"), "recovery", ws["recovery"])
    assert ws["record"].read_bytes() == original
    assert sorted(path.name for path in ws["slots"].iterdir()) == [ws["record"].name]
    assert message(ws) == TEXT


def test_rewrap_requires_the_service_to_be_stopped(encrypted):
    ws = encrypted
    original = ws["record"].read_bytes()
    with data_lock(ws["data"]), pytest.raises(DataDirInUse):
        rewrap(ws, "server", credential(ws["tmp"] / "new.key"), "recovery", ws["recovery"])
    assert ws["record"].read_bytes() == original


def test_key_that_opens_no_database_is_never_rewrapped(encrypted):
    """A record carrying a different data key under valid credentials is refused."""
    ws = encrypted
    record = json.loads(ws["record"].read_text())
    info = {name: record[name] for name in ("version", "workspace_id", "key_id")}
    forged = crypto.wrap_key(os.urandom(32), ws["recovery"].read_bytes(), info, "recovery")
    record["slots"]["recovery"] = forged
    ws["record"].write_text(json.dumps(record))
    current = ws["record"].read_bytes()
    with pytest.raises(crypto.EncryptionError, match="could not be opened with this key"):
        rewrap(ws, "server", credential(ws["tmp"] / "new.key"), "recovery", ws["recovery"])
    with pytest.raises(crypto.EncryptionError, match="could not be opened with this key"):
        check(ws, "recovery", ws["recovery"])
    assert ws["record"].read_bytes() == current
    assert message(ws) == TEXT


def test_operator_blind_marker_stays_locked(encrypted):
    """A workspace marked for token slots never reaches the server or recovery credential."""
    ws = encrypted
    config = workspace_path(ws["data"], ws["wid"]) / "workspace.json"
    settings = json.loads(config.read_text())
    settings["encryption"]["key_mode"] = "token-v2"
    config.write_text(json.dumps(settings))
    original = ws["record"].read_bytes()
    with pytest.raises(crypto.EncryptionError, match="B3 remains locked"):
        check(ws, "recovery", ws["recovery"])
    with pytest.raises(crypto.EncryptionError, match="B3 remains locked"):
        rewrap(ws, "server", credential(ws["tmp"] / "new.key"), "recovery", ws["recovery"])
    assert ws["record"].read_bytes() == original


def test_unencrypted_workspace_is_refused_and_still_opens_as_plain_sqlite(tmp_path, monkeypatch):
    pytest.importorskip("sqlcipher3")
    data = tmp_path / "data"
    with closing(ControlDB(str(data / "control.sqlite3"))) as db:
        owner = db.create_user("owner@example.test")
        wid = db.create_workspace("Plain workspace", owner)
    root = data / "workspaces" / wid
    root.mkdir(parents=True)
    (root / "workspace.json").write_text('{"store":"compact"}')
    # Key settings in the environment never change how a plaintext workspace opens.
    monkeypatch.setenv("COMMONPLACE_KEY_STORE", str(tmp_path / "absent"))
    monkeypatch.setenv("COMMONPLACE_WRAPPING_KEY_FILE", str(tmp_path / "absent.key"))
    with closing(crypto.connect(root / "threads.v2.sqlite3")) as conn:
        assert isinstance(conn, sqlite3.Connection)
        conn.execute("CREATE TABLE fixture(content TEXT)")
    assert (root / "threads.v2.sqlite3").read_bytes().startswith(b"SQLite format 3\x00")
    key_file = credential(tmp_path / "server.key")
    ws = {"data": data, "wid": wid, "owner": owner, "slots": tmp_path / "slots"}
    with pytest.raises(crypto.EncryptionError, match="Workspace is not encrypted"):
        check(ws, "server", key_file)
    with pytest.raises(crypto.EncryptionError, match="Workspace is not encrypted"):
        rewrap(ws, "server", credential(tmp_path / "new.key"), "server", key_file)
    assert not (tmp_path / "slots").exists()


def test_cli_never_prints_key_material(encrypted, capsys, caplog):
    ws = encrypted
    record = json.loads(ws["record"].read_text())
    info = {name: record[name] for name in ("version", "workspace_id", "key_id")}
    new_server = credential(ws["tmp"] / "server-new.key")
    stranger = credential(ws["tmp"] / "stranger.key")
    secrets = [path.read_bytes() for path in (ws["server"], ws["recovery"], new_server, stranger)]
    secrets.append(crypto.unwrap_key(record, secrets[0], info, "server"))
    base = ["--data", str(ws["data"]), "workspace"]
    store = ["--key-store", str(ws["slots"])]

    def keyslot_check(slot, key_file):
        return main(
            [*base, "keyslot-check", ws["wid"], *store, "--slot", slot, "--key-file", str(key_file)]
        )

    def keyslot_rewrap(new, unlock_slot, unlock):
        return main(
            [
                *base,
                "keyslot-rewrap",
                ws["wid"],
                "--by",
                ws["owner"],
                *store,
                "--slot",
                "server",
                "--new-key-file",
                str(new),
                "--unlock-slot",
                unlock_slot,
                "--unlock-key-file",
                str(unlock),
            ]
        )

    assert keyslot_check("recovery", ws["recovery"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "databases": 1,
        "key_id": info["key_id"],
        "slot": "recovery",
        "slots": ["recovery", "server"],
        "state": "ok",
    }
    assert keyslot_check("server", stranger) == 2
    assert capsys.readouterr().err == "commonplace: Workspace keyslot could not be unlocked\n"
    assert keyslot_rewrap(new_server, "recovery", stranger) == 2
    assert capsys.readouterr().err == "commonplace: Workspace keyslot could not be unlocked\n"
    assert keyslot_rewrap(ws["recovery"], "recovery", ws["recovery"]) == 2
    refused = capsys.readouterr().err
    assert "must differ from every current slot key" in refused
    assert keyslot_rewrap(new_server, "recovery", ws["recovery"]) == 0
    rewrapped = capsys.readouterr()
    assert json.loads(rewrapped.out)["state"] == "rewrapped"
    assert keyslot_check("server", ws["server"]) == 2
    assert keyslot_check("server", new_server) == 0
    last = capsys.readouterr()
    printed = refused + rewrapped.out + rewrapped.err + last.out + last.err + caplog.text
    stored = ws["record"].read_text()
    for secret in secrets:
        assert secret.hex() not in printed
        assert secret.hex() not in stored
