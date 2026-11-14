"""Chat-platform bridge, identity slice: link codes (DM only, hashed, single use, tries), links
between a platform identity and a person, unlink, the owner and person routes, and the per-event
membership re-check. Over real HTTP with the in-memory fake platform of the foundation tests."""

import io
import json
import logging
import sqlite3
import threading
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from commonplace.chat_bridge import links
from tests.test_chat_bridge_foundation import (  # noqa: F401 - fixtures
    SENTINEL,
    _enabled_install,
    audit_rows,
    bridge_fixture,
    everything_on_disk,
    fake_fixture,
)
from tests.test_commonplace_boards import mcp

NOW = datetime.now(timezone.utc).replace(microsecond=0)  # codes are issued on the real clock


def at(minutes):
    return NOW + timedelta(minutes=minutes)


class Identity:
    """Helpers over the bridge hub: a second member, issuing and presenting codes."""

    def __init__(self, hub):
        self.hub = hub
        self.ws = hub.hub.workspace
        self.third_user = hub.db.create_user("third@example.com")
        hub.db.add_member(self.ws, self.third_user, "member", actor=hub.hub.admin)
        self.third = hub.cookie("third@example.com", self.ws)

    def code(self, install, auth=None):
        status, body, _ = self.hub.post(
            auth or self.hub.member, f"/api/bridge/installs/{install}/link-code", {}
        )
        assert status == 201, (status, body)
        return body["code"]

    def redeem(self, install, ext_user, text, kind="dm", now=NOW):
        return links.redeem(self.hub.hub.gateway, self.ws, install, ext_user, kind, text, now)

    def link(self, install, ext_user="U-MEMBER", auth=None):
        done = self.redeem(install, ext_user, self.code(install, auth))
        assert done.code == "linked", done
        return done.link_id

    def resolve(self, install, ext_user):
        return links.resolve(self.hub.hub.gateway, self.ws, install, ext_user)

    def db(self):
        return sqlite3.connect(self.hub.file())

    def leave(self, user_id):
        with self.hub.db._lock:
            conn = self.hub.db._conn
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute(
                "DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (self.ws, user_id)
            )
            conn.execute("PRAGMA foreign_keys=ON")
            conn.commit()


@pytest.fixture(name="ident")
def ident_fixture(bridge, fake):
    return Identity(bridge)


@pytest.fixture(name="install")
def install_fixture(bridge, fake):
    return _enabled_install(bridge, "T-ONE")


# --- default off ---------------------------------------------------------------------------------


def test_clean_workspace_nothing_exists_nothing_is_created_no_worker_starts(bridge, fake):
    threads = threading.active_count()
    ghost = "0" * 32
    for caller in (bridge.member, bridge.owner, bridge.t1, bridge.tm):
        status, body, _ = bridge.get(caller, "/api/bridge/links")
        assert (status, body["code"]) == (404, "not_found"), caller
        status, body, _ = bridge.request(caller, "DELETE", f"/api/bridge/links/{ghost}")
        assert (status, body["code"]) == (404, "not_found"), caller
        status, body, _ = bridge.post(caller, f"/api/bridge/installs/{ghost}/link-code", {})
        assert (status, body["code"]) == (404, "not_found"), caller
    assert bridge.get(None, "/api/bridge/links")[0] == 401
    status, body, _ = bridge.get(bridge.owner, f"/api/bridge/installs/{ghost}/links")
    assert (status, body["code"]) == (404, "not_found")
    status, body, _ = bridge.request(
        bridge.owner, "DELETE", f"/api/bridge/installs/{ghost}/links/{ghost}"
    )
    assert (status, body["code"]) == (404, "not_found")
    # Intake helpers on a workspace without a file answer "off" and create nothing.
    gateway = bridge.hub.gateway
    ident = links.redeem(gateway, bridge.hub.workspace, ghost, "U1", "dm", "ABCD-EFGH-JKMN", NOW)
    assert ident.code == "off"
    assert links.resolve(gateway, bridge.hub.workspace, ghost, "U1").reason == "off"
    assert links.redeem(gateway, bridge.hub.workspace, ghost, "U1", "dm", "hello", NOW).code == (
        "not_a_code"
    )
    assert not bridge.file().exists()
    assert audit_rows(bridge) == []
    assert threading.active_count() == threads
    # No agent tool gained anything: the tool lists are what they were.
    for token in (bridge.hub.owner_token("agent-owner"), bridge.tm, bridge.t1):
        assert not [t for t in bridge.hub.tool_names(token) if t.startswith("bridge")]


def test_an_install_that_is_off_cannot_issue_or_redeem(bridge, fake, ident):
    install = bridge.create()[1]["install"]["id"]  # created, never enabled
    status, body, _ = bridge.post(bridge.member, f"/api/bridge/installs/{install}/link-code", {})
    assert (status, body["code"]) == (404, "not_found")
    assert bridge.get(bridge.member, "/api/bridge/links")[1]["available"] == []
    assert ident.redeem(install, "U1", "ABCD-EFGH-JKMN").code == "off"
    assert ident.resolve(install, "U1").reason == "off"
    assert ident.db().execute("SELECT COUNT(*) FROM bridge_links").fetchone()[0] == 0
    assert ident.db().execute("SELECT COUNT(*) FROM bridge_link_codes").fetchone()[0] == 0


# --- codes ---------------------------------------------------------------------------------------


def test_a_code_works_once_and_only_in_a_direct_message(bridge, ident, install):
    first = ident.code(install)
    # Sent in a channel (a mention): refused and burned, even by the right person.
    assert ident.redeem(install, "U-MEMBER", first, kind="mention").code == "channel"
    assert ident.redeem(install, "U-MEMBER", first).code == "invalid"
    second = ident.code(install)
    done = ident.redeem(install, "U-MEMBER", second)
    assert (done.code, done.text) == ("linked", links.REDEEM_TEXT["linked"])
    # Once means once: the same code from anyone, or again, does not work.
    assert ident.redeem(install, "U-OTHER", second).code == "invalid"
    assert ident.redeem(install, "U-MEMBER", second).code == "invalid"
    # Any non-DM kind burns.
    third = ident.code(install, ident.third)
    assert ident.redeem(install, "U-THIRD", third, kind="other").code == "channel"
    assert ident.redeem(install, "U-THIRD", third).code == "invalid"
    assert ident.resolve(install, "U-THIRD").reason == "unlinked"


def test_expired_wrong_and_foreign_install_codes_fail(bridge, ident, install):
    code = ident.code(install)
    assert ident.redeem(install, "U1", code, now=at(15)).code == "invalid"  # 10 minutes only
    # A code is bound to its install: a second install does not accept it.
    other = _enabled_install(bridge, "T-TWO")
    fresh = ident.code(install)
    assert ident.redeem(other, "U1", fresh).code == "invalid"
    assert ident.redeem(install, "U1", fresh).code == "linked"
    assert ident.resolve(other, "U1").reason == "unlinked"


def test_codes_are_shaped_and_a_new_code_ends_the_earlier_one(bridge, ident, install):
    first = ident.code(install)
    assert len(first.replace("-", "")) == links.CODE_LENGTH and first.count("-") == 2
    second = ident.code(install)
    assert second != first
    assert ident.redeem(install, "U1", first).code == "invalid"
    # Spacing, case, dashes and markup around the code do not matter.
    sloppy = "`" + second.lower().replace("-", " ") + "`"
    assert links.normalize(sloppy) == second.replace("-", "")
    assert ident.redeem(install, "U1", sloppy).code == "linked"
    for text in ("hello", "", "ABCD-EFGH-JKM", "ABCD-EFGH-JKMNP", "ABCD-EFGH-JKM0", None, 5):
        assert links.normalize(text) is None, text


def test_text_that_is_not_a_code_costs_no_tries(bridge, ident, install):
    for _ in range(20):
        assert ident.redeem(install, "U1", "hello there").code == "not_a_code"
    assert ident.db().execute("SELECT COUNT(*) FROM bridge_link_tries").fetchone()[0] == 0
    assert ident.redeem(install, "U1", ident.code(install)).code == "linked"


def test_wrong_codes_count_tries_and_lock_the_sender(bridge, ident, install):
    good = ident.code(install)
    wrong = "ABCD-EFGH-JKMN"
    results = [ident.redeem(install, "U-GUESS", wrong, now=at(i * 0.1)).code for i in range(5)]
    assert results == ["invalid"] * 4 + ["locked"]
    # Locked: even the right code from this sender is refused, and nothing is spent.
    assert ident.redeem(install, "U-GUESS", good, now=at(1)).code == "locked"
    assert ident.db().execute("SELECT used_at FROM bridge_link_codes").fetchone()[0] is None
    # Another sender is not affected.
    assert ident.redeem(install, "U-OTHER", ident.code(install, ident.third), now=at(1)).code == (
        "linked"
    )
    # The lock ends by itself: a code issued later works for the same sender.
    assert ident.redeem(install, "U-GUESS", wrong, now=at(16)).code == "invalid"
    later = links.issue(bridge.hub.gateway, bridge.person(bridge.member_user), install, now=at(15))
    assert ident.redeem(install, "U-GUESS", later["code"], now=at(16)).code == "linked"
    assert [r["tool"] for r in audit_rows(bridge)].count("code_locked") == 1


def test_tries_are_forgotten_after_a_quiet_period_and_per_sender(bridge, ident, install):
    wrong = "ABCD-EFGH-JKMN"
    for i in range(4):
        assert ident.redeem(install, "U1", wrong, now=at(i)).code == "invalid"
    assert ident.redeem(install, "U2", wrong, now=at(4)).code == "invalid"  # separate counter
    # Fifteen quiet minutes later the count starts over.
    for i in range(4):
        assert ident.redeem(install, "U1", wrong, now=at(30 + i)).code == "invalid"
    assert ident.redeem(install, "U1", wrong, now=at(34)).code == "locked"


def test_a_code_that_cannot_finish_is_dead_after_five_presentations(bridge, ident, install):
    ident.link(install, "U-MEMBER")  # the member is linked already
    other = _enabled_install(bridge, "T-TWO")
    stale = ident.code(other)
    # The sender's platform id belongs to someone else on this install.
    third = ident.code(install, ident.third)
    for _ in range(5):
        assert ident.redeem(install, "U-MEMBER", third).code == "already_linked"
    assert ident.redeem(install, "U-THIRD", third).code == "invalid"
    assert ident.redeem(other, "U-X", stale).code == "linked"


# --- codes are secrets ---------------------------------------------------------------------------


def test_a_code_is_stored_hashed_and_never_appears_after_issue(bridge, ident, install, caplog):
    caplog.set_level(logging.DEBUG)
    seen = []

    def keep(result):
        seen.append(result[2])
        return result

    status, body, raw = bridge.post(bridge.member, f"/api/bridge/installs/{install}/link-code", {})
    assert status == 201 and raw.count(body["code"].encode()) == 1  # shown here, once
    code = body["code"]
    plain = code.replace("-", "")
    keep(bridge.get(bridge.member, "/api/bridge/links"))
    keep(bridge.get(bridge.owner, f"/api/bridge/installs/{install}/links"))
    keep(bridge.get(bridge.owner, "/api/bridge/installs"))
    # Wrong tries, a channel presentation, a lock and the successful redeem.
    for text, kind in (("ABCD-EFGH-JKMN", "dm"), (code, "mention")):
        done = ident.redeem(install, "U1", text, kind)
        seen.append(json.dumps([done.code, done.text]).encode())
    fresh = ident.code(install)
    done = ident.redeem(install, "U-MEMBER", fresh)
    seen.append(json.dumps([done.code, done.text]).encode())
    plain_fresh = fresh.replace("-", "")
    keep(bridge.get(bridge.member, "/api/bridge/links"))
    ok, text = mcp(bridge.hub, bridge.hub.owner_token("agent-owner"), "bridge_status")
    assert ok
    seen.append(text.encode())
    needles = [n.encode() for n in (code, plain, fresh, plain_fresh)]
    for raw in seen:
        assert not any(n in raw for n in needles), raw
    for row in audit_rows(bridge):
        assert not any(n.decode() in json.dumps(row) for n in needles)
    assert not any(n.decode() in record.getMessage() for record in caplog.records for n in needles)
    disk = everything_on_disk(bridge)
    assert not any(n in disk for n in needles)
    # What is stored is a 64-hex digest salted by the install: the live code's, not the code.
    stored = {r[0] for r in ident.db().execute("SELECT code_hash FROM bridge_link_codes")}
    assert links.digest(install, plain) in stored and links.digest(install, plain_fresh) in stored
    assert all(len(h) == 64 and set(h) <= set("0123456789abcdef") for h in stored)
    assert links.digest(install, plain) != links.digest("another-install", plain)


def test_audit_rows_carry_ids_and_fixed_codes_not_platform_ids(bridge, ident, install):
    link = ident.link(install, "U-SENTINEL-PLATFORM-ID")
    ident.redeem(install, "U-SENTINEL-PLATFORM-ID", "ABCD-EFGH-JKMN")
    bridge.request(bridge.member, "DELETE", f"/api/bridge/links/{link}")
    rows = audit_rows(bridge)
    tools = [r["tool"] for r in rows]
    for expected in ("link_code", "link", "unlink"):
        assert expected in tools, tools
    blob = json.dumps(rows)
    assert "U-SENTINEL-PLATFORM-ID" not in blob and "ABCD" not in blob
    (made,) = [r for r in rows if r["tool"] == "link"]
    assert json.loads(made["target_ids"]) == [install, link]
    assert made["user_id"] == bridge.member_user


# --- links ---------------------------------------------------------------------------------------


def test_a_link_belongs_to_one_person_and_one_install(bridge, ident, install):
    link = ident.link(install, "U-MEMBER")
    # One platform id, one person here: another person cannot take it over, and their code is kept.
    third_code = ident.code(install, ident.third)
    assert ident.redeem(install, "U-MEMBER", third_code).code == "already_linked"
    assert ident.resolve(install, "U-MEMBER").link["id"] == link
    assert ident.resolve(install, "U-MEMBER").principal.user_id == bridge.member_user
    # One person, one platform id here: no second code while linked.
    status, body, _ = bridge.post(bridge.member, f"/api/bridge/installs/{install}/link-code", {})
    assert (status, body["code"]) == (409, "already_linked")
    # The code is still usable by the right sender for the right person.
    assert ident.redeem(install, "U-THIRD", third_code).code == "linked"
    # The same platform id is a different identity on another install.
    other = _enabled_install(bridge, "T-TWO")
    assert ident.link(other, "U-MEMBER") != link
    assert ident.resolve(other, "U-MEMBER").principal.user_id == bridge.member_user
    # The store holds the rule too, not only the service.
    db = ident.db()
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO bridge_links (id,install_id,ext_user,user_id,created_at)"
            " VALUES ('x',?,'U-NEW',?,'t')",
            (install, bridge.member_user),
        )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO bridge_links (id,install_id,ext_user,user_id,created_at)"
            " VALUES ('y',?,'U-MEMBER','someone','t')",
            (install,),
        )


def test_unlinking_ends_the_link_at_once_and_it_can_be_made_again(bridge, ident, install):
    link = ident.link(install, "U-MEMBER")
    db = ident.db()
    db.execute(
        "INSERT INTO bridge_prompts (pending,install_id,ext_user,ext_channel)"
        " VALUES ('p1',?,'U-MEMBER','D1'), ('p2',?,'U-ELSE','D2')",
        (install, install),
    )
    db.commit()
    assert ident.resolve(install, "U-MEMBER").reason == "ok"
    status, body, _ = bridge.request(bridge.member, "DELETE", f"/api/bridge/links/{link}")
    assert (status, body) == (200, {"ok": True})
    after = ident.resolve(install, "U-MEMBER")
    assert (after.principal, after.reason) == (None, "unlinked")
    assert bridge.get(bridge.member, "/api/bridge/links")[1]["links"] == []
    # Open confirmations of that platform id are cancelled, others are not.
    assert [r[0] for r in ident.db().execute("SELECT pending FROM bridge_prompts")] == ["p2"]
    # Ending it twice is a 404; linking again (even to another platform id) works.
    assert bridge.request(bridge.member, "DELETE", f"/api/bridge/links/{link}")[0] == 404
    again = ident.link(install, "U-NEW")
    assert again != link
    assert ident.resolve(install, "U-NEW").principal.user_id == bridge.member_user
    assert ident.resolve(install, "U-MEMBER").reason == "unlinked"


def test_a_person_sees_and_ends_only_their_own_links(bridge, ident, install):
    mine = ident.link(install, "U-MEMBER")
    theirs = ident.link(install, "U-THIRD", ident.third)
    status, body, _ = bridge.get(bridge.member, "/api/bridge/links")
    assert status == 200
    assert [(i["id"], i["ext_user"], i["confirm_ok"]) for i in body["links"]] == [
        (mine, "U-MEMBER", False)
    ]
    assert body["available"] == [{"id": install, "platform": "fake", "label": "Team chat"}]
    assert theirs not in json.dumps(body) and "U-THIRD" not in json.dumps(body)
    # Another person's link is a plain 404, the same as one that does not exist; nothing changes.
    for auth in (bridge.member, bridge.owner):
        status, body, _ = bridge.request(auth, "DELETE", f"/api/bridge/links/{theirs}")
        assert (status, body["code"]) == (404, "not_found")
    assert bridge.request(bridge.member, "DELETE", f"/api/bridge/links/{'0' * 32}")[0] == 404
    assert ident.resolve(install, "U-THIRD").reason == "ok"
    assert bridge.request(ident.third, "DELETE", f"/api/bridge/links/{theirs}")[0] == 200
    # Agents and tokens never reach the person routes.
    for token in (bridge.tm, bridge.t1):
        assert bridge.get(token, "/api/bridge/links")[0] == 404
        assert bridge.request(token, "DELETE", f"/api/bridge/links/{mine}")[0] == 404
        assert bridge.post(token, f"/api/bridge/installs/{install}/link-code", {})[0] == 404
    # Another workspace's person does not see this one's installs.
    assert bridge.get(bridge.other, "/api/bridge/links")[0] == 404


def test_the_owner_sees_who_is_linked_and_can_end_any_link_but_reads_no_text(
    bridge, ident, install
):
    mine = ident.link(install, "U-MEMBER")
    status, body, raw = bridge.get(bridge.owner, f"/api/bridge/installs/{install}/links")
    assert status == 200
    (row,) = body["links"]
    assert set(row) == {"id", "ext_user", "user_id", "email", "member", "created_at"}
    assert (row["id"], row["ext_user"], row["email"], row["member"]) == (
        mine,
        "U-MEMBER",
        "member@example.com",
        True,
    )
    # A member cannot use the owner routes; the link of another install is not reachable by id.
    assert bridge.get(bridge.member, f"/api/bridge/installs/{install}/links")[0] == 404
    assert (
        bridge.request(bridge.member, "DELETE", f"/api/bridge/installs/{install}/links/{mine}")[0]
        == 404
    )
    other = _enabled_install(bridge, "T-TWO")
    status, body, _ = bridge.request(
        bridge.owner, "DELETE", f"/api/bridge/installs/{other}/links/{mine}"
    )
    assert (status, body["code"]) == (404, "not_found")
    assert ident.resolve(install, "U-MEMBER").reason == "ok"
    status, body, _ = bridge.request(
        bridge.owner, "DELETE", f"/api/bridge/installs/{install}/links/{mine}"
    )
    assert (status, body) == (200, {"ok": True})
    assert ident.resolve(install, "U-MEMBER").reason == "unlinked"
    assert bridge.get(bridge.owner, f"/api/bridge/installs/{install}/links")[1]["links"] == []
    assert [r["tool"] for r in audit_rows(bridge)].count("unlink_owner") == 1
    # The owner sees no conversation text because none is stored in the bridge file.
    tables = {r[0] for r in ident.db().execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any(
        "text" in c[1] for t in tables for c in ident.db().execute(f"PRAGMA table_info({t})")
    )


# --- the per-event re-check ----------------------------------------------------------------------


def test_a_removed_member_resolves_to_no_principal_and_stays_unlinked_when_added_back(
    bridge, ident, install
):
    link = ident.link(install, "U-MEMBER")
    assert ident.resolve(install, "U-MEMBER").principal is not None
    ident.leave(bridge.member_user)
    gone = ident.resolve(install, "U-MEMBER")
    assert (gone.principal, gone.reason) == (None, "removed")
    owner_view = bridge.get(bridge.owner, f"/api/bridge/installs/{install}/links")[1]["links"]
    assert owner_view == []  # the link ended with the next event
    assert [r["tool"] for r in audit_rows(bridge)].count("link_end") == 1
    bridge.hub.db.add_member(ident.ws, bridge.member_user, "member", actor=bridge.hub.admin)
    again = ident.resolve(install, "U-MEMBER")
    assert (again.principal, again.reason) == (None, "unlinked")  # adding back does not revive it
    assert ident.link(install, "U-MEMBER") != link


def test_the_owner_list_marks_a_person_who_left_before_any_event(bridge, ident, install):
    ident.link(install, "U-MEMBER")
    ident.leave(bridge.member_user)
    (row,) = bridge.get(bridge.owner, f"/api/bridge/installs/{install}/links")[1]["links"]
    assert row["member"] is False


def test_a_person_who_left_cannot_redeem_and_their_stale_link_makes_room(bridge, ident, install):
    code = ident.code(install)
    ident.leave(bridge.member_user)
    assert ident.redeem(install, "U-MEMBER", code).code == "invalid"
    # A link whose person left (no event ended it yet) does not block the platform id or a person.
    ident.link(install, "U-THIRD", ident.third)
    ident.leave(ident.third_user)
    bridge.hub.db.add_member(ident.ws, bridge.member_user, "member", actor=bridge.hub.admin)
    fresh = ident.code(install)
    assert ident.redeem(install, "U-THIRD", fresh).code == "linked"
    assert ident.resolve(install, "U-THIRD").principal.user_id == bridge.member_user
    # A disabled account resolves to nobody either.
    with bridge.hub.db._lock:
        bridge.hub.db._conn.execute(
            "UPDATE users SET disabled_at='2026-01-01T00:00:00Z' WHERE id=?",
            (bridge.member_user,),
        )
        bridge.hub.db._conn.commit()
    assert ident.resolve(install, "U-THIRD").reason == "removed"


def test_disabling_the_install_stops_resolving_and_enabling_again_restores_the_link(
    bridge, ident, install
):
    ident.link(install, "U-MEMBER")
    path = f"/api/bridge/installs/{install}"
    assert bridge.request(bridge.owner, "PATCH", path, {"enabled": False})[0] == 200
    off = ident.resolve(install, "U-MEMBER")
    assert (off.principal, off.reason) == (None, "off")
    assert ident.redeem(install, "U-X", "ABCD-EFGH-JKMN").code == "off"
    assert bridge.get(bridge.member, "/api/bridge/links")[1]["available"] == []
    assert (
        len(bridge.get(bridge.member, "/api/bridge/links")[1]["links"]) == 1
    )  # still theirs to end
    assert bridge.request(bridge.owner, "PATCH", path, {"enabled": True})[0] == 200
    assert ident.resolve(install, "U-MEMBER").reason == "ok"


def test_deleting_the_install_removes_its_codes_links_and_tries(bridge, ident, install):
    ident.link(install, "U-MEMBER")
    ident.code(install, ident.third)
    ident.redeem(install, "U-X", "ABCD-EFGH-JKMN")
    assert bridge.request(bridge.owner, "DELETE", f"/api/bridge/installs/{install}")[0] == 200
    db = ident.db()
    assert db.execute("SELECT COUNT(*) FROM bridge_links").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM bridge_link_codes").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM bridge_link_tries").fetchone()[0] == 0


# --- account export ------------------------------------------------------------------------------


def test_the_account_export_redacts_code_hashes(bridge, ident, install):
    from commonplace.account import export

    code = ident.code(install)
    plain = code.replace("-", "")
    hashed = links.digest(install, plain)
    ident.link(install, "U-THIRD", ident.third)
    planned = export.plan(bridge.hub.gateway, bridge.hub.admin)
    out = io.BytesIO()
    export.write(bridge.hub.gateway, planned, out)
    archive = zipfile.ZipFile(io.BytesIO(out.getvalue()))
    prefix = f"workspaces/{bridge.hub.workspace}/stores/chat_bridge.sqlite3"
    rows = [
        json.loads(line) for line in archive.read(f"{prefix}/bridge_link_codes.ndjson").splitlines()
    ]
    assert rows and all(r["code_hash"] == "[redacted]" for r in rows)
    assert f"{prefix}/bridge_links.ndjson" in archive.namelist()
    blob = b"".join(archive.read(name) for name in archive.namelist())
    assert hashed.encode() not in blob and plain.encode() not in blob
    assert SENTINEL.encode() not in blob
