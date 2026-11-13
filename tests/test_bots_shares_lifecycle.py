"""Members and owners leaving, disable and adopt, over real HTTP with a scripted model.

Generic members: Avery owns the workspace (and so may disable and adopt), Blake and Casey are members.
Dana is a second workspace owner. A removed member is a membership row that is gone; the removal hook
(``lifecycle.member_removed``) runs once it is, and nothing is ever deleted: messages, threads and the
bot stay in the file, and a removed owner's bot is frozen until a workspace owner adopts it.
"""

import json
import threading

import pytest

from commonplace import sessions
from commonplace.bots import access, lifecycle, store, turn
from commonplace.chat import model_stream
from tests._bots_helpers import bots_hub_fixture  # noqa: F401
from tests.test_bots_shares_api import Team, entry, final
from tests.test_bots_turn import Fake, Stream, call, configure

SECRET = "OLDOWNERSECRET-7731"


@pytest.fixture
def fake(monkeypatch):
    fake = Fake()
    monkeypatch.setattr(model_stream, "stream", fake)
    return fake


@pytest.fixture(name="team")
def team_fixture(bots_hub):
    team = Team(bots_hub)
    assert team.switch()[0] == 200
    return team


def remove(team, user, hook=True):
    """Remove ``user`` from the workspace (their membership row goes), then run the hook."""
    db = team.hub.hub.db
    with db._lock, db._tx() as conn:
        conn.execute("DELETE FROM connections WHERE user_id=?", (user,))
        conn.execute(
            "DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (team.workspace, user)
        )
    return (
        lifecycle.member_removed(team.hub.hub.gateway, team.workspace, user, team.avery)
        if hook
        else None
    )


def readd(team, user):
    team.hub.hub.db.add_member(team.workspace, user, "member", actor=team.avery)


def dana(team):
    """A second workspace owner, with a session."""
    db = team.hub.hub.db
    user = db.create_user("dana@example.com")
    db.add_member(team.workspace, user, "owner", actor=team.avery)
    team.auth[user] = team.hub.cookie("dana@example.com", team.workspace)
    return user


def make_bot(team, user, name="Casey bot"):
    """A custom bot made by ``user`` (a member)."""
    status, body, _ = team.hub.post(team.auth[user], "/api/bots", {"name": name})
    assert status == 201, body
    return body["bot"]["id"]


def grant(team, user, bot_id, entries):
    """Set the access list as the bot's owner (the bot must be shared by then)."""
    auth = team.auth[user]
    version = team.hub.get(auth, f"/api/bots/{bot_id}/access")[1]["version"]
    status, body, _ = team.hub.request(
        auth, "PUT", f"/api/bots/{bot_id}/access", {"version": version, "entries": entries}
    )
    assert status == 200, body


def share(team, bot_id, owner, member, **flags):
    status, body, _ = team.share(bot_id, member, auth=team.auth[owner], **flags)
    assert status in (200, 201), body


def post(team, user, bot_id, tail):
    return team.hub.post(team.auth[user], f"/api/bots/{bot_id}/{tail}", {})


def shared_ids(team, user):
    status, body, _ = team.hub.get(team.auth[user], "/api/bots/shared")
    return status, [b["id"] for b in body.get("bots", [])]


def messages_of(team, user, bot_id, suffix=""):
    status, body, _ = team.hub.get(team.auth[user], f"/api/bots/{bot_id}/messages{suffix}")
    return status, [m["text"] for m in body.get("messages", [])]


def rows(team, sql, *args):
    with team.store._tx(write=False) as conn:
        return [dict(r) for r in conn.execute(sql, args)]


# A member leaves: access ends, the bot and its shared history stay


def test_removing_a_member_ends_their_access_and_keeps_the_bot_and_the_shared_history(team, fake):
    bot_id = team.shared_bot(team.blake, team.casey)
    for user in (team.blake, team.casey):
        share(team, bot_id, team.avery, user, talk=True, see_shared=True)
    status, body, _ = team.hub.post(
        team.auth[team.casey], f"/api/bots/{bot_id}/threads", {"visibility": "shared"}
    )
    assert status == 201
    thread = f"?thread={body['thread']['id']}"
    configure(team.hub)
    fake.script = [[("text", "Noted.")]]
    Stream(
        team.hub, team.auth[team.casey], bot_id, "Casey says hello", "request-0001", thread
    ).until_end()
    team.ask(fake, team.casey, bot_id, "A private word", request_id="request-0002")
    before = rows(team, "SELECT COUNT(*) AS n FROM bot_messages")[0]["n"]

    assert remove(team, team.casey) == {"shares": 1, "frozen": 0}

    for tail in ("messages", "threads", "access", "shares", "memory?own=1"):
        status, _, _ = team.hub.get(team.auth[team.casey], f"/api/bots/{bot_id}/{tail}")
        assert status in (401, 403, 404), tail
    # The share is ended with its reason; the bot, its access and every message stay.
    ended = [
        s
        for s in team.store.shares(bot_id, team.avery, include_revoked=True)
        if s["user_id"] == team.casey
    ]
    assert ended[0]["revoke_reason"] == "member_removed" and ended[0]["revoked_at"]
    assert rows(team, "SELECT COUNT(*) AS n FROM bot_messages")[0]["n"] == before
    assert len(rows(team, "SELECT id FROM bot_threads WHERE user_id=?", team.casey)) == 2
    assert team.store.get(bot_id, team.avery)["frozen"] is False
    # Blake and the owner still read the shared thread, with Casey's words attributed to Casey.
    status, body, _ = team.hub.get(team.auth[team.blake], f"/api/bots/{bot_id}/messages{thread}")
    assert status == 200 and "Casey says hello" in [m["text"] for m in body["messages"]]
    assert team.hub.get(team.auth[team.avery], f"/api/bots/{bot_id}/messages{thread}")[0] == 200
    assert messages_of(team, team.blake, bot_id)[0] == 200  # and Blake still talks to it
    # Nobody reads Casey's private thread, the owner included, and the hook ran once.
    private = rows(
        team, "SELECT id FROM bot_threads WHERE user_id=? AND visibility='private'", team.casey
    )[0]
    for user in (team.avery, team.blake):
        assert messages_of(team, user, bot_id, f"?thread={private['id']}")[0] == 404
    assert remove(team, team.casey) == {"shares": 0, "frozen": 0}  # idempotent
    log = [r["action"] for r in team.store.access_log(bot_id, team.avery)]
    assert log.count("unshare") == 1
    audit = [
        r
        for r in team.hub.hub.db.audit_entries(team.workspace)
        if r["action"] == "bot_lifecycle:set"
    ]
    assert len(audit) == 1 and "hello" not in json.dumps(dict(audit[0]))


def test_re_adding_a_removed_member_does_not_restore_their_share(team, fake):
    bot_id = team.shared_bot(team.casey)
    share(team, bot_id, team.avery, team.casey, talk=True, see_shared=True)
    team.ask(fake, team.casey, bot_id, "Before I left")
    remove(team, team.casey)
    readd(team, team.casey)
    cookie = team.hub.cookie("casey@example.com", team.workspace)
    team.auth[team.casey] = cookie
    assert team.thread(team.casey, bot_id)[0] == 404  # a member again, but with no share
    assert bot_id not in [b["id"] for b in team.hub.bots(cookie)["bots"]]
    assert team.store.shares(bot_id, team.avery) == []
    # The owner shares again: a fresh share, and her private thread is hers once more.
    share(team, bot_id, team.avery, team.casey, talk=True, see_shared=False)
    assert team.texts(team.casey, bot_id) == ["Before I left", "Done."]
    log = [r["action"] for r in team.store.access_log(bot_id, team.avery)]
    assert [a for a in log if a in ("share", "unshare")] == ["share", "unshare", "share"]


def test_a_re_added_member_never_reads_the_shared_history_from_before_the_removal(team, fake):
    bot_id = team.shared_bot(team.blake, team.casey)
    for user in (team.blake, team.casey):
        share(team, bot_id, team.avery, user, talk=True, see_shared=True)
    thread = team.hub.post(
        team.auth[team.blake], f"/api/bots/{bot_id}/threads", {"visibility": "shared"}
    )[1]["thread"]["id"]
    configure(team.hub)
    fake.script = [[("text", "Ok.")]]
    Stream(
        team.hub, team.auth[team.blake], bot_id, "Old plans", "request-0001", f"?thread={thread}"
    ).until_end()
    remove(team, team.casey)
    fake.script = [[("text", "Ok.")]]
    Stream(
        team.hub,
        team.auth[team.blake],
        bot_id,
        "Plans made while Casey was out",
        "request-0002",
        f"?thread={thread}",
    ).until_end()
    readd(team, team.casey)
    team.auth[team.casey] = team.hub.cookie("casey@example.com", team.workspace)
    share(team, bot_id, team.avery, team.casey, talk=True, see_shared=True)
    status, texts = messages_of(team, team.casey, bot_id, f"?thread={thread}")
    assert status == 200 and texts == []  # a new history_from: nothing from before


# The owner leaves: the bot is frozen; a workspace owner adopts it


def owned_bot(team, fake):
    """Casey's shared custom bot, with Casey's private words and one shared with Blake."""
    bot_id = make_bot(team, team.casey)
    share(team, bot_id, team.casey, team.blake, talk=True, see_shared=False)
    grant(
        team,
        team.casey,
        bot_id,
        [entry("project", "docs", "write"), entry("tool", "list_work", "use")],
    )
    team.ask(fake, team.casey, bot_id, f"Private note {SECRET}")
    team.ask(fake, team.blake, bot_id, "Blake asks", request_id="request-0002")
    return bot_id


def test_removing_the_owner_freezes_the_bot_and_deletes_nothing(team, fake):
    bot_id = owned_bot(team, fake)
    before = rows(team, "SELECT COUNT(*) AS n FROM bot_messages")[0]["n"]
    grants = team.store.access_get(bot_id, team.casey)

    assert remove(team, team.casey)["frozen"] >= 1

    state = team.store.shared_state(bot_id, team.casey)  # the store keeps the row
    assert state["frozen"] is True
    row = rows(team, "SELECT frozen_reason, user_id FROM bots WHERE id=?", bot_id)[0]
    assert row == {"frozen_reason": "owner_left", "user_id": team.casey}
    # Nobody talks: members get 404 for it, the roster drops it, nothing was deleted.
    assert team.thread(team.blake, bot_id)[0] == 404
    status, _, _ = team.hub.post(
        team.auth[team.blake],
        f"/api/bots/{bot_id}/messages",
        {"text": "anyone?", "request_id": "request-0003"},
    )
    assert status == 404
    assert bot_id not in [b["id"] for b in team.hub.bots(team.auth[team.blake])["bots"]]
    assert rows(team, "SELECT COUNT(*) AS n FROM bot_messages")[0]["n"] == before
    assert team.store.access_get(bot_id, team.casey)["entries"] == grants["entries"]
    assert (
        len(
            rows(
                team, "SELECT user_id FROM bot_shares WHERE bot_id=? AND revoked_at IS NULL", bot_id
            )
        )
        == 1
    )
    # The workspace owner sees it as ownerless, with its members and access but no conversation.
    status, body, _ = team.hub.get(team.auth[team.avery], "/api/bots/shared")
    listed = {b["id"]: b for b in body["bots"]}[bot_id]
    assert (status, listed["frozen"], listed["frozen_reason"]) == (200, True, "owner_left")
    assert SECRET not in json.dumps(body) and "Blake asks" not in json.dumps(body)


def test_a_workspace_owner_adopts_it_with_the_list_kept_and_capped_by_the_adopter(team, fake):
    bot_id = owned_bot(team, fake)
    remove(team, team.casey)
    entries = team.store.access_get(bot_id, team.casey)["entries"]
    version = team.store.access_get(bot_id, team.casey)["version"]

    status, body, _ = post(team, team.avery, bot_id, "adopt")
    assert status == 200 and body["owner"]["id"] == team.avery

    assert team.store.get(bot_id, team.avery)["frozen"] is False
    adopted = team.store.access_get(bot_id, team.avery)
    assert [(e["kind"], e["ref"], e["mode"]) for e in adopted["entries"]] == [
        (e["kind"], e["ref"], e["mode"]) for e in entries
    ]  # the list is kept, never widened
    assert adopted["version"] == version + 1  # a stale edit of the old owner's cannot land
    log = team.store.access_log(bot_id, team.avery)
    assert log[-1]["action"] == "adopt" and log[-1]["member_id"] == team.casey
    assert log[-1]["actor_id"] == team.avery
    audit = [
        r
        for r in team.hub.hub.db.audit_entries(team.workspace)
        if r["action"] == "bot_lifecycle:set"
    ]
    assert any("adopt" in r["reason"] for r in audit)
    # Blake's share survived and the bot works for him again, under the adopter's rights.
    assert team.texts(team.blake, bot_id) == ["Blake asks", "Done."]
    team.ask(fake, team.blake, bot_id, "Back again", request_id="request-0004")
    assert "Back again" in team.texts(team.blake, bot_id)


def test_the_kept_list_is_capped_by_the_adopters_rights_and_never_widened(team, fake):
    owner = dana(team)  # a workspace owner, so she may grant the private project
    bot_id = make_bot(team, owner, "Dana bot")
    share(team, bot_id, owner, team.blake, talk=True)
    grant(
        team, owner, bot_id, [entry("project", "vault", "write"), entry("project", "docs", "read")]
    )
    remove(team, owner)
    assert post(team, team.avery, bot_id, "adopt")[0] == 200
    gateway, db = team.hub.hub.gateway, team.hub.hub.db
    bot = team.store.viewable(bot_id, team.avery)
    entries = team.store.access_get(bot_id, team.avery)["entries"]
    assert {(e["ref"], e["mode"]) for e in entries} == {("vault", "write"), ("docs", "read")}

    def reach(talker):
        return access.effective(
            gateway,
            bot,
            entries,
            sessions.person_principal(db, talker, team.workspace),
            sessions.person_principal(db, team.avery, team.workspace),
        )

    assert set(reach(team.avery).projects) == {"vault", "docs"}  # the adopter holds both
    assert set(reach(team.blake).projects) == {"docs"} and not reach(team.blake).can_write("docs")
    with db._lock, db._tx() as conn:  # the adopter loses the private project: the list shrinks
        conn.execute(
            "UPDATE memberships SET role='member' WHERE workspace_id=? AND user_id=?",
            (team.workspace, team.avery),
        )
    assert set(reach(team.avery).projects) == {"docs"}
    assert team.store.access_get(bot_id, team.avery)["entries"] == entries  # kept, only dormant


def test_the_adopter_cannot_read_the_old_owners_private_thread(team, fake):
    bot_id = owned_bot(team, fake)
    old = rows(
        team, "SELECT id, text FROM bot_messages WHERE bot_id=? AND thread_id IS NULL", bot_id
    )
    assert any(SECRET in r["text"] for r in old)
    private_blake = team.store.private_thread(bot_id, team.blake)
    with team.store._tx() as conn:  # legacy rows with no person: they were the old owner's
        conn.execute(
            "INSERT INTO bot_work (bot_id,board,card,message_id) VALUES (?,?,?,?)",
            (bot_id, "docs", 7, old[0]["id"]),
        )
    remove(team, team.casey)
    assert post(team, team.avery, bot_id, "adopt")[0] == 200

    status, texts = messages_of(team, team.avery, bot_id)
    assert status == 200 and texts == []  # the old owner's original thread stays behind
    assert SECRET not in json.dumps(
        team.hub.get(team.auth[team.avery], f"/api/bots/{bot_id}/messages")[1]
    )
    assert messages_of(team, team.avery, bot_id, "?after=0")[1] == []
    assert messages_of(team, team.avery, bot_id, "?before=999999")[1] == []
    mine = {b["id"]: b for b in team.hub.bots(team.auth[team.avery])["bots"]}[bot_id]
    assert mine["last"] is None and mine["unread"] == 0 and mine["mine"] is True
    assert messages_of(team, team.avery, bot_id, f"?thread={private_blake}")[0] == 404
    assert team.store.message_for_request(bot_id, team.avery, "request-0001") is None
    assert team.store.unanswered(bot_id, team.avery) == []
    assert team.store.work_list(bot_id, team.avery) == []  # what the old owner filed is not theirs
    assert (
        rows(team, "SELECT user_id FROM bot_work WHERE bot_id=?", bot_id)[0]["user_id"]
        == team.casey
    )
    # What the adopter writes from now on is visible to them.
    status, _, _ = team.hub.post(  # the old owner's request id is taken: a plain conflict
        team.auth[team.avery],
        f"/api/bots/{bot_id}/messages",
        {"text": "Mine now", "request_id": "request-0001"},
    )
    assert status == 409
    team.ask(fake, team.avery, bot_id, "Mine now", request_id="request-0005")
    assert team.texts(team.avery, bot_id) == ["Mine now", "Done."]
    # Nothing of the old owner's was rewritten or deleted.
    assert any(
        SECRET in r["text"]
        for r in rows(team, "SELECT text FROM bot_messages WHERE bot_id=?", bot_id)
    )


def test_adopt_is_for_workspace_owners_in_a_web_session_and_only_for_a_frozen_shared_custom_bot(
    team, fake
):
    bot_id = owned_bot(team, fake)
    # Not frozen yet: nothing to adopt. A member, a stranger and another workspace get 404.
    assert post(team, team.avery, bot_id, "adopt")[0] == 404
    remove(team, team.casey)
    for auth in (team.auth[team.blake], team.hub.other):
        assert team.hub.post(auth, f"/api/bots/{bot_id}/adopt", {})[0] == 404
    assert shared_ids(team, team.blake)[0] == 404
    assert post(team, team.avery, "no-such-bot", "adopt")[0] == 404
    assert team.hub.post(team.auth[team.avery], f"/api/bots/{bot_id}/adopt/extra", {})[0] == 404
    assert post(team, team.avery, bot_id, "adopt")[0] == 200
    assert post(team, team.avery, bot_id, "adopt")[0] == 404  # once: it is no longer ownerless
    # A bot that was never shared, and a default bot, cannot be adopted after their owner left.
    plain = make_bot(team, team.blake, "Plain")
    default = next(
        b for b in team.hub.bots(team.auth[team.blake])["bots"] if b["kind"] == "general"
    )
    remove(team, team.blake)
    assert post(team, team.avery, plain, "adopt")[0] == 404
    assert post(team, team.avery, default["id"], "adopt")[0] == 404
    assert rows(team, "SELECT frozen_at FROM bots WHERE id=?", plain)[0]["frozen_at"]


def test_a_re_added_owner_does_not_unfreeze_the_bot_and_loses_it_on_adoption(team, fake):
    bot_id = owned_bot(team, fake)
    remove(team, team.casey)
    readd(team, team.casey)
    cookie = team.auth[team.casey] = team.hub.cookie("casey@example.com", team.workspace)
    # Her own words stay readable to her, but the bot is frozen: no turn, no new share, no new grant.
    assert messages_of(team, team.casey, bot_id)[0] == 200
    status, _, _ = team.hub.post(
        cookie, f"/api/bots/{bot_id}/messages", {"text": "hi", "request_id": "request-0008"}
    )
    assert status == 409
    assert team.share(bot_id, team.blake, see_shared=True, auth=cookie)[0] == 409
    version = team.hub.get(cookie, f"/api/bots/{bot_id}/access")[1]["version"]
    assert (
        team.hub.request(
            cookie, "PUT", f"/api/bots/{bot_id}/access", {"version": version, "entries": []}
        )[0]
        == 409
    )
    assert post(team, team.avery, bot_id, "adopt")[0] == 200
    assert (
        team.thread(team.casey, bot_id)[0] == 404
    )  # not the owner any more, and not a member of it
    assert team.hub.get(cookie, f"/api/bots/{bot_id}/access")[0] == 404


def test_a_second_workspace_owner_can_adopt_a_bot_whose_owner_is_one(team, fake):
    owner = dana(team)
    bot_id = make_bot(team, owner, "Dana bot")
    share(team, bot_id, owner, team.blake, talk=True)
    remove(team, owner)
    assert post(team, team.avery, bot_id, "adopt")[0] == 200
    assert team.store.get(bot_id, team.avery)["owner_id"] == team.avery


# Disable and enable by a workspace owner


def test_a_workspace_owner_disables_a_shared_bot_and_enables_it_again(team, fake):
    bot_id = team.shared_bot()
    team.ask(fake, team.blake, bot_id, "Before")
    before = rows(team, "SELECT COUNT(*) AS n FROM bot_messages")[0]["n"]
    status, body, _ = post(team, team.avery, bot_id, "disable")
    assert (status, body) == (200, {"ok": True, "frozen": True})
    assert post(team, team.avery, bot_id, "disable")[0] == 200  # idempotent, one log row
    assert team.thread(team.blake, bot_id)[0] == 404
    assert bot_id not in [b["id"] for b in team.hub.bots(team.auth[team.blake])["bots"]]
    mine = {b["id"]: b for b in team.hub.bots(team.auth[team.avery])["bots"]}[bot_id]
    assert (mine["frozen"], mine["frozen_reason"]) == (True, "disabled")
    status, _, _ = team.hub.post(
        team.auth[team.avery],
        f"/api/bots/{bot_id}/messages",
        {"text": "hello", "request_id": "request-0007"},
    )
    assert status == 409  # no new turn for anyone, the owner included
    assert rows(team, "SELECT COUNT(*) AS n FROM bot_messages")[0]["n"] == before
    # A bot disabled by a workspace owner is not adoptable (its owner is still here).
    assert post(team, team.avery, bot_id, "adopt")[0] == 409
    assert post(team, team.avery, bot_id, "enable")[0] == 200
    assert post(team, team.avery, bot_id, "enable")[0] == 200
    assert team.texts(team.blake, bot_id) == ["Before", "Done."]  # nothing was lost
    log = team.store.access_log(bot_id, team.avery)
    freezes = [(r["ref"], r["mode_after"]) for r in log if r["action"] == "freeze"]
    assert freezes == [("disabled", "frozen"), ("disabled", "lifted")]
    audit = [
        r["reason"]
        for r in team.hub.hub.db.audit_entries(team.workspace)
        if r["action"] == "bot_lifecycle:set"
    ]
    assert sorted(audit) == [f"bot={bot_id} disable", f"bot={bot_id} enable"]


def test_disabling_stops_a_turn_in_flight(team, fake):
    bot_id = team.shared_bot()
    configure(team.hub)
    fake.hold_call = threading.Event()
    fake.script = [
        [call("create_work", "c1", project="docs", title="Weekly sync")],
        [("text", "Filed.")],
    ]
    stream = Stream(
        team.hub, team.auth[team.blake], bot_id, "File a note: Weekly sync", "request-0001"
    )
    events = [stream.next()]
    assert events[0][0] == "ack" and fake.entered.wait(3)
    assert post(team, team.avery, bot_id, "disable")[0] == 200
    fake.hold_call.set()
    while events[-1][0] not in ("done", "error"):
        events.append(stream.next())
    stream.close()
    assert events[-1][0] == "error" and final(events)["code"] == "revoked"
    assert final(events)["message"]["text"] == turn.SENTENCES["revoked"]
    assert not any(e == "action" for e, _ in events)  # nothing from a tool ran after the freeze
    assert len(fake.calls) == 1


def test_enabling_an_ownerless_bot_needs_an_adopter_and_disable_needs_a_shared_bot(team, fake):
    bot_id = owned_bot(team, fake)
    remove(team, team.casey)
    assert post(team, team.avery, bot_id, "enable")[0] == 409
    assert post(team, team.avery, bot_id, "disable")[0] == 200  # already frozen: no second row
    assert [
        r["ref"] for r in team.store.access_log(bot_id, team.casey) if r["action"] == "freeze"
    ] == ["owner_left"]
    plain = team.bot("Plain", grants=None)
    assert (
        post(team, team.avery, plain, "disable")[0] == 404
    )  # not shared: not the workspace owner's
    assert post(team, team.avery, plain, "adopt")[0] == 404


def test_disable_enable_and_the_shared_list_are_for_workspace_owners_in_a_web_session(team, fake):
    bot_id = team.shared_bot()
    for tail in ("disable", "enable", "adopt"):
        for auth in (team.auth[team.blake], team.auth[team.casey], team.hub.other):
            assert team.hub.post(auth, f"/api/bots/{bot_id}/{tail}", {})[0] == 404, tail
    assert team.hub.get(team.auth[team.blake], "/api/bots/shared")[0] == 404
    assert team.hub.get(team.hub.other, "/api/bots/shared")[1].get("bots") in ([], None)
    assert not rows(team, "SELECT 1 FROM bots WHERE frozen_at IS NOT NULL")


# The workspace owner's list


def test_the_workspace_owner_lists_every_shared_bot_without_threads(team, fake):
    mine = team.shared_bot(team.blake, name="Avery bot")
    theirs = owned_bot(team, fake)
    plain = team.bot("Plain", grants=None)
    status, body, _ = team.hub.get(team.auth[team.avery], "/api/bots/shared")
    assert status == 200
    listed = {b["id"]: b for b in body["bots"]}
    assert set(listed) == {mine, theirs} and plain not in listed  # only shared bots
    bot = listed[theirs]
    assert bot["owner"]["id"] == team.casey and bot["frozen"] is False
    assert [m["user"]["id"] for m in bot["members"]] == [team.blake]
    assert [(a["kind"], a["ref"], a["mode"]) for a in bot["access"]] == [
        ("project", "docs", "write"),
        ("tool", "list_work", "use"),
    ]
    text = json.dumps(body)
    assert SECRET not in text and "Blake asks" not in text
    assert not any(key in bot for key in ("messages", "threads", "thread", "instructions"))
    # Another workspace's owner sees none of it.
    status, other, _ = team.hub.get(team.hub.other, "/api/bots/shared")
    assert [b["id"] for b in other.get("bots", [])] == []


# Archived interplay


def test_an_archived_shared_bot_keeps_its_shares_reads_but_takes_no_new_turn(team, fake):
    bot_id = team.shared_bot()
    team.ask(fake, team.blake, bot_id, "Before archive")
    assert (
        team.hub.request(team.auth[team.avery], "PATCH", f"/api/bots/{bot_id}", {"archived": True})[
            0
        ]
        == 200
    )
    assert team.thread(team.blake, bot_id)[0] == 200  # threads stay readable, shares stay
    assert len(team.store.shares(bot_id, team.avery)) == 1
    status, _, _ = team.hub.post(
        team.auth[team.blake],
        f"/api/bots/{bot_id}/messages",
        {"text": "more", "request_id": "request-0009"},
    )
    assert status == 409  # no new turn
    # Lifecycle on an archived bot: its owner leaves, a workspace owner adopts, it stays archived.
    other = make_bot(team, team.casey, "Archived one")
    share(team, other, team.casey, team.blake, talk=True)
    team.hub.request(team.auth[team.casey], "PATCH", f"/api/bots/{other}", {"archived": True})
    remove(team, team.casey)
    assert post(team, team.avery, other, "adopt")[0] == 200
    assert team.store.get(other, team.avery)["archived"] is True
    assert team.thread(team.blake, other)[0] == 200  # still readable for the member
    assert (
        team.hub.request(team.auth[team.avery], "PATCH", f"/api/bots/{other}", {"archived": False})[
            0
        ]
        == 200
    )
    team.ask(fake, team.blake, other, "Alive again", request_id="request-0010")
    assert "Alive again" in team.texts(team.blake, other)


# Hook and sweep


def test_the_hook_creates_no_bots_file_and_a_workspace_without_shares_changes_nothing(team):
    gateway = team.hub.hub.gateway
    stranger = team.hub.hub.db.create_user("stranger@example.com")
    assert lifecycle.member_removed(gateway, "no-such-workspace", stranger, team.avery) is None
    assert not store.exists(gateway, "no-such-workspace")
    assert team.store.member_left(stranger, team.avery) == {"shares": 0, "frozen": 0}
    assert not rows(team, "SELECT 1 FROM bot_access_log WHERE member_id=?", stranger)
    assert not rows(team, "SELECT 1 FROM bot_access_log")  # nothing was written for the stranger


def test_a_removal_that_skipped_the_hook_is_caught_by_the_next_listing(team, fake):
    bot_id = owned_bot(team, fake)
    remove(team, team.casey, hook=False)  # a path that bypassed the hook
    assert shared_ids(team, team.avery) == (200, [bot_id])
    row = rows(team, "SELECT frozen_reason FROM bots WHERE id=?", bot_id)[0]
    assert row["frozen_reason"] == "owner_left"  # the list swept first
    other = team.shared_bot(team.blake, name="Second")
    remove(team, team.blake, hook=False)
    assert team.hub.get(team.auth[team.avery], "/api/bots")[0] == 200  # the roster sweeps too
    states = rows(team, "SELECT revoke_reason FROM bot_shares WHERE bot_id=?", other)
    assert states == [{"revoke_reason": "member_removed"}]


def test_a_member_removed_mid_turn_ends_the_turn_with_the_fixed_sentence(team, fake):
    bot_id = team.shared_bot()
    configure(team.hub)
    fake.hold_call = threading.Event()
    fake.script = [
        [call("create_work", "c1", project="docs", title="Weekly sync")],
        [("text", "Filed.")],
    ]
    stream = Stream(
        team.hub, team.auth[team.blake], bot_id, "File a note: Weekly sync", "request-0001"
    )
    events = [stream.next()]
    assert events[0][0] == "ack" and fake.entered.wait(3)
    remove(team, team.blake)
    fake.hold_call.set()
    while events[-1][0] not in ("done", "error"):
        events.append(stream.next())
    stream.close()
    assert events[-1][0] == "error" and final(events)["code"] == "revoked"
    assert not any(e == "action" for e, _ in events)
    assert len(fake.calls) == 1
    states = rows(team, "SELECT state FROM bot_messages WHERE role='bot'")
    assert states == [{"state": "failed:revoked"}]  # the fixed reply, no model text


def test_a_bot_disabled_before_its_owner_left_is_owner_left_and_cannot_be_enabled(team, fake):
    bot_id = owned_bot(team, fake)
    assert post(team, team.avery, bot_id, "disable")[0] == 200
    before = rows(team, "SELECT COUNT(*) AS n FROM bot_messages")[0]["n"]
    remove(team, team.casey)
    row = rows(team, "SELECT frozen_reason, frozen_at FROM bots WHERE id=?", bot_id)[0]
    assert row["frozen_reason"] == "owner_left" and row["frozen_at"]
    assert post(team, team.avery, bot_id, "enable")[0] == 409  # no live bot without an owner
    assert post(team, team.avery, bot_id, "adopt")[0] == 200
    assert rows(team, "SELECT user_id, frozen_at FROM bots WHERE id=?", bot_id) == [
        {"user_id": team.avery, "frozen_at": None}
    ]
    assert rows(team, "SELECT COUNT(*) AS n FROM bot_messages")[0]["n"] == before
    refs = [
        (r["ref"], r["mode_after"])
        for r in team.store.access_log(bot_id, team.avery)
        if r["action"] == "freeze"
    ]
    assert refs == [("disabled", "frozen"), ("owner_left", "frozen")]


def test_adopting_logs_the_adopters_own_ended_share(team, fake):
    bot_id = owned_bot(team, fake)
    share(team, bot_id, team.casey, team.avery, talk=True)
    remove(team, team.casey)
    assert post(team, team.avery, bot_id, "adopt")[0] == 200
    log = team.store.access_log(bot_id, team.avery)
    assert [(r["action"], r["member_id"]) for r in log[-2:]] == [
        ("unshare", team.avery),
        ("adopt", team.casey),
    ]
