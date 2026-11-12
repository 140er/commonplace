"""Shared bots, store foundation: tables, the upgrade of an old file, ``reach``, shares, access.

Generic members only: Avery owns the bot, Blake and Casey are members. Nothing here is reached
by a route or tool yet; a bot that is not shared behaves as before.
"""

import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from commonplace.bots import shares, sharing_setting
from commonplace.bots.errors import SharingOff, Unsupported
from commonplace.bots.store import BotStore, Conflict, Invalid, Limit, Missing
from commonplace.control_db import ControlDB

AVERY, BLAKE, CASEY = "u-avery", "u-blake", "u-casey"
REQ = "request-0001"


class Switch:
    on = True

    def __call__(self):
        return self.on


@pytest.fixture(name="switch")
def switch_fixture():
    return Switch()


@pytest.fixture(name="bots")
def bots_fixture(tmp_path, switch):
    supervisor = SimpleNamespace(workspace_dir=lambda workspace: str(tmp_path / workspace))
    opened = []

    def open_store(sharing=switch):
        opened.append(BotStore(supervisor, "ws1", sharing=sharing))
        return opened[-1]

    bots = open_store()
    bots.reopen = open_store
    bots.path = tmp_path / "ws1" / "bots.sqlite3"
    yield bots
    for each in opened:
        each.close()


def rows(bots, sql, *args):
    with closing(sqlite3.connect(bots.path)) as conn:
        return conn.execute(sql, args).fetchall()


def shared_bot(bots, name="Team", **share):
    bot_id = bots.create(AVERY, name)["id"]
    bots.share(bot_id, AVERY, BLAKE, **({"talk": True, "see_shared": False} | share))
    return bot_id


# Default off


def test_sharing_is_off_by_default_and_nothing_is_written(tmp_path):
    supervisor = SimpleNamespace(workspace_dir=lambda workspace: str(tmp_path / workspace))
    bots = BotStore(supervisor, "ws1")
    try:
        bot_id = bots.create(AVERY, "Team")["id"]
        with pytest.raises(SharingOff):
            bots.share(bot_id, AVERY, BLAKE, True, False)
        path = tmp_path / "ws1" / "bots.sqlite3"
        with closing(sqlite3.connect(path)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM bot_shares").fetchone()[0] == 0
            assert conn.execute("SELECT shared FROM bots").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM bot_access_log").fetchone()[0] == 0
    finally:
        bots.close()


def test_switch_off_hides_a_live_share_without_changing_it(bots, switch):
    bot_id = shared_bot(bots)
    thread = bots.thread_open(bot_id, BLAKE, "private")
    assert bots.reach(BLAKE, bot_id, thread["id"]).kind == "private"
    switch.on = False
    for call in (
        lambda: bots.reach(BLAKE, bot_id),
        lambda: bots.shares(bot_id, BLAKE),
        lambda: bots.access_get(bot_id, BLAKE),
        lambda: bots.thread_open(bot_id, BLAKE, "shared"),
    ):
        with pytest.raises(Missing):
            call()
    assert bots.reach(AVERY, bot_id).owner is True
    switch.on = True
    assert bots.reach(BLAKE, bot_id, thread["id"]).kind == "private"
    assert [s["user_id"] for s in bots.shares(bot_id, AVERY)] == [BLAKE]


def test_a_failing_switch_fails_closed(bots):
    bot_id = shared_bot(bots)

    def broken():
        raise RuntimeError("down")

    other = bots.reopen(sharing=broken)
    with pytest.raises(Missing):
        other.access_get(bot_id, BLAKE)
    assert other.access_get(bot_id, AVERY)["version"] == 0


def test_the_workspace_setting_is_owner_only_off_by_default_and_audited(tmp_path):
    with closing(ControlDB(str(tmp_path / "control.sqlite3"))) as db:
        owner = SimpleNamespace(person=True, role="owner", workspace_id="ws1", user_id=AVERY)
        member = SimpleNamespace(person=True, role="member", workspace_id="ws1", user_id=BLAKE)
        agent = SimpleNamespace(person=False, role="owner", workspace_id="ws1", user_id=None)
        assert sharing_setting.enabled(db, "ws1") is False
        for denied in (member, agent):
            with pytest.raises(sharing_setting.NotOwner):
                sharing_setting.set_enabled(db, denied, True)
        with pytest.raises(ValueError):
            sharing_setting.set_enabled(db, owner, "yes")
        assert sharing_setting.enabled(db, "ws1") is False
        assert sharing_setting.set_enabled(db, owner, True) is True
        assert sharing_setting.set_enabled(db, owner, True) is True  # no second audit row
        assert sharing_setting.enabled(db, "ws1") is True
        assert sharing_setting.enabled(db, "ws2") is False
        sharing_setting.set_enabled(db, owner, False)
        assert sharing_setting.enabled(db, "ws1") is False
        audits = [r for r in db.audit_entries() if r["action"] == "bots.sharing"]
        assert sorted(r["reason"] for r in audits) == ["off", "on"]


# Upgrade of an old file


OLD_FILE = """
CREATE TABLE bots (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
    job TEXT NOT NULL DEFAULT '', instructions TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL,
    board TEXT, read_upto INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, archived_at TEXT);
CREATE TABLE bot_messages (id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT NOT NULL,
    role TEXT NOT NULL, text TEXT NOT NULL, blocks TEXT NOT NULL DEFAULT '[]', request_id TEXT,
    reply_to INTEGER, model TEXT, state TEXT NOT NULL DEFAULT 'ok', created_at TEXT NOT NULL);
CREATE UNIQUE INDEX bot_messages_request ON bot_messages(bot_id, request_id) WHERE role='user';
CREATE TABLE bot_memory (id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT NOT NULL,
    text TEXT NOT NULL, created_at TEXT NOT NULL, removed_at TEXT);
CREATE TABLE bot_work (bot_id TEXT NOT NULL, board TEXT NOT NULL, card INTEGER NOT NULL,
    message_id INTEGER NOT NULL, PRIMARY KEY (bot_id, board, card));
CREATE TABLE bot_forwards (bot_id TEXT NOT NULL, board TEXT NOT NULL,
    space_message_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
    PRIMARY KEY (bot_id, board, space_message_id));
CREATE TABLE bot_pending (id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, user_id TEXT NOT NULL,
    tool TEXT NOT NULL, args TEXT NOT NULL, label TEXT NOT NULL, created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open');
INSERT INTO bots (id,user_id,name,kind,created_at)
    VALUES ('old-bot','u-avery','Old','custom','2026-01-01T00:00:00Z');
INSERT INTO bot_messages (bot_id,role,text,request_id,created_at)
    VALUES ('old-bot','user','hello there','request-0001','2026-01-01T00:00:01Z');
INSERT INTO bot_memory (bot_id,text,created_at)
    VALUES ('old-bot','a note','2026-01-01T00:00:02Z');
"""


def test_an_old_format_file_opens_keeps_its_data_and_gets_the_new_shape(tmp_path):
    path = tmp_path / "ws1" / "bots.sqlite3"
    path.parent.mkdir()
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(OLD_FILE)
    supervisor = SimpleNamespace(workspace_dir=lambda workspace: str(tmp_path / workspace))
    bots = BotStore(supervisor, "ws1")
    try:
        assert bots.get("old-bot", AVERY)["name"] == "Old"
        assert [m["text"] for m in bots.messages("old-bot", AVERY)] == ["hello there"]
        assert [m["text"] for m in bots.memory_list("old-bot", AVERY)] == ["a note"]
        assert bots.add_user_message("old-bot", AVERY, "hello there", REQ)["id"] == 1
        with pytest.raises(Conflict):
            bots.add_user_message("old-bot", AVERY, "other text", REQ)
        with closing(sqlite3.connect(path)) as conn:
            assert conn.execute(
                "SELECT shared, access_version, frozen_at FROM bots"
            ).fetchone() == (0, 0, None)
            index = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name='bot_messages_request'"
            ).fetchone()[0]
            assert "thread_id" in index
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert {
                "bot_shares",
                "bot_threads",
                "bot_reads",
                "bot_access",
                "bot_access_log",
            } <= tables
    finally:
        bots.close()
    again = BotStore(supervisor, "ws1")  # a second open changes nothing
    try:
        assert [m["id"] for m in again.messages("old-bot", AVERY)] == [1]
    finally:
        again.close()


def test_a_fresh_file_and_a_reopen_have_the_same_request_index(bots):
    bots.create(AVERY, "Team")
    first = rows(bots, "SELECT sql FROM sqlite_master WHERE name='bot_messages_request'")
    bots.reopen().close()
    assert rows(bots, "SELECT sql FROM sqlite_master WHERE name='bot_messages_request'") == first


# Reach


def test_reach_for_the_owner_and_an_unshared_bot(bots):
    bot_id = bots.create(AVERY, "Notes")["id"]
    reach = bots.reach(AVERY, bot_id)
    assert (reach.kind, reach.thread_id, reach.owner, reach.read_from, reach.can_write) == (
        "owner",
        None,
        True,
        0,
        True,
    )
    for stranger in (BLAKE, CASEY, "", None):
        with pytest.raises(Missing):
            bots.reach(stranger, bot_id)
    with pytest.raises(Missing):
        bots.reach(AVERY, "no-such-bot")
    with pytest.raises(Missing):
        bots.reach(AVERY, bot_id, "no-such-thread")
    archived = bots.update(bot_id, AVERY, archived=True)
    assert archived["archived"] is True
    assert bots.reach(AVERY, bot_id).can_write is False


def test_defaults_cannot_be_shared(bots):
    bots.ensure_defaults(AVERY, [("work", "Work")])
    for bot in bots.roster(AVERY):
        with pytest.raises(Invalid):
            bots.share(bot["id"], AVERY, BLAKE, True, False)
        with pytest.raises(sqlite3.IntegrityError):
            rows_conn = sqlite3.connect(bots.path)
            try:
                rows_conn.execute("UPDATE bots SET shared=1 WHERE id=?", (bot["id"],))
            finally:
                rows_conn.close()


def test_reach_table_for_members_threads_and_visibility(bots):
    bot_id = shared_bot(bots, talk=True, see_shared=False)
    bots.share(bot_id, AVERY, CASEY, talk=False, see_shared=True)
    blake_private = bots.thread_open(bot_id, BLAKE, "private")
    assert bots.thread_open(bot_id, BLAKE, "private")["id"] == blake_private["id"]
    shared = bots.thread_open(bot_id, BLAKE, "shared", "Plan")
    # The member's own private thread, by default and by id.
    default = bots.reach(BLAKE, bot_id)
    assert (default.kind, default.thread_id, default.owner) == (
        "private",
        blake_private["id"],
        False,
    )
    assert default.can_write is True
    # Nobody else reaches it: not the owner, not another member.
    for other in (AVERY, CASEY):
        with pytest.raises(Missing):
            bots.reach(other, bot_id, blake_private["id"])
    # A member without talk has no private thread and cannot open one.
    with pytest.raises(Missing):
        bots.reach(CASEY, bot_id)
    with pytest.raises(Missing):
        bots.thread_open(bot_id, CASEY, "private")
    with pytest.raises(Missing):
        bots.thread_open(bot_id, CASEY, "shared")
    # Shared thread: opener with talk writes, see_shared reads only, the owner reads and writes.
    assert bots.reach(BLAKE, bot_id, shared["id"]).can_write is True
    casey = bots.reach(CASEY, bot_id, shared["id"])
    assert (casey.kind, casey.can_write) == ("shared", False)
    assert bots.reach(AVERY, bot_id, shared["id"]).can_write is True
    # Another workspace member with no share, and a thread id of another bot, are unknown.
    with pytest.raises(Missing):
        bots.reach("u-drew", bot_id, shared["id"])
    other = bots.create(AVERY, "Other")["id"]
    with pytest.raises(Missing):
        bots.reach(AVERY, other, shared["id"])
    # The owner has no private row; their original thread is the default.
    with pytest.raises(Invalid):
        bots.thread_open(bot_id, AVERY, "private")
    assert [t["visibility"] for t in bots.threads(bot_id, CASEY)] == ["shared"]
    assert sorted(t["visibility"] for t in bots.threads(bot_id, BLAKE)) == ["private", "shared"]
    assert [t["visibility"] for t in bots.threads(bot_id, AVERY)] == ["shared"]


def test_talk_only_member_reads_only_a_shared_thread_they_opened(bots):
    bot_id = shared_bot(bots, talk=True, see_shared=False)
    bots.share(bot_id, AVERY, CASEY, talk=True, see_shared=False)
    mine = bots.thread_open(bot_id, BLAKE, "shared")
    with pytest.raises(Missing):
        bots.reach(CASEY, bot_id, mine["id"])
    assert bots.reach(BLAKE, bot_id, mine["id"]).can_write is True
    bots.share(bot_id, AVERY, CASEY, talk=True, see_shared=True)
    assert bots.reach(CASEY, bot_id, mine["id"]).can_write is True


def test_archived_threads_and_bots_are_read_only_and_only_the_opener_archives(bots):
    bot_id = shared_bot(bots, talk=True, see_shared=True)
    thread = bots.thread_open(bot_id, BLAKE, "shared")
    with pytest.raises(Missing):
        bots.thread_archive(bot_id, AVERY, thread["id"], True)
    assert bots.thread_archive(bot_id, BLAKE, thread["id"], True)["archived"] is True
    assert bots.reach(BLAKE, bot_id, thread["id"]).can_write is False
    assert bots.reach(AVERY, bot_id, thread["id"]).can_write is False
    bots.thread_archive(bot_id, BLAKE, thread["id"], False)
    bots.update(bot_id, AVERY, archived=True)
    assert bots.reach(BLAKE, bot_id, thread["id"]).can_write is False
    with pytest.raises(Invalid):
        bots.thread_open(bot_id, BLAKE, "shared")


def test_a_frozen_bot_is_unreachable_for_members(bots):
    bot_id = shared_bot(bots)
    thread = bots.thread_open(bot_id, BLAKE, "private")
    rows_conn = sqlite3.connect(bots.path)
    try:
        rows_conn.execute("UPDATE bots SET frozen_at='2026-01-01T00:00:00Z'")
        rows_conn.commit()
    finally:
        rows_conn.close()
    with pytest.raises(Missing):
        bots.reach(BLAKE, bot_id, thread["id"])
    frozen = bots.reach(AVERY, bot_id)
    assert (frozen.frozen, frozen.can_write) == (True, False)


# Shares


def test_share_validation_and_idempotence(bots):
    bot_id = bots.create(AVERY, "Team")["id"]
    for bad in (
        (bot_id, AVERY, AVERY, True, False),  # the owner is not a member
        (bot_id, AVERY, "", True, False),
        (bot_id, AVERY, BLAKE, False, False),  # a share must be useful
        (bot_id, AVERY, BLAKE, 1, False),
        (bot_id, AVERY, BLAKE, True, None),
    ):
        with pytest.raises(Invalid):
            bots.share(*bad)
    for foreign in (BLAKE, CASEY):
        with pytest.raises(Missing):
            bots.share(bot_id, foreign, "u-drew", True, False)
    first = bots.share(bot_id, AVERY, BLAKE, True, False)
    assert bots.share(bot_id, AVERY, BLAKE, True, False) == first
    changed = bots.share(bot_id, AVERY, BLAKE, True, True)
    assert (changed["talk"], changed["see_shared"]) == (True, True)
    assert changed["history_from"] == first["history_from"]
    log = bots.access_log(bot_id, AVERY)
    assert [(r["action"], r["member_id"]) for r in log] == [
        ("share", BLAKE),
        ("permissions", BLAKE),
    ]


def test_first_share_seeds_only_the_board_read_grant(bots):
    plain = shared_bot(bots)
    assert bots.access_get(plain, AVERY) == {"version": 0, "entries": []}
    bots.create(AVERY, "Docs board")  # a custom bot may carry a board
    with closing(sqlite3.connect(bots.path)) as conn:
        conn.execute("UPDATE bots SET board='work' WHERE name='Docs board'")
        conn.commit()
    boarded = bots.roster(AVERY)
    bot_id = next(b["id"] for b in boarded if b["name"] == "Docs board")
    bots.share(bot_id, AVERY, BLAKE, True, False)
    bots.share(bot_id, AVERY, CASEY, True, False)
    access = bots.access_get(bot_id, AVERY)
    assert access["version"] == 1
    assert [(e["kind"], e["ref"], e["mode"], e["set_by"]) for e in access["entries"]] == [
        ("project", "work", "read", AVERY)
    ]
    assert [(r["action"], r["version"]) for r in bots.access_log(bot_id, AVERY)] == [
        ("grant", 1),
        ("share", 1),
        ("share", 1),
    ]


def test_revoke_is_immediate_idempotent_and_reshare_resets_history(bots):
    bot_id = shared_bot(bots, talk=True, see_shared=True)
    thread = bots.thread_open(bot_id, BLAKE, "shared")
    first = bots.thread_post(bot_id, BLAKE, thread["id"], "before", REQ)
    assert bots.revoke(bot_id, AVERY, BLAKE) is True
    assert bots.revoke(bot_id, AVERY, BLAKE) is False
    assert bots.revoke(bot_id, AVERY, "u-nobody") is False
    with pytest.raises(Missing):
        bots.reach(BLAKE, bot_id, thread["id"])
    with pytest.raises(Missing):
        bots.thread_post(bot_id, BLAKE, thread["id"], "after", "request-0002")
    (row,) = bots.shares(bot_id, AVERY, include_revoked=True)
    assert (row["revoke_reason"], row["revoked_at"] is not None) == ("owner", True)
    assert bots.shares(bot_id, AVERY) == []
    with pytest.raises(Invalid):
        bots.revoke(bot_id, AVERY, BLAKE, reason="whatever")
    again = bots.share(bot_id, AVERY, BLAKE, True, True)
    assert again["revoked_at"] is None and again["history_from"] >= first["id"]
    # The opener's own old message is below the new history start; nothing older is restored.
    assert bots.thread_messages(bot_id, BLAKE, thread["id"]) == []
    assert [r["action"] for r in bots.access_log(bot_id, AVERY)] == [
        "share",
        "unshare",
        "share",
    ]


def test_history_from_hides_older_shared_messages_from_a_new_member(bots):
    bot_id = shared_bot(bots, talk=True, see_shared=True)
    thread = bots.thread_open(bot_id, BLAKE, "shared")
    bots.thread_post(bot_id, BLAKE, thread["id"], "early", REQ)
    bots.share(bot_id, AVERY, CASEY, talk=False, see_shared=True)
    bots.thread_post(bot_id, BLAKE, thread["id"], "late", "request-0002")
    assert [m["text"] for m in bots.thread_messages(bot_id, CASEY, thread["id"])] == ["late"]
    assert [m["text"] for m in bots.thread_messages(bot_id, BLAKE, thread["id"])] == [
        "early",
        "late",
    ]
    assert [m["text"] for m in bots.thread_messages(bot_id, AVERY, thread["id"])] == [
        "early",
        "late",
    ]
    page = bots.thread_messages(bot_id, CASEY, thread["id"], after=0)
    assert [m["text"] for m in page] == ["late"]


def test_share_caps(bots, monkeypatch):
    monkeypatch.setattr(shares, "MAX_SHARED_BOTS", 2)
    monkeypatch.setattr(shares, "MAX_SHARES", 3)
    monkeypatch.setattr(shares, "MAX_SEE_SHARED", 2)
    a, b = (bots.create(AVERY, n)["id"] for n in "AB")
    bots.share(a, AVERY, BLAKE, True, False)
    bots.share(b, AVERY, BLAKE, True, False)
    c = bots.create(AVERY, "C")["id"]
    with pytest.raises(Limit):
        bots.share(c, AVERY, BLAKE, True, False)
    bots.share(a, AVERY, CASEY, True, True)
    bots.share(a, AVERY, "u-drew", True, True)
    with pytest.raises(Limit):  # the fourth share
        bots.share(a, AVERY, "u-eli", True, False)
    bots.revoke(a, AVERY, "u-drew")
    bots.share(a, AVERY, "u-eli", True, True)
    with pytest.raises(Limit):  # the third see_shared member
        bots.share(a, AVERY, BLAKE, True, True)


# Messages and threads


def test_request_ids_are_per_thread_and_idempotent_within_one(bots):
    bot_id = shared_bot(bots, talk=True, see_shared=True)
    bots.share(bot_id, AVERY, CASEY, talk=True, see_shared=False)
    blake = bots.thread_open(bot_id, BLAKE, "private")["id"]
    casey = bots.thread_open(bot_id, CASEY, "private")["id"]
    owner = bots.add_user_message(bot_id, AVERY, "owner words", REQ)
    mine = bots.thread_post(bot_id, BLAKE, blake, "blake words", REQ)
    theirs = bots.thread_post(bot_id, CASEY, casey, "casey words", REQ)
    assert len({owner["id"], mine["id"], theirs["id"]}) == 3
    assert bots.thread_post(bot_id, BLAKE, blake, "blake words", REQ)["id"] == mine["id"]
    with pytest.raises(Conflict):
        bots.thread_post(bot_id, BLAKE, blake, "changed", REQ)
    assert bots.add_user_message(bot_id, AVERY, "owner words", REQ)["id"] == owner["id"]
    with pytest.raises(Conflict):
        bots.add_user_message(bot_id, AVERY, "casey words", REQ)
    # Another member cannot squat or replay into a thread that is not theirs.
    with pytest.raises(Missing):
        bots.thread_post(bot_id, CASEY, blake, "blake words", "request-0002")
    assert bots.message_for_request(bot_id, AVERY, REQ)["id"] == owner["id"]
    with closing(sqlite3.connect(bots.path)) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO bot_messages (bot_id,role,text,request_id,created_at,thread_id) "
                "VALUES (?,?,?,?,?,?)",
                (bot_id, "user", "dup", REQ, "2026-01-01T00:00:00Z", blake),
            )


def test_member_messages_never_appear_in_the_owners_original_thread(bots):
    bot_id = shared_bot(bots, talk=True, see_shared=False)
    private = bots.thread_open(bot_id, BLAKE, "private")["id"]
    user = bots.thread_post(bot_id, BLAKE, private, "member secret", REQ)
    bots.thread_reply(bot_id, BLAKE, private, user["id"], "reply to member")
    bots.add_user_message(bot_id, AVERY, "owner words", REQ)
    assert [m["text"] for m in bots.messages(bot_id, AVERY)] == ["owner words"]
    assert bots.unanswered(bot_id, AVERY)[0]["text"] == "owner words"
    assert bots.reply_to(bot_id, AVERY, user["id"]) is None
    (entry,) = bots.roster(AVERY, bot_id)
    assert entry["last"]["text"] == "owner words" and entry["unread"] == 0
    assert bots.mark_read(bot_id, AVERY, 10**6) == 3  # the owner's own message id
    mine = bots.thread_messages(bot_id, BLAKE, private)
    assert [(m["role"], m["text"], m["author_id"]) for m in mine] == [
        ("user", "member secret", BLAKE),
        ("bot", "reply to member", None),
    ]
    with pytest.raises(Invalid):
        bots.add_bot_message(bot_id, AVERY, user["id"], "owner replies to a member message")
    for other in (AVERY, CASEY):
        with pytest.raises(Missing):
            bots.thread_messages(bot_id, other, private)


def test_notes_of_a_member_never_reach_the_owner_store_reads(bots):
    bot_id = bots.create(AVERY, "Team")["id"]
    bots.memory_add(bot_id, AVERY, "bot note")
    with closing(sqlite3.connect(bots.path)) as conn:
        conn.execute(
            "INSERT INTO bot_memory (bot_id,text,created_at,owner_id) VALUES (?,?,?,?)",
            (bot_id, "blake's own note", "2026-01-01T00:00:00Z", BLAKE),
        )
        conn.commit()
    assert [n["text"] for n in bots.memory_list(bot_id, AVERY)] == ["bot note"]
    assert bots.memory_remove(bot_id, AVERY, 2) is False


# Access


def test_access_set_is_compare_and_set_logged_once_and_noop_when_equal(bots):
    bot_id = shared_bot(bots)
    entries = [
        {"kind": "project", "ref": "work", "mode": "read"},
        {"kind": "tool", "ref": "list_work", "mode": "use"},
    ]
    stored = bots.access_set(bot_id, AVERY, 0, entries)
    assert stored["version"] == 1
    assert [(e["kind"], e["ref"], e["mode"]) for e in stored["entries"]] == [
        ("project", "work", "read"),
        ("tool", "list_work", "use"),
    ]
    with pytest.raises(Conflict):  # a stale version writes nothing
        bots.access_set(bot_id, AVERY, 0, [])
    assert bots.access_get(bot_id, AVERY)["version"] == 1
    before = len(bots.access_log(bot_id, AVERY))
    assert bots.access_set(bot_id, AVERY, 1, list(reversed(entries)))["version"] == 1
    assert len(bots.access_log(bot_id, AVERY)) == before  # equal list: no log row
    bots.access_set(
        bot_id,
        AVERY,
        1,
        [{"kind": "project", "ref": "work", "mode": "write"}],
    )
    log = [r for r in bots.access_log(bot_id, AVERY) if r["version"] == 2]
    assert [(r["action"], r["kind"], r["ref"], r["mode_before"], r["mode_after"]) for r in log] == [
        ("change", "project", "work", "read", "write"),
        ("remove", "tool", "list_work", "use", None),
    ]
    assert {r["actor_id"] for r in log} == {AVERY}


def test_access_validation_and_owner_only(bots):
    bot_id = shared_bot(bots)
    for bad in (
        "all",
        [{"kind": "project", "ref": "work"}],
        [{"kind": "project", "ref": "work", "mode": "use"}],
        [{"kind": "machine", "ref": "m1", "mode": "read"}],
        [{"kind": "project", "ref": "", "mode": "read"}],
        [{"kind": "project", "ref": "x" * 201, "mode": "read"}],
        [{"kind": "project", "ref": "a", "mode": "read", "extra": 1}],
        [{"kind": "project", "ref": "a", "mode": "read"}] * 2,
        [{"kind": "project", "ref": str(i), "mode": "read"} for i in range(201)],
    ):
        with pytest.raises(Invalid):
            bots.access_set(bot_id, AVERY, 0, bad)
    with pytest.raises(Unsupported):
        bots.access_set(bot_id, AVERY, 0, [{"kind": "wing", "ref": "w", "mode": "read"}])
    with pytest.raises(Invalid):
        bots.access_set(bot_id, AVERY, "0", [])
    with pytest.raises(Missing):  # a member cannot change it (the route answers owner_only)
        bots.access_set(bot_id, BLAKE, 0, [])
    unshared = bots.create(AVERY, "Solo")["id"]
    with pytest.raises(Invalid):
        bots.access_set(unshared, AVERY, 0, [])
    assert bots.access_get(bot_id, AVERY)["version"] == 0


def test_members_read_the_list_and_log_but_not_other_members_shares(bots):
    bot_id = shared_bot(bots)
    bots.share(bot_id, AVERY, CASEY, talk=False, see_shared=True)
    bots.access_set(bot_id, AVERY, 0, [{"kind": "project", "ref": "work", "mode": "read"}])
    assert bots.access_get(bot_id, BLAKE) == bots.access_get(bot_id, AVERY)
    assert len(bots.access_log(bot_id, CASEY)) == 3
    assert [s["user_id"] for s in bots.shares(bot_id, BLAKE)] == [BLAKE]
    assert [s["user_id"] for s in bots.shares(bot_id, AVERY)] == [BLAKE, CASEY]
    with pytest.raises(Missing):
        bots.access_get(bot_id, "u-drew")
    with pytest.raises(Missing):
        bots.access_log(bot_id, "u-drew")
    bots.revoke(bot_id, AVERY, BLAKE)
    with pytest.raises(Missing):
        bots.access_get(bot_id, BLAKE)


def test_the_access_log_is_append_only_and_carries_no_text(bots):
    bot_id = shared_bot(bots)
    bots.access_set(bot_id, AVERY, 0, [{"kind": "tool", "ref": "list_work", "mode": "use"}])
    with closing(sqlite3.connect(bots.path)) as conn:
        for sql in ("UPDATE bot_access_log SET actor_id='x'", "DELETE FROM bot_access_log"):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)
    columns = {r[1] for r in rows(bots, "PRAGMA table_info(bot_access_log)")}
    assert columns == {
        "id", "bot_id", "version", "actor_id", "action", "kind", "ref", "mode_before",
        "mode_after", "member_id", "at",
    }  # fmt: skip


def test_shares_of_one_bot_do_not_touch_another_workspace(tmp_path, switch):
    supervisor = SimpleNamespace(workspace_dir=lambda workspace: str(tmp_path / workspace))
    one, two = (
        BotStore(supervisor, "ws1", sharing=switch),
        BotStore(supervisor, "ws2", sharing=switch),
    )
    try:
        bot_id = one.create(AVERY, "Team")["id"]
        one.share(bot_id, AVERY, BLAKE, True, False)
        for who in (AVERY, BLAKE):
            with pytest.raises(Missing):
                two.reach(who, bot_id)
    finally:
        one.close()
        two.close()
