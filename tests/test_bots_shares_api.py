"""Sharing a bot and members' private threads, over real HTTP with a scripted model.

Generic members: Avery owns the workspace (and reads a private project), Blake and Casey are
members who cannot. Another workspace and agent and app tokens are in the cast. A bot that is
not shared behaves as before; sharing is off until the workspace owner turns it on.
"""

import json
import threading

import pytest

from commonplace import sessions
from commonplace.bots import learned, turn
from commonplace.chat import model_stream
from tests._bots_helpers import bots_hub_fixture  # noqa: F401
from tests.test_bots_app_grant import app as app_token, bearer
from tests.test_bots_turn import (  # noqa: F401
    Fake,
    Stream,
    call,
    configure,
    confirm_tool,
    extra_tools,
)
from tests.test_commonplace_boards import service

NOTE = "zebra quartz lantern 7731"


def entry(kind, ref, mode):
    return {"kind": kind, "ref": ref, "mode": mode}


GRANTS = [
    entry("project", "docs", "write"),
    entry("project", "vault", "read"),
    entry("tool", "list_work", "use"),
    entry("tool", "create_work", "use"),
]
APPROVE = entry("tool", "approve_item", "use")


class Team:
    """Avery, Blake and Casey; projects ``docs`` (everyone) and ``vault`` (workspace owner only)."""

    def __init__(self, hub):
        self.hub = hub
        self.workspace = hub.hub.workspace
        self.avery, self.blake = hub.hub.admin, hub.member_user
        self.casey = hub.hub.db.create_user("casey@example.com")
        hub.hub.db.add_member(self.workspace, self.casey, "member", actor=self.avery)
        self.auth = {
            self.avery: hub.owner,
            self.blake: hub.member,
            self.casey: hub.cookie("casey@example.com", self.workspace),
        }
        boards = service(hub.hub, hub.person(self.avery))
        boards.board_add(slug="docs", name="Docs", wing="shared")
        boards.board_add(slug="vault", name="Vault", wing="private")
        self.store = hub.store()

    def switch(self, on=True, auth=None):
        return self.hub.request(
            auth or self.hub.owner, "PATCH", "/api/bots/settings", {"sharing": on}
        )

    def bot(self, name="Team", grants=GRANTS, board=None):
        body = {"name": name, **({"board": board} if board else {})}
        status, made, _ = self.hub.post(self.hub.owner, "/api/bots", body)
        assert status == 201, made
        bot_id = made["bot"]["id"]
        if grants is not None:
            self.grants(bot_id, grants)
        return bot_id

    def grants(self, bot_id, entries):
        # The first share may seed the board grant, so the version is read after sharing.
        version = self.hub.get(self.hub.owner, f"/api/bots/{bot_id}/access")[1].get("version", 0)
        return self.hub.request(
            self.hub.owner,
            "PUT",
            f"/api/bots/{bot_id}/access",
            {"version": version, "entries": entries},
        )

    def share(self, bot_id, user, talk=True, see_shared=False, auth=None):
        return self.hub.post(
            auth or self.hub.owner,
            f"/api/bots/{bot_id}/shares",
            {"user": user, "talk": talk, "see_shared": see_shared},
        )

    def shared_bot(self, *members, grants=GRANTS, name="Team", board=None):
        """A custom bot shared with ``members`` (talk) and granted ``grants``."""
        bot_id = self.bot(name, grants=None, board=board)
        for member in members or (self.blake,):
            status, body, _ = self.share(bot_id, member)
            assert status in (200, 201), body
        if grants is not None:
            status, body, _ = self.grants(bot_id, grants)
            assert status == 200, body
        return bot_id

    def ask(self, fake, user, bot_id, text, *calls, request_id="request-0001", suffix=""):
        configure(self.hub)
        fake.script = [list(calls), [("text", "Done.")]] if calls else [[("text", "Done.")]]
        return Stream(self.hub, self.auth[user], bot_id, text, request_id, suffix).until_end()

    def thread(self, user, bot_id, suffix=""):
        return self.hub.get(self.auth[user], f"/api/bots/{bot_id}/messages{suffix}")

    def texts(self, user, bot_id, suffix=""):
        status, body, _ = self.thread(user, bot_id, suffix)
        assert status == 200, body
        return [m["text"] for m in body["messages"]]


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


def outcomes(events):
    return [
        (d["tool"], d["state"], d.get("code"))
        for e, d in events
        if e == "action" and d["state"] != "running"
    ]


def final(events):
    return next(d for e, d in events if e in ("done", "error"))


def roster(team, user):
    status, body, _ = team.hub.get(team.auth[user], "/api/bots")
    assert status == 200, body
    return {b["id"]: b for b in body["bots"]}


# Default off, and a clean workspace


def test_a_clean_workspace_shares_nothing_until_the_switch_is_on(bots_hub):
    hub = bots_hub
    team = Team(hub)
    status, body, _ = hub.get(hub.member, "/api/bots/settings")
    assert (status, body) == (200, {"sharing": False, "can_configure": False})
    assert all(b["mine"] and not b["shared"] for b in roster(team, team.blake).values())
    bot_id = team.bot(grants=None)
    status, body, _ = team.share(bot_id, team.blake)
    assert (status, body["code"]) == (409, "sharing_off")
    assert team.store.shares(bot_id, team.avery) == []  # nothing was stored
    assert not team.store.shared_state(bot_id, team.avery)["shared"]
    assert team.store.access_log(bot_id, team.avery) == []
    assert bot_id not in roster(team, team.blake)
    assert team.thread(team.blake, bot_id)[0] == 404  # an id guessed from outside


def test_only_the_workspace_owner_turns_sharing_on_and_it_is_audited(bots_hub):
    hub = bots_hub
    team = Team(hub)
    assert team.switch(True, hub.member)[0] == 403
    assert hub.get(hub.member, "/api/bots/settings")[1]["sharing"] is False
    assert team.switch("yes")[0] == 400
    status, body, _ = team.switch(True)
    assert (status, body) == (200, {"sharing": True, "can_configure": True})
    assert hub.get(hub.member, "/api/bots/settings")[1] == {"sharing": True, "can_configure": False}
    assert team.switch(True)[0] == 200  # idempotent
    rows = [r for r in hub.hub.db.audit_entries(team.workspace) if r["action"] == "bots.sharing"]
    assert len(rows) == 1  # one row per real change


# Share, revoke, roster


def test_the_owner_shares_and_revokes_and_the_roster_shows_the_bot_to_the_member_only(team):
    hub = team.hub
    bot_id = team.bot(grants=None)
    status, body, _ = team.share(bot_id, team.blake)
    assert status == 201
    assert body["share"]["user"]["id"] == team.blake and body["share"]["talk"] is True
    assert team.share(bot_id, team.blake)[0] == 200  # the same share again: a no-op
    seen = roster(team, team.blake)[bot_id]
    assert (seen["mine"], seen["shared"], seen["owner"]["id"]) == (False, True, team.avery)
    assert seen["perms"] == {"talk": True, "see_shared": False} and seen["frozen"] is False
    assert roster(team, team.avery)[bot_id]["mine"] is True
    assert bot_id not in roster(team, team.casey)  # not shared with Casey
    assert team.thread(team.casey, bot_id)[0] == 404
    status, body, _ = hub.request(
        hub.owner, "PATCH", f"/api/bots/{bot_id}/shares/{team.blake}", {"see_shared": True}
    )
    assert status == 200 and body["share"]["see_shared"] is True
    status, body, _ = hub.request(
        hub.owner,
        "PATCH",
        f"/api/bots/{bot_id}/shares/{team.blake}",
        {"talk": False, "see_shared": False},
    )
    assert status == 400  # a share with neither flag is not storable
    status, _, _ = hub.request(hub.owner, "DELETE", f"/api/bots/{bot_id}/shares/{team.blake}")
    assert status == 200
    assert hub.request(hub.owner, "DELETE", f"/api/bots/{bot_id}/shares/{team.blake}")[0] == 200
    assert bot_id not in roster(team, team.blake)  # the roster drops it at once
    assert team.thread(team.blake, bot_id)[0] == 404
    assert hub.get(team.auth[team.blake], f"/api/bots/{bot_id}/shares")[0] == 404
    log = [r["action"] for r in team.store.access_log(bot_id, team.avery)]
    assert log == ["share", "permissions", "unshare"]  # one log row per real change
    audit = [r for r in hub.hub.db.audit_entries(team.workspace) if r["action"] == "bot_share:set"]
    assert len(audit) == 3 and all(NOTE not in json.dumps(dict(r)) for r in audit)


def test_shares_take_a_current_member_other_than_the_owner_and_only_from_the_owner(team):
    hub = team.hub
    bot_id = team.bot(grants=None)
    assert team.share(bot_id, team.avery)[0] == 400  # the owner is not a member to share with
    assert team.share(bot_id, "nobody")[0] == 400
    stranger = hub.hub.db.create_user("stranger@example.com")
    assert team.share(bot_id, stranger)[0] == 400  # not in this workspace
    assert (
        hub.post(hub.owner, f"/api/bots/{bot_id}/shares", {"user": team.blake, "extra": 1})[0]
        == 400
    )
    assert team.share(bot_id, team.blake, False, False)[0] == 400
    assert team.share(bot_id, team.blake)[0] == 201
    status, body, _ = team.share(bot_id, team.casey, auth=team.auth[team.blake])
    assert (status, body["code"]) == (403, "owner_only")  # a member who sees the bot
    mine = hub.get(team.auth[team.blake], f"/api/bots/{bot_id}/shares")[1]["shares"]
    assert [s["user"]["id"] for s in mine] == [team.blake]  # a member sees only their own row
    assert team.share(bot_id, team.casey, auth=team.auth[team.casey])[0] == 404  # not shared with
    for method, path, body in (
        ("PATCH", f"/api/bots/{bot_id}", {"name": "Mine now"}),
        ("PATCH", f"/api/bots/{bot_id}/shares/{team.blake}", {"talk": False, "see_shared": True}),
        ("DELETE", f"/api/bots/{bot_id}/shares/{team.blake}", None),
    ):
        status, got, _ = hub.request(team.auth[team.blake], method, path, body)
        assert (status, got["code"]) == (403, "owner_only"), path


def test_a_member_works_only_in_their_own_private_thread_and_the_bots_access_applies(team, fake):
    bot_id = team.shared_bot()
    events = team.ask(
        fake,
        team.blake,
        bot_id,
        "Look at docs and the vault",
        call("list_work", "c1", project="docs"),
        call("list_work", "c2", project="vault"),  # the bot may read it; Blake may not
        call("list_work", "c3", project="elsewhere"),
    )
    # The vault is not even offered to Blake's model as a project name: it is not his to read.
    assert outcomes(events)[:2] == [("list_work", "ok", None), ("list_work", "refused", "invalid")]
    assert final(events)["message"]["text"] == "Done."
    texts = team.texts(team.blake, bot_id)
    assert texts == ["Look at docs and the vault", "Done."]
    assert team.texts(team.avery, bot_id) == []  # the owner's original thread has nothing of Blake
    # The same call from the owner, who reads the vault, is within the bot's access.
    owner_events = team.ask(
        fake,
        team.avery,
        bot_id,
        "Look",
        call("list_work", "c1", project="vault"),
        request_id="request-0002",
    )
    assert outcomes(owner_events) == [("list_work", "ok", None)]
    assert "shared with a team" in fake.calls[0]["system"]


def test_a_read_grant_never_writes_even_for_a_member_who_may(team, fake):
    bot_id = team.shared_bot(
        grants=[entry("project", "docs", "read"), entry("tool", "create_work", "use")]
    )
    team.ask(
        fake,
        team.blake,
        bot_id,
        "File a note: Weekly sync",
        call("create_work", "c1", project="docs", title="Weekly sync"),
    )
    assert "create_work" not in {t["name"] for t in fake.calls[0]["tools"]}  # never offered
    boards = service(team.hub.hub, team.hub.person(team.avery))
    assert boards.task_list(board="docs", all_statuses=True) == []


# Isolation between members, and the owner


def test_member_a_cannot_read_or_use_member_bs_private_thread(team, fake):
    bot_id = team.shared_bot(team.blake, team.casey)
    team.ask(fake, team.blake, bot_id, "Blake private secret one", request_id="request-0001")
    team.ask(fake, team.casey, bot_id, "Casey private secret two", request_id="request-0001")
    blake_thread = team.hub.get(team.auth[team.blake], f"/api/bots/{bot_id}/threads")[1]["own"]
    casey_thread = team.hub.get(team.auth[team.casey], f"/api/bots/{bot_id}/threads")[1]["own"]
    assert blake_thread and casey_thread and blake_thread != casey_thread
    assert team.texts(team.blake, bot_id) == ["Blake private secret one", "Done."]
    assert team.texts(team.casey, bot_id) == ["Casey private secret two", "Done."]
    # Casey and the owner name Blake's thread on every route that takes one.
    for user in (team.casey, team.avery):
        auth, suffix = team.auth[user], f"?thread={blake_thread}"
        assert team.thread(user, bot_id, suffix)[0] == 404
        for tail, body in (
            ("messages", {"text": "hello", "request_id": "request-0009"}),
            ("read", {"upto": 99}),
            ("stop", {"request_id": "request-0001"}),
            ("confirm", {"pending": "x", "accept": True}),
        ):
            status, got, _ = team.hub.request(
                auth, "POST", f"/api/bots/{bot_id}/{tail}{suffix}", body
            )
            assert status == 404 and "secret" not in json.dumps(got), (user, tail)
        status, got, _ = team.hub.request(
            auth, "PATCH", f"/api/bots/{bot_id}/threads/{blake_thread}", {"archived": True}
        )
        assert status == 404
        listed = team.hub.get(auth, f"/api/bots/{bot_id}/threads")[1]
        assert blake_thread not in {t["id"] for t in listed["threads"]}
    # Nothing of one person's words shows in another's roster entry.
    assert "Blake" not in json.dumps(roster(team, team.casey)[bot_id]["last"])
    assert roster(team, team.avery)[bot_id]["last"] is None
    # The owner's turn never reads a member's thread either.
    team.ask(fake, team.avery, bot_id, "Owner asks", request_id="request-0003")
    sent = json.dumps(fake.calls[-1]["messages"])
    assert "secret" not in sent
    assert team.thread(team.blake, bot_id, f"?thread={casey_thread}")[0] == 404
    assert team.thread(team.blake, bot_id, "?thread=not-a-thread")[0] == 404


def test_notes_are_per_person_and_bot_level_notes_are_the_owners(team, fake):
    hub = team.hub
    bot_id = team.shared_bot(team.blake, team.casey)
    owner_note = "Always answer in short sentences"
    team.ask(
        fake,
        team.avery,
        bot_id,
        f"Remember this: {owner_note}",
        call("remember", "c1", text=owner_note),
    )
    team.ask(
        fake, team.blake, bot_id, f"Remember this: {NOTE}", call("remember", "c1", text=NOTE),
        request_id="request-0002",
    )  # fmt: skip
    assert (
        hub.get(team.auth[team.blake], f"/api/bots/{bot_id}/memory")[1]["memories"][0]["text"]
        == owner_note
    )
    own = hub.get(team.auth[team.blake], f"/api/bots/{bot_id}/memory?own=1")[1]["memories"]
    assert [n["text"] for n in own] == [NOTE]
    assert hub.get(team.auth[team.casey], f"/api/bots/{bot_id}/memory?own=1")[1]["memories"] == []
    bot_level = hub.get(hub.owner, f"/api/bots/{bot_id}/memory")[1]["memories"]
    assert [n["text"] for n in bot_level] == [owner_note]  # Blake's note is not the bot's
    assert NOTE not in json.dumps(hub.get(hub.owner, f"/api/bots/{bot_id}/memory?own=1")[1])
    # A turn reads bot-level notes and the asker's own, nobody else's.
    team.ask(fake, team.casey, bot_id, "Hello", request_id="request-0003")
    seen = json.dumps(fake.calls[-1]["messages"])
    assert owner_note in seen and NOTE not in seen
    team.ask(fake, team.blake, bot_id, "Hello again", request_id="request-0004")
    assert NOTE in json.dumps(fake.calls[-1]["messages"])
    # Bot-level notes are changed by the owner only; a member's own are theirs to remove.
    status, body, _ = hub.post(team.auth[team.blake], f"/api/bots/{bot_id}/memory", {"text": "x1"})
    assert (status, body["code"]) == (403, "owner_only")
    assert (
        hub.request(
            team.auth[team.blake], "DELETE", f"/api/bots/{bot_id}/memory/{bot_level[0]['id']}"
        )[0]
        == 403
    )
    assert (
        hub.request(
            team.auth[team.casey], "DELETE", f"/api/bots/{bot_id}/memory/{own[0]['id']}?own=1"
        )[0]
        == 404
    )
    assert (
        hub.request(
            team.auth[team.blake], "DELETE", f"/api/bots/{bot_id}/memory/{own[0]['id']}?own=1"
        )[0]
        == 200
    )
    assert hub.get(team.auth[team.blake], f"/api/bots/{bot_id}/memory?own=1")[1]["memories"] == []


def test_unread_is_per_person_and_thread(team, fake):
    bot_id = team.shared_bot(team.blake, team.casey)
    team.ask(fake, team.blake, bot_id, "Hello from Blake")
    assert roster(team, team.blake)[bot_id]["unread"] == 1
    assert roster(team, team.casey)[bot_id]["unread"] == 0
    assert roster(team, team.avery)[bot_id]["unread"] == 0
    newest = team.hub.get(team.auth[team.blake], f"/api/bots/{bot_id}/messages")[1]["messages"][-1][
        "id"
    ]
    status, _, _ = team.hub.post(
        team.auth[team.blake], f"/api/bots/{bot_id}/read", {"upto": newest}
    )
    assert status == 200 and roster(team, team.blake)[bot_id]["unread"] == 0
    assert roster(team, team.blake)[bot_id]["last"]["text"] == "Done."


# Revoke: the next request, and a turn in flight


def test_revoke_takes_effect_on_the_next_request_and_a_new_share_restores_access(team, fake):
    hub = team.hub
    bot_id = team.shared_bot()
    team.ask(fake, team.blake, bot_id, "Before the revoke")
    assert hub.request(hub.owner, "DELETE", f"/api/bots/{bot_id}/shares/{team.blake}")[0] == 200
    for method, tail, body in (
        ("GET", "messages", None),
        ("POST", "messages", {"text": "again", "request_id": "request-0002"}),
        ("GET", "memory?own=1", None),
        ("GET", "access", None),
        ("GET", "threads", None),
    ):
        status, _, _ = hub.request(
            team.auth[team.blake], method, f"/api/bots/{bot_id}/{tail}", body
        )
        assert status == 404, tail
    assert team.share(bot_id, team.blake)[0] == 201
    assert team.texts(team.blake, bot_id) == [
        "Before the revoke",
        "Done.",
    ]  # their thread is theirs again


def test_a_turn_in_flight_stops_when_the_share_is_revoked(team, fake):
    hub = team.hub
    bot_id = team.shared_bot()
    configure(hub)
    fake.hold_call = threading.Event()
    fake.script = [
        [call("create_work", "c1", project="docs", title="Weekly sync")],
        [("text", "Filed.")],
    ]
    stream = Stream(hub, team.auth[team.blake], bot_id, "File a note: Weekly sync", "request-0001")
    events = [stream.next()]
    assert events[0][0] == "ack" and fake.entered.wait(3)
    assert hub.request(hub.owner, "DELETE", f"/api/bots/{bot_id}/shares/{team.blake}")[0] == 200
    fake.hold_call.set()
    while events[-1][0] not in ("done", "error"):
        events.append(stream.next())
    stream.close()
    last = events[-1][1]
    assert events[-1][0] == "error" and last["code"] == "revoked"
    assert last["message"]["text"] == turn.SENTENCES["revoked"]  # a fixed sentence, no model text
    assert last["message"]["state"] == "failed:revoked" and last["message"]["blocks"] == []
    assert not any(e == "action" for e, _ in events)  # no tool ran after the share ended
    boards = service(hub.hub, hub.person(team.avery))
    assert boards.task_list(board="docs", all_statuses=True) == []
    assert len(fake.calls) == 1  # and no second model request was made
    assert team.share(bot_id, team.blake)[0] == 201
    status, body, _ = team.thread(team.blake, bot_id)
    assert [m["state"] for m in body["messages"]] == ["ok", "failed:revoked"]


def test_a_person_who_lost_workspace_access_reaches_nothing_at_once(team, fake):
    bot_id = team.shared_bot()
    team.ask(fake, team.blake, bot_id, "Hello")
    db = team.hub.hub.db
    with db._lock, db._tx() as conn:  # the removal hook is a later slice; the person is gone
        conn.execute(
            "UPDATE users SET disabled_at='2026-11-12T00:00:00Z' WHERE id=?", (team.blake,)
        )
    for status in (
        team.thread(team.blake, bot_id)[0],
        team.hub.get(team.auth[team.blake], "/api/bots")[0],
    ):
        assert status in (401, 404)


# Replay, concurrency, confirm


def test_a_request_id_is_per_thread_so_members_never_collide_or_replay_each_other(team, fake):
    bot_id = team.shared_bot(team.blake, team.casey)
    team.ask(fake, team.blake, bot_id, "Blake says one")
    team.ask(fake, team.casey, bot_id, "Casey says other")  # the same request id, another thread
    assert team.texts(team.casey, bot_id) == ["Casey says other", "Done."]
    status, body, _ = team.hub.post(
        team.auth[team.blake],
        f"/api/bots/{bot_id}/messages?stream=0",
        {"text": "Something else", "request_id": "request-0001"},
    )
    assert (status, body["code"]) == (409, "conflict")  # the same person's id, other text
    calls = len(fake.calls)
    again = Stream(
        team.hub, team.auth[team.blake], bot_id, "Blake says one", "request-0001", "?stream=0"
    )
    assert again.status == 200 and again.json()["reply"]["text"] == "Done."
    assert len(fake.calls) == calls  # replayed from the stored reply; the model was not asked
    assert team.texts(team.casey, bot_id) == ["Casey says other", "Done."]


def test_turns_run_per_thread_not_per_bot(team, fake):
    hub = team.hub
    bot_id = team.shared_bot(team.blake, team.casey)
    configure(hub)
    fake.hold_call = threading.Event()
    fake.script = [[("text", "One.")], [("text", "Two.")]]
    first = Stream(hub, team.auth[team.blake], bot_id, "Blake asks", "request-0001")
    assert first.next()[0] == "ack" and fake.entered.wait(3)
    second = Stream(hub, team.auth[team.casey], bot_id, "Casey asks", "request-0001")
    assert second.status == 200  # another thread of the same bot runs at the same time
    busy = hub.post(
        team.auth[team.blake],
        f"/api/bots/{bot_id}/messages",
        {"text": "Again", "request_id": "request-0002"},
    )
    assert busy[0] == 409 and busy[1]["code"] == "busy"  # but one turn per thread
    fake.hold_call.set()
    first.close()
    second.close()


def test_a_confirm_tap_is_bound_to_the_asking_person_and_thread(team, fake, confirm_tool):  # noqa: F811
    bot_id = team.shared_bot(team.blake, team.casey, grants=[*GRANTS, APPROVE])
    boards = service(team.hub.hub, team.hub.person(team.avery))
    first, second = (boards.task_add(title=t, board="docs")["number"] for t in ("One", "Two"))
    events = team.ask(fake, team.blake, bot_id, "Approve it", call("approve_item", "c1", ref=first))
    [chip] = [d for e, d in events if e == "block" and d["type"] == "confirm"]
    blake_thread = team.hub.get(team.auth[team.blake], f"/api/bots/{bot_id}/threads")[1]["own"]
    body = {"pending": chip["pending"], "accept": True}
    for user, suffix in (
        (team.casey, ""),  # her own thread
        (team.casey, f"?thread={blake_thread}"),  # Blake's thread
        (team.avery, ""),  # the owner
        (team.avery, f"?thread={blake_thread}"),
    ):
        status, got, _ = team.hub.request(
            team.auth[user], "POST", f"/api/bots/{bot_id}/confirm{suffix}", body
        )
        assert status == 404, (user, suffix)
    assert confirm_tool == []  # nothing ran for anyone else's tap
    own = team.ask(
        fake,
        team.casey,
        bot_id,
        "Approve mine",
        call("approve_item", "c1", ref=second),
        request_id="request-0002",
    )
    [mine] = [d for e, d in own if e == "block" and d["type"] == "confirm"]
    other = team.hub.post(
        team.auth[team.blake],
        f"/api/bots/{bot_id}/confirm",
        {"pending": mine["pending"], "accept": True},
    )
    assert other[0] == 404  # nor can Blake tap Casey's chip
    status, got, _ = team.hub.post(team.auth[team.blake], f"/api/bots/{bot_id}/confirm", body)
    assert status == 200
    [(runner, ran)] = confirm_tool  # run once, as the person who tapped
    assert (runner, ran["ref"]) == (team.blake, first)
    assert team.hub.post(team.auth[team.blake], f"/api/bots/{bot_id}/confirm", body)[0] == 409
    assert "Casey" not in json.dumps(got)
    assert team.thread(team.blake, bot_id)[1]["messages"][-1]["role"] == "event"
    assert [m["role"] for m in team.thread(team.casey, bot_id)[1]["messages"]] == ["user", "bot"]


# Events go to the thread of the person who filed


def test_a_filed_task_reports_to_the_filers_thread_and_stops_with_the_share(team, fake):
    hub = team.hub
    bot_id = team.shared_bot(team.blake, team.casey)
    team.ask(
        fake, team.blake, bot_id, "File a note: Weekly sync",
        call("create_work", "c1", project="docs", title="Weekly sync"),
    )  # fmt: skip
    boards = service(hub.hub, hub.person(team.avery))
    [card] = boards.task_list(board="docs", all_statuses=True)
    worker = service(hub.hub, hub.principal(hub.t2))
    worker.task_update(card["number"], status="doing")
    worker.task_update(card["number"], status="done", context="The work is complete and tested.")
    blake = team.thread(team.blake, bot_id)[1]["messages"]
    assert [m["role"] for m in blake] == ["user", "bot", "event"]
    assert "Weekly sync" in blake[-1]["text"]
    assert [m["role"] for m in team.thread(team.casey, bot_id)[1]["messages"]] == []
    assert team.texts(team.avery, bot_id) == []
    # Once the share has ended the event is dropped, and it does not come back with a new share.
    second = boards.task_add(title="Other", board="docs")["number"]
    team.store.work_add(
        bot_id,
        team.blake,
        "docs",
        second,
        blake[0]["id"],
        team.store.private_thread(bot_id, team.blake),
    )
    hub.request(hub.owner, "DELETE", f"/api/bots/{bot_id}/shares/{team.blake}")
    worker.task_update(second, status="doing")
    worker.task_update(second, status="done", context="The work is complete and tested.")
    team.share(bot_id, team.blake)
    assert [m["role"] for m in team.thread(team.blake, bot_id)[1]["messages"]] == [
        "user",
        "bot",
        "event",
    ]


# Threads


def test_private_threads_are_opened_once(team, fake):
    hub = team.hub
    bot_id = team.shared_bot(team.blake)
    blake = team.auth[team.blake]
    status, body, _ = hub.post(
        blake, f"/api/bots/{bot_id}/threads", {"visibility": "private", "title": "Plans"}
    )
    assert status == 201 and body["thread"]["title"] == "Plans"
    thread_id = body["thread"]["id"]
    assert hub.post(blake, f"/api/bots/{bot_id}/threads", {"visibility": "private"})[0] == 200
    assert hub.get(blake, f"/api/bots/{bot_id}/threads")[1]["own"] == thread_id
    assert hub.post(blake, f"/api/bots/{bot_id}/threads", {"visibility": "other"})[0] == 400
    assert (
        hub.post(hub.owner, f"/api/bots/{bot_id}/threads", {"visibility": "private"})[0] == 400
    )  # the owner has the original
    assert (
        hub.post(team.auth[team.casey], f"/api/bots/{bot_id}/threads", {"visibility": "shared"})[0]
        == 404
    )
    # An archived thread keeps its messages and takes no new one.
    team.ask(fake, team.blake, bot_id, "First")
    status, body, _ = hub.request(
        blake, "PATCH", f"/api/bots/{bot_id}/threads/{thread_id}", {"archived": True}
    )
    assert status == 200 and body["thread"]["archived"] is True
    status, body, _ = hub.post(
        blake,
        f"/api/bots/{bot_id}/messages?stream=0",
        {"text": "More", "request_id": "request-0002"},
    )
    assert (status, body["code"]) == (409, "conflict")
    assert team.texts(team.blake, bot_id) == ["First", "Done."]
    assert (
        hub.request(blake, "PATCH", f"/api/bots/{bot_id}/threads/{thread_id}", {"archived": False})[
            0
        ]
        == 200
    )
    assert team.ask(fake, team.blake, bot_id, "More", request_id="request-0002")[-1][0] == "done"


def test_a_member_without_talk_has_no_private_thread(team, fake):
    hub = team.hub
    bot_id = team.bot(grants=None)
    assert team.share(bot_id, team.blake, talk=False, see_shared=True)[0] == 201
    blake = team.auth[team.blake]
    assert team.thread(team.blake, bot_id)[0] == 404
    assert (
        hub.post(
            blake,
            f"/api/bots/{bot_id}/messages?stream=0",
            {"text": "Hi", "request_id": "request-0001"},
        )[0]
        == 404
    )
    assert hub.get(blake, f"/api/bots/{bot_id}/threads")[1] == {"threads": [], "own": None}
    assert roster(team, team.blake)[bot_id]["last"] is None


# Switch off, other workspaces, tokens, caps


def test_switching_sharing_off_suspends_every_share_and_on_resumes_it(team, fake):
    hub = team.hub
    bot_id = team.shared_bot()
    plain = team.bot("Plain", grants=None)
    team.ask(fake, team.blake, bot_id, "Hello")
    assert team.switch(False)[0] == 200
    assert team.thread(team.blake, bot_id)[0] == 404 and bot_id not in roster(team, team.blake)
    assert team.share(bot_id, team.casey)[1]["code"] == "sharing_off"
    assert (
        hub.get(hub.owner, f"/api/bots/{bot_id}/shares")[0] == 200
    )  # the owner's bot stays theirs
    assert (
        team.ask(fake, team.avery, plain, "Still works", request_id="request-0005")[-1][0] == "done"
    )
    assert team.switch(True)[0] == 200
    assert team.texts(team.blake, bot_id) == ["Hello", "Done."]  # nothing was changed or lost


def test_another_workspace_sees_nothing(team, fake):
    hub = team.hub
    bot_id = team.shared_bot()
    team.ask(fake, team.blake, bot_id, "Hello")
    stranger = hub.other
    assert hub.get(stranger, f"/api/bots/{bot_id}/messages")[0] == 404
    assert hub.get(stranger, f"/api/bots/{bot_id}/shares")[0] == 404
    assert hub.post(stranger, f"/api/bots/{bot_id}/shares", {"user": team.casey})[0] == 404
    assert bot_id not in {b["id"] for b in hub.get(stranger, "/api/bots")[1]["bots"]}
    assert hub.get(stranger, "/api/bots/settings")[1]["sharing"] is False  # its own switch
    blake_thread = hub.get(team.auth[team.blake], f"/api/bots/{bot_id}/threads")[1]["own"]
    assert hub.get(stranger, f"/api/bots/{bot_id}/messages?thread={blake_thread}")[0] == 404
    # A person in two workspaces reaches a bot only in the workspace it lives in.
    hub.hub.db.add_member(hub.other_workspace, team.blake, "member", actor=hub.other_user)
    elsewhere = hub.cookie("member@example.com", hub.other_workspace)
    assert hub.get(elsewhere, f"/api/bots/{bot_id}/messages")[0] == 404
    assert bot_id not in {b["id"] for b in hub.get(elsewhere, "/api/bots")[1]["bots"]}


def test_agent_tokens_get_404_and_an_app_never_changes_sharing(team, fake):
    hub = team.hub
    bot_id = team.shared_bot()
    for method, path, body in (
        ("GET", f"/api/bots/{bot_id}/shares", None),
        ("POST", f"/api/bots/{bot_id}/shares", {"user": team.casey, "talk": True}),
        ("DELETE", f"/api/bots/{bot_id}/shares/{team.blake}", None),
        ("GET", f"/api/bots/{bot_id}/threads", None),
        ("GET", "/api/bots/settings", None),
        ("PATCH", "/api/bots/settings", {"sharing": False}),
    ):
        for token in (hub.t1, hub.tm):
            status, _, _ = hub.request(token, method, path, body)
            assert status == 404, (method, path)
    token, _cid = app_token(hub, team.avery, "phone")  # the owner's own app, with bots:chat
    owner_app = bearer(token)
    assert hub.get(owner_app, f"/api/bots/{bot_id}/shares")[0] == 200
    for method, path, body in (
        ("POST", f"/api/bots/{bot_id}/shares", {"user": team.casey, "talk": True}),
        ("PATCH", f"/api/bots/{bot_id}/shares/{team.blake}", {"see_shared": True}),
        ("DELETE", f"/api/bots/{bot_id}/shares/{team.blake}", None),
        ("PATCH", "/api/bots/settings", {"sharing": False}),
    ):
        assert hub.request(owner_app, method, path, body)[0] == 404, (method, path)
    assert hub.get(hub.owner, "/api/bots/settings")[1]["sharing"] is True
    assert [
        s["user"]["id"] for s in hub.get(hub.owner, f"/api/bots/{bot_id}/shares")[1]["shares"]
    ] == [team.blake]
    # A member's own app acts as that member, through their live share only.
    blake_token, _ = app_token(hub, team.blake, "blake-phone")
    assert bot_id in {b["id"] for b in hub.get(bearer(blake_token), "/api/bots")[1]["bots"]}
    hub.request(hub.owner, "DELETE", f"/api/bots/{bot_id}/shares/{team.blake}")
    assert bot_id not in {b["id"] for b in hub.get(bearer(blake_token), "/api/bots")[1]["bots"]}
    assert hub.get(bearer(blake_token), f"/api/bots/{bot_id}/messages")[0] == 404


def test_the_caps_on_shares_see_shared_members_and_shared_bots(team):
    hub, db = team.hub, team.hub.hub.db
    bot_id = team.bot(grants=None)
    members = []
    for n in range(50):
        user = db.create_user(f"member{n}@example.com")
        db.add_member(team.workspace, user, "member", actor=team.avery)
        members.append(user)
    for user in members[:25]:
        assert team.share(bot_id, user, talk=True, see_shared=True)[0] == 201
    status, body, _ = team.share(bot_id, members[25], talk=True, see_shared=True)
    assert (status, body["code"]) == (409, "limit")  # at most 25 members who read shared threads
    for user in members[25:]:
        assert team.share(bot_id, user, talk=True)[0] == 201  # 50 shares in all
    status, body, _ = team.share(bot_id, team.casey)
    assert (status, body["code"]) == (409, "limit")
    assert hub.request(hub.owner, "DELETE", f"/api/bots/{bot_id}/shares/{members[0]}")[0] == 200
    assert team.share(bot_id, team.casey)[0] == 201  # a revoked share frees its place
    for n in range(19):
        assert team.share(team.bot(f"Extra {n}", grants=None), team.blake)[0] == 201
    status, body, _ = team.share(team.bot("One too many", grants=None), team.blake)
    assert (status, body["code"]) == (409, "limit")  # at most 20 shared bots per owner


def test_a_bot_that_is_not_shared_behaves_as_before(team, fake):
    plain = team.bot("Plain", grants=None)
    events = team.ask(fake, team.avery, plain, "Hello", call("list_work", "c1", project="vault"))
    assert outcomes(events) == [("list_work", "ok", None)]  # no access list for an unshared bot
    assert "shared with a team" not in fake.calls[0]["system"]
    assert team.thread(team.blake, plain)[0] == 404
    assert sessions.person_principal(team.hub.hub.db, team.blake, team.workspace) is not None


def test_learned_items_stay_with_their_person_and_never_reach_a_members_turn(team, fake):
    bot_id = team.shared_bot(team.blake)
    keep = "Please keep answers short and in plain language."
    items = learned.open_learned(team.hub.hub.gateway, team.workspace)
    items.set_policy(learned.WORKSPACE, team.avery, enabled=True)
    items.configure(bot_id, team.avery, mode="auto")
    source = team.store.add_user_message(bot_id, team.avery, keep, "request-9001")["id"]
    items.add_item(
        team.avery,
        bot_id,
        kind="preference",
        topic="answer style",
        text=keep,
        source_message_id=source,
    )
    team.ask(fake, team.avery, bot_id, "How long are answers?", request_id="request-0001")
    assert keep in json.dumps(fake.calls[-1]["messages"])  # the owner's own turn uses it
    team.ask(fake, team.blake, bot_id, "How long are answers?", request_id="request-0001")
    assert keep not in json.dumps(fake.calls[-1]["messages"])
    assert keep not in fake.calls[-1]["system"]
    assert keep not in json.dumps(team.thread(team.blake, bot_id)[1])
