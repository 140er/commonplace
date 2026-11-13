"""Shared threads and the audience rule, over real HTTP with a scripted model.

Generic members: Avery owns the workspace and the bot (and reads a private project), Blake and
Casey are members who cannot. A shared thread is read by every member with ``see_shared``, so a
turn in it reaches only what the bot may use, the talker, the owner and each of those readers may
use. Default off is real: sharing is a workspace switch and a bot that is not shared has no shared
threads.
"""

import json
import threading

import pytest

from commonplace.bots import access, shares, tools, updates
from commonplace.chat import model_stream
from tests._bots_helpers import bots_hub_fixture  # noqa: F401
from tests.test_bots_shares_api import APPROVE, GRANTS, Team, entry, final, outcomes
from tests.test_bots_turn import (  # noqa: F401
    Fake,
    Stream,
    call,
    configure,
    confirm_tool,
    extra_tools,
)
from tests.test_commonplace_boards import service

VAULT_NOTE = "VAULTNOTE-8812"
WRITE_GRANTS = [
    entry("project", "docs", "write"),
    entry("project", "vault", "write"),
    entry("tool", "list_work", "use"),
    entry("tool", "create_work", "use"),
]


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


def open_shared(team, user, bot_id, title=""):
    status, body, _ = team.hub.post(
        team.auth[user], f"/api/bots/{bot_id}/threads", {"visibility": "shared", "title": title}
    )
    assert status == 201, body
    assert body["thread"]["visibility"] == "shared" and body["thread"]["title"] == title
    return body["thread"]["id"]


def at(thread_id):
    return f"?thread={thread_id}"


def readers(team, bot_id, user, thread_id):
    """The messages ``user`` reads in a shared thread: (status, [(role, text)])."""
    status, body, _ = team.thread(user, bot_id, at(thread_id))
    if status != 200:
        return status, []
    return status, [(m["role"], m["text"]) for m in body["messages"]]


def say(team, fake, user, bot_id, thread_id, text, reply, *calls, request_id="request-0001"):
    """One turn in a shared thread whose scripted reply is ``reply``."""
    configure(team.hub)
    fake.script = [list(calls), [("text", reply)]] if calls else [[("text", reply)]]
    return Stream(team.hub, team.auth[user], bot_id, text, request_id, at(thread_id)).until_end()


def put_shared(team, bot_id, user, talk=True, see_shared=True):
    status, body, _ = team.share(bot_id, user, talk=talk, see_shared=see_shared)
    assert status in (200, 201), body


# Default off, and a clean workspace


def test_a_clean_workspace_has_no_shared_threads_until_the_bot_is_shared_and_the_switch_is_on(
    bots_hub,
):
    team = Team(bots_hub)  # the workspace switch is off
    bot_id = team.bot(grants=None)
    owner = bots_hub.owner
    status, body, _ = bots_hub.post(owner, f"/api/bots/{bot_id}/threads", {"visibility": "shared"})
    assert status == 400  # an unshared bot has no audience, so no shared thread
    assert team.store.threads(bot_id, team.avery) == []
    assert bots_hub.get(owner, f"/api/bots/{bot_id}/threads")[1]["threads"] == []
    assert team.switch(True)[0] == 200
    assert team.share(bot_id, team.blake, talk=True, see_shared=True)[0] == 201
    tid = open_shared(team, team.blake, bot_id, "Plans")
    assert [t["id"] for t in bots_hub.get(owner, f"/api/bots/{bot_id}/threads")[1]["threads"]] == [
        tid
    ]
    # Switching sharing off suspends the thread for members without changing it.
    assert team.switch(False)[0] == 200
    blake = team.auth[team.blake]
    assert bots_hub.get(blake, f"/api/bots/{bot_id}/threads")[0] == 404
    assert team.thread(team.blake, bot_id, at(tid))[0] == 404
    assert bots_hub.post(blake, f"/api/bots/{bot_id}/threads", {"visibility": "shared"})[0] == 404
    assert team.switch(True)[0] == 200
    assert team.thread(team.blake, bot_id, at(tid))[0] == 200  # and it resumes


def test_a_default_bot_and_a_bot_nobody_shared_with_cannot_open_shared_threads(team):
    plain = team.bot("Plain", grants=None)
    assert (
        team.hub.post(team.hub.owner, f"/api/bots/{plain}/threads", {"visibility": "shared"})[0]
        == 400
    )
    for slug, bot in team.hub.by_board(team.hub.owner).items():
        if bot["kind"] != "custom":
            assert (
                team.hub.post(
                    team.hub.owner, f"/api/bots/{bot['id']}/threads", {"visibility": "shared"}
                )[0]
                == 400
            ), slug
    # A member of another workspace, or without a share, gets 404 as for an unknown id.
    shared = team.shared_bot(team.blake)
    for auth in (team.auth[team.casey], team.hub.other):
        assert (
            team.hub.post(auth, f"/api/bots/{shared}/threads", {"visibility": "shared"})[0] == 404
        )


# Who reads what: see_shared, talk and history_from


def test_see_shared_reads_from_its_history_from_and_talk_alone_reads_only_threads_it_opened(
    team, fake
):
    bot_id = team.shared_bot(team.blake)  # Blake: talk only
    tid = open_shared(team, team.blake, bot_id, "Plans")
    team.ask(fake, team.blake, bot_id, "First shared words", suffix=at(tid))
    assert readers(team, bot_id, team.blake, tid)[1] == [
        ("user", "First shared words"),
        ("bot", "Done."),
    ]
    # Casey is shared in later and may only read: she gets nothing from before her share.
    put_shared(team, bot_id, team.casey, talk=False, see_shared=True)
    assert readers(team, bot_id, team.casey, tid) == (200, [])
    team.ask(
        fake, team.blake, bot_id, "Second shared words", suffix=at(tid), request_id="request-0002"
    )
    assert readers(team, bot_id, team.casey, tid)[1] == [
        ("user", "Second shared words"),
        ("bot", "Done."),
    ]
    # The model of a later turn is not handed what a late reader cannot read.
    assert "First shared words" not in json.dumps(fake.calls[-1]["messages"])
    # Casey reads and may not write, nor open threads of her own.
    status, body, _ = team.hub.post(
        team.auth[team.casey],
        f"/api/bots/{bot_id}/messages?stream=0&thread={tid}",
        {"text": "May I?", "request_id": "request-0003"},
    )
    assert (status, body["code"]) == (403, "read_only")
    assert (
        team.hub.post(
            team.auth[team.casey], f"/api/bots/{bot_id}/threads", {"visibility": "shared"}
        )[0]
        == 404
    )
    assert (
        team.hub.post(
            team.auth[team.casey], f"/api/bots/{bot_id}/threads", {"visibility": "private"}
        )[0]
        == 404
    )
    assert team.texts(team.casey, bot_id, at(tid))[:1] == ["Second shared words"]
    # A thread the owner opened is not Blake's to read: talk alone reads only what it opened.
    owner_thread = open_shared(team, team.avery, bot_id, "Owner")
    team.ask(
        fake, team.avery, bot_id, "Owner words", suffix=at(owner_thread), request_id="request-0004"
    )
    assert team.thread(team.blake, bot_id, at(owner_thread))[0] == 404
    blake_list = team.hub.get(team.auth[team.blake], f"/api/bots/{bot_id}/threads")[1]
    assert [t["id"] for t in blake_list["threads"]] == [tid]
    casey_list = team.hub.get(team.auth[team.casey], f"/api/bots/{bot_id}/threads")[1]
    assert {t["id"] for t in casey_list["threads"]} == {tid, owner_thread}
    assert casey_list["own"] is None  # without talk there is no private thread
    assert readers(team, bot_id, team.casey, owner_thread)[1][0] == ("user", "Owner words")
    # Revoking and sharing again starts a new history: older shared messages stay hidden.
    assert (
        team.hub.request(team.hub.owner, "DELETE", f"/api/bots/{bot_id}/shares/{team.casey}")[0]
        == 200
    )
    assert team.thread(team.casey, bot_id, at(tid))[0] == 404
    put_shared(team, bot_id, team.casey, talk=False, see_shared=True)
    assert readers(team, bot_id, team.casey, tid) == (200, [])
    assert readers(team, bot_id, team.casey, owner_thread) == (200, [])
    team.ask(
        fake, team.blake, bot_id, "Third shared words", suffix=at(tid), request_id="request-0005"
    )
    assert readers(team, bot_id, team.casey, tid)[1][0] == ("user", "Third shared words")


def test_a_member_with_both_flags_reads_every_shared_thread_and_writes_in_it(team, fake):
    bot_id = team.shared_bot(team.blake, team.casey)
    put_shared(team, bot_id, team.blake)
    tid = open_shared(team, team.casey, bot_id)  # opened by Casey, who has talk only
    team.ask(fake, team.casey, bot_id, "Casey opens", suffix=at(tid))
    team.ask(fake, team.blake, bot_id, "Blake joins in", suffix=at(tid), request_id="request-0002")
    assert team.texts(team.blake, bot_id, at(tid)) == [
        "Casey opens",
        "Done.",
        "Blake joins in",
        "Done.",
    ]
    assert team.thread(team.blake, bot_id, at(tid))[0] == 200
    # The thread is the opener's to archive; an archived one takes no new message.
    blake, casey = team.auth[team.blake], team.auth[team.casey]
    path = f"/api/bots/{bot_id}/threads/{tid}"
    assert team.hub.request(blake, "PATCH", path, {"archived": True})[0] == 404
    assert team.hub.request(casey, "PATCH", path, {"archived": True})[1]["thread"]["archived"]
    status, body, _ = team.hub.post(
        blake,
        f"/api/bots/{bot_id}/messages?stream=0&thread={tid}",
        {"text": "More", "request_id": "request-0003"},
    )
    assert (status, body["code"]) == (409, "conflict")


def test_messages_carry_their_author_and_one_member_cannot_replay_or_stop_anothers_turn(team, fake):
    hub = team.hub
    bot_id = team.shared_bot(team.blake, team.casey)
    put_shared(team, bot_id, team.blake)
    put_shared(team, bot_id, team.casey)
    tid = open_shared(team, team.blake, bot_id)
    team.ask(fake, team.blake, bot_id, "Blake speaks", suffix=at(tid), request_id="request-0001")
    team.ask(fake, team.casey, bot_id, "Casey speaks", suffix=at(tid), request_id="request-0002")
    team.ask(fake, team.avery, bot_id, "Avery speaks", suffix=at(tid), request_id="request-0003")
    body = team.thread(team.casey, bot_id, at(tid))[1]
    authors = [
        (m["text"], m.get("author", {}).get("id")) for m in body["messages"] if m["role"] == "user"
    ]
    assert authors == [
        ("Blake speaks", team.blake),
        ("Casey speaks", team.casey),
        ("Avery speaks", team.avery),
    ]
    assert all("author" not in m for m in body["messages"] if m["role"] != "user")
    # The model reads another member's words as that member's, never as the asker's.
    assert "another member" in json.dumps(fake.calls[-1]["messages"])
    # An id another member used in this thread is theirs: no replay, no rerun.
    calls = len(fake.calls)
    status, got, _ = hub.post(
        team.auth[team.casey],
        f"/api/bots/{bot_id}/messages?stream=0&thread={tid}",
        {"text": "Blake speaks", "request_id": "request-0001"},
    )
    assert (status, got["code"]) == (409, "conflict") and len(fake.calls) == calls
    # Another reader cannot stop a turn that is not theirs.
    configure(hub)
    fake.hold_call = threading.Event()
    fake.script = [[("text", "Slow.")]]
    running = Stream(hub, team.auth[team.blake], bot_id, "Slow one", "request-0004", at(tid))
    assert running.next()[0] == "ack" and fake.entered.wait(3)
    stop = f"/api/bots/{bot_id}/stop{at(tid)}"
    other = hub.post(team.auth[team.casey], stop, {"request_id": "request-0004"})
    assert other[0] == 200 and other[1] == {"ok": False}
    assert hub.post(team.auth[team.blake], stop, {"request_id": "request-0004"})[1] == {"ok": True}
    fake.hold_call.set()
    running.close()


# The audience rule


def leak_cast(team, fake):
    """A board-less bot with the vault granted; Avery reads it, Blake (a reader) cannot."""
    bot_id = team.shared_bot(team.blake, team.casey, grants=GRANTS)
    put_shared(team, bot_id, team.blake)  # Blake reads shared threads; Casey only talks
    return bot_id, open_shared(team, team.avery, bot_id, "Owner thread")


def tool_names(request, name="list_work"):
    spec = next(t for t in request["tools"] if t["name"] == name)
    return spec["schema"]["properties"]["project"]["enum"]


def test_a_shared_turn_never_reaches_a_project_a_reader_cannot_read(team, fake):
    bot_id, tid = leak_cast(team, fake)
    # In the owner's own thread the vault is hers to use...
    events = team.ask(
        fake, team.avery, bot_id, "Vault please", call("list_work", "c1", project="vault")
    )
    assert outcomes(events) == [("list_work", "ok", None)]
    assert "vault" in tool_names(fake.calls[0])
    # ...in a thread Blake reads it is not offered, named or used, and the refusal is fixed text.
    events = team.ask(
        fake,
        team.avery,
        bot_id,
        "Vault in the shared thread",
        call("list_work", "c1", project="vault"),
        call("list_work", "c2", project="docs"),
        request_id="request-0002",
        suffix=at(tid),
    )
    assert outcomes(events) == [
        ("list_work", "refused", "audience_limited"),
        ("list_work", "ok", None),
    ]
    first, second = fake.calls[-2], fake.calls[-1]
    assert tool_names(first) == ["docs"]
    assert (
        "vault" not in first["system"].lower() and "vault" not in json.dumps(first["tools"]).lower()
    )
    assert access.SENTENCES["audience_limited"] in json.dumps(second["messages"])
    assert final(events)["message"]["text"] == "Done."
    assert "Vault in the shared thread" in " ".join(team.texts(team.blake, bot_id, at(tid)))
    # Asking what it may reach says the same fixed sentence, and nothing about other projects.
    team.ask(
        fake,
        team.avery,
        bot_id,
        "What can you reach?",
        call("my_access", "c1", project="vault"),
        call("my_access", "c2"),
        request_id="request-0003",
        suffix=at(tid),
    )
    result = json.dumps(fake.calls[-1]["messages"])
    assert access.SENTENCES["audience_limited"] in result
    assert "vault" not in json.dumps(fake.calls[-1]["tools"]).lower()


def test_a_member_removed_mid_thread_widens_the_next_turn_and_a_new_one_narrows_it_again(
    team, fake
):
    bot_id, tid = leak_cast(team, fake)
    ask = lambda request_id: team.ask(  # noqa: E731
        fake,
        team.avery,
        bot_id,
        "Vault?",
        call("list_work", "c1", project="vault"),
        request_id=request_id,
        suffix=at(tid),
    )
    assert outcomes(ask("request-0001")) == [("list_work", "refused", "audience_limited")]
    assert (
        team.hub.request(team.hub.owner, "DELETE", f"/api/bots/{bot_id}/shares/{team.blake}")[0]
        == 200
    )
    assert outcomes(ask("request-0002")) == [("list_work", "ok", None)]  # no reader is left
    assert team.thread(team.blake, bot_id, at(tid))[0] == 404
    put_shared(team, bot_id, team.blake)
    assert outcomes(ask("request-0003")) == [("list_work", "refused", "audience_limited")]
    # A reader who left the workspace reads nothing, so they no longer narrow the thread.
    db = team.hub.hub.db
    with db._lock, db._tx() as conn:
        conn.execute(
            "UPDATE users SET disabled_at='2026-11-12T00:00:00Z' WHERE id=?", (team.blake,)
        )
    assert outcomes(ask("request-0004")) == [("list_work", "ok", None)]


def test_a_member_added_later_never_reads_nor_triggers_what_the_thread_said_before(team, fake):
    bot_id = team.shared_bot(team.casey, grants=GRANTS)
    tid = open_shared(team, team.avery, bot_id)
    say(
        team, fake, team.avery, bot_id, tid, "Read the vault", f"Vault says: {VAULT_NOTE}",
        call("list_work", "c1", project="vault"),
    )  # fmt: skip
    assert VAULT_NOTE in " ".join(t for _r, t in readers(team, bot_id, team.avery, tid)[1])
    put_shared(team, bot_id, team.blake)
    assert readers(team, bot_id, team.blake, tid) == (200, [])  # nothing from before the share
    team.ask(fake, team.avery, bot_id, "Anything else?", suffix=at(tid), request_id="request-0002")
    assert VAULT_NOTE not in json.dumps(fake.calls[-1]["messages"])  # not carried into the model
    assert readers(team, bot_id, team.blake, tid)[1] == [
        ("user", "Anything else?"),
        ("bot", "Done."),
    ]
    assert VAULT_NOTE not in json.dumps(team.thread(team.blake, bot_id, at(tid))[1])


def test_a_member_who_joins_while_a_turn_runs_holds_the_reply_back_and_stops_the_tools(team, fake):
    hub = team.hub
    bot_id = team.shared_bot(team.casey, grants=GRANTS)
    tid = open_shared(team, team.avery, bot_id)
    configure(hub)
    fake.hold_call = threading.Event()
    fake.script = [[call("list_work", "c1", project="vault")], [("text", f"Vault: {VAULT_NOTE}")]]
    stream = Stream(hub, team.auth[team.avery], bot_id, "Read the vault", "request-0001", at(tid))
    events = [stream.next()]
    assert events[0][0] == "ack" and fake.entered.wait(3)
    put_shared(team, bot_id, team.blake)  # Blake cannot read the vault and now reads the thread
    fake.hold_call.set()
    while events[-1][0] not in ("done", "error"):
        events.append(stream.next())
    stream.close()
    assert events[-1][0] == "error" and events[-1][1]["code"] == "audience"
    assert ("action", "refused") in [(e, d.get("state")) for e, d in events if e == "action"]
    assert not any(d.get("state") == "ok" for e, d in events if e == "action")  # no tool ran
    stored = events[-1][1]["message"]
    assert (
        stored["text"]
        == "Someone joined this shared thread while I worked, so I held this answer back."
    )
    assert stored["state"] == "failed:audience" and stored["blocks"] == []
    assert VAULT_NOTE not in json.dumps(team.thread(team.blake, bot_id, at(tid))[1])


def test_blocks_hydrate_with_each_readers_own_rights_and_events_carry_no_titles(team, fake):
    hub = team.hub
    bot_id = team.shared_bot(team.casey, grants=WRITE_GRANTS)
    tid = open_shared(team, team.avery, bot_id)
    # Filed while only Casey (who talks only) shares the bot: the vault is in reach.
    say(
        team, fake, team.avery, bot_id, tid, "File a note: Secret plan", "Filed.",
        call("create_work", "c1", project="vault", title="Secret plan"),
    )  # fmt: skip
    boards = service(hub.hub, hub.person(team.avery))
    [secret] = boards.task_list(board="vault", all_statuses=True)
    put_shared(team, bot_id, team.blake)  # Blake reads shared threads but not the vault
    say(
        team, fake, team.avery, bot_id, tid, "File a note: Weekly sync", "Filed.",
        call("create_work", "c1", project="docs", title="Weekly sync"), request_id="request-0002",
    )  # fmt: skip
    [docs_card] = boards.task_list(board="docs", all_statuses=True)
    for card in (secret["number"], docs_card["number"]):
        boards.task_update(card, status="doing")
        boards.task_update(card, status="done", context="The work is complete and tested.")
    for user in (team.avery, team.blake):
        body = team.thread(user, bot_id, at(tid))[1]
        events = [m for m in body["messages"] if m["role"] == "event"]
        assert [m["text"] for m in events] == [updates.SHARED_WORK_EVENT] * 2
        assert all([b["type"] for b in m["blocks"]] == ["work"] for m in events)
        dumped = json.dumps(events)
        assert "Weekly sync" in dumped or user == team.blake  # the block, never the text
    blake = team.thread(team.blake, bot_id, at(tid))[1]["messages"]
    by_card = {b["card"]: b for m in blake if m["role"] == "event" for b in m["blocks"]}
    assert by_card[secret["number"]].get("gone") is True  # hydrated with Blake's rights
    assert "Secret plan" not in json.dumps(blake)
    assert by_card[docs_card["number"]]["title"] == "Weekly sync"
    avery = team.thread(team.avery, bot_id, at(tid))[1]["messages"]
    titles = {b["card"]: b.get("title") for m in avery if m["role"] == "event" for b in m["blocks"]}
    assert titles == {secret["number"]: "Secret plan", docs_card["number"]: "Weekly sync"}
    assert all(
        "title" not in m["text"].lower() and "plan" not in m["text"].lower()
        for m in blake
        if m["role"] == "event"
    )


def test_an_answer_to_a_forwarded_post_is_not_copied_into_a_shared_thread(team, fake):
    hub = team.hub
    bot_id = team.shared_bot(team.casey, grants=WRITE_GRANTS)
    put_shared(team, bot_id, team.blake)
    tid = open_shared(team, team.avery, bot_id)
    assert hub.request(hub.owner, "PATCH", "/api/boards/docs", {"coordinator": "a1"})[0] == 200
    owner = service(hub.hub, hub.person(team.avery))
    coordinator = service(hub.hub, hub.principal(hub.t1))
    for text, where in (("Forward to the project", tid), ("Forward from the owner thread", None)):
        posted = owner.space_post("docs", text)["id"]
        message = team.store.add_user_message(
            bot_id, team.avery, text, f"fw-{posted:04d}0", thread_id=where
        )
        team.store.forward_add(bot_id, team.avery, "docs", posted, message["id"], thread_id=where)
        coordinator.space_post("docs", f"The reply holds {VAULT_NOTE} for {text}")
    shared = team.thread(team.blake, bot_id, at(tid))[1]["messages"]
    assert [m["text"] for m in shared if m["role"] == "event"] == [updates.SHARED_REPLY_EVENT]
    assert VAULT_NOTE not in json.dumps(shared)  # the reply is read in the project, not copied
    # The owner's original thread still gets the reply as written, marked as the orchestrator's.
    original = [m for m in team.thread(team.avery, bot_id)[1]["messages"] if m["role"] == "event"]
    assert [m["text"] for m in original] == [
        f"The reply holds {VAULT_NOTE} for Forward from the owner thread"
    ]
    assert original[0]["blocks"] == [{"type": "orchestrator"}]


# Notes and learned items belong to a person, not to a thread several people read


def test_a_members_own_notes_stay_out_of_a_shared_thread_but_bot_level_notes_stay_in(team, fake):
    bot_id = team.shared_bot(team.blake)
    hub = team.hub
    own = "zebra quartz lantern 7731"
    bot_level = "The team prefers short answers."
    assert (
        hub.post(team.auth[team.blake], f"/api/bots/{bot_id}/memory?own=1", {"text": own})[0] == 200
    )
    assert hub.post(hub.owner, f"/api/bots/{bot_id}/memory", {"text": bot_level})[0] == 200
    team.ask(fake, team.blake, bot_id, "Private question")
    assert own in json.dumps(fake.calls[-1]["messages"]) and bot_level in json.dumps(
        fake.calls[-1]["messages"]
    )
    tid = open_shared(team, team.blake, bot_id)
    team.ask(fake, team.blake, bot_id, "Shared question", suffix=at(tid), request_id="request-0002")
    shared_request = json.dumps(fake.calls[-1]["messages"]) + fake.calls[-1]["system"]
    assert own not in shared_request and bot_level in shared_request


# Confirm taps and the reader's own thread view


def test_a_confirm_chip_in_a_shared_thread_is_the_askers_alone(team, fake, confirm_tool):  # noqa: F811
    bot_id = team.shared_bot(team.blake, team.casey, grants=[*GRANTS, APPROVE])
    put_shared(team, bot_id, team.blake)
    put_shared(team, bot_id, team.casey)
    tid = open_shared(team, team.blake, bot_id)
    boards = service(team.hub.hub, team.hub.person(team.avery))
    ref = boards.task_add(title="One", board="docs")["number"]
    events = team.ask(
        fake, team.blake, bot_id, "Approve it", call("approve_item", "c1", ref=ref), suffix=at(tid)
    )
    [chip] = [d for e, d in events if e == "block" and d["type"] == "confirm"]
    body = {"pending": chip["pending"], "accept": True}
    for user in (team.casey, team.avery):  # readers of the thread, not the asker
        status, _, _ = team.hub.request(
            team.auth[user], "POST", f"/api/bots/{bot_id}/confirm{at(tid)}", body
        )
        assert status == 404, user
    assert confirm_tool == []
    seen = team.thread(team.casey, bot_id, at(tid))[1]["messages"]
    assert [b.get("gone") for m in seen for b in m["blocks"] if b["type"] == "confirm"] == [True]
    status, _, _ = team.hub.request(
        team.auth[team.blake], "POST", f"/api/bots/{bot_id}/confirm{at(tid)}", body
    )
    assert status == 200 and [(who, ran["ref"]) for who, ran in confirm_tool] == [(team.blake, ref)]
    assert team.thread(team.casey, bot_id, at(tid))[1]["messages"][-1]["role"] == "event"


# Isolation


def test_private_threads_stay_private_when_shared_threads_exist(team, fake):
    bot_id = team.shared_bot(team.blake, team.casey)
    put_shared(team, bot_id, team.blake)
    put_shared(team, bot_id, team.casey)
    team.ask(fake, team.blake, bot_id, "Blake private words")
    blake_own = team.hub.get(team.auth[team.blake], f"/api/bots/{bot_id}/threads")[1]["own"]
    tid = open_shared(team, team.blake, bot_id)
    team.ask(
        fake, team.blake, bot_id, "Blake shared words", suffix=at(tid), request_id="request-0002"
    )
    for user in (team.casey, team.avery):
        assert team.thread(user, bot_id, at(blake_own))[0] == 404
        listed = team.hub.get(team.auth[user], f"/api/bots/{bot_id}/threads")[1]["threads"]
        assert blake_own not in {t["id"] for t in listed} and tid in {t["id"] for t in listed}
        assert "Blake private words" not in json.dumps(team.thread(user, bot_id, at(tid))[1])
    assert team.texts(team.avery, bot_id) == []  # the owner's original thread has nothing of Blake
    # Outsiders get 404 for a shared thread: another workspace, agent tokens, a stranger member.
    for auth in (team.hub.other, team.hub.t1, team.hub.tm):
        assert team.hub.get(auth, f"/api/bots/{bot_id}/messages{at(tid)}")[0] == 404
        assert team.hub.get(auth, f"/api/bots/{bot_id}/threads")[0] == 404
    stranger = team.hub.hub.db.create_user("dana@example.com")
    team.hub.hub.db.add_member(team.workspace, stranger, "member", actor=team.avery)
    dana = team.hub.cookie("dana@example.com", team.workspace)
    assert team.hub.get(dana, f"/api/bots/{bot_id}/messages{at(tid)}")[0] == 404
    assert bot_id not in {b["id"] for b in team.hub.get(dana, "/api/bots")[1]["bots"]}


def test_a_shared_thread_costs_the_talkers_own_budget_and_runs_one_turn_per_thread(team, fake):
    hub = team.hub
    bot_id = team.shared_bot(team.blake, team.casey)
    put_shared(team, bot_id, team.blake)
    put_shared(team, bot_id, team.casey)
    tid = open_shared(team, team.blake, bot_id)
    configure(hub)
    fake.hold_call = threading.Event()
    fake.script = [[("text", "One.")]]
    first = Stream(hub, team.auth[team.blake], bot_id, "Blake asks", "request-0001", at(tid))
    assert first.next()[0] == "ack" and fake.entered.wait(3)
    busy = hub.post(
        team.auth[team.casey],
        f"/api/bots/{bot_id}/messages{at(tid)}",
        {"text": "Casey asks", "request_id": "request-0002"},
    )
    assert busy[0] == 409 and busy[1]["code"] == "busy"  # one turn per thread
    fake.hold_call.set()
    first.close()


def test_the_open_shared_thread_cap_and_the_unread_counts(team, fake, monkeypatch):
    monkeypatch.setattr(shares, "MAX_SHARED_THREADS", 2)
    bot_id = team.shared_bot(team.blake, team.casey)
    put_shared(team, bot_id, team.casey, talk=True, see_shared=True)
    first = open_shared(team, team.blake, bot_id)
    second = open_shared(team, team.blake, bot_id)
    status, body, _ = team.hub.post(
        team.auth[team.blake], f"/api/bots/{bot_id}/threads", {"visibility": "shared"}
    )
    assert (status, body["code"]) == (409, "limit")
    team.hub.request(
        team.auth[team.blake], "PATCH", f"/api/bots/{bot_id}/threads/{second}", {"archived": True}
    )
    assert open_shared(team, team.blake, bot_id)  # an archived thread frees its place
    team.ask(fake, team.blake, bot_id, "Blake speaks", suffix=at(first))
    listed = team.hub.get(team.auth[team.casey], f"/api/bots/{bot_id}/threads")[1]["threads"]
    unread = {t["id"]: t["unread"] for t in listed}
    assert unread[first] == 2 and unread[second] == 0  # Casey's own: both messages are new to her
    mine = team.hub.get(team.auth[team.blake], f"/api/bots/{bot_id}/threads")[1]["threads"]
    assert {t["id"]: t["unread"] for t in mine}[first] == 1  # Blake's own words do not count
    newest = team.thread(team.casey, bot_id, at(first))[1]["messages"][-1]["id"]
    assert (
        team.hub.post(
            team.auth[team.casey], f"/api/bots/{bot_id}/read{at(first)}", {"upto": newest}
        )[0]
        == 200
    )
    listed = team.hub.get(team.auth[team.casey], f"/api/bots/{bot_id}/threads")[1]["threads"]
    assert {t["id"]: t["unread"] for t in listed}[first] == 0


# The access function, as a table


@pytest.mark.parametrize(
    "talker, readers_, projects, limited",
    [
        ("avery", [], {"docs", "vault"}, set()),
        ("avery", ["blake"], {"docs"}, {"vault"}),
        ("avery", ["blake", "casey"], {"docs"}, {"vault"}),
        ("blake", [], {"docs"}, set()),  # the vault was never the talker's
        ("blake", ["casey"], {"docs"}, set()),
        ("blake", ["avery"], {"docs"}, set()),
    ],
)
def test_effective_reach_is_the_intersection_with_every_reader(
    team, talker, readers_, projects, limited
):
    hub, gateway = team.hub, team.hub.hub.gateway
    people = {"avery": team.avery, "blake": team.blake, "casey": team.casey}

    def person(name):
        return hub.person(people[name])

    bot = {"shared": True, "id": "x", "owner_id": team.avery}
    grants = [entry("project", "docs", "write"), entry("project", "vault", "write")]
    got = access.effective(
        gateway, bot, grants, person(talker), person("avery"), [person(r) for r in readers_]
    )
    assert set(got.projects) == projects and set(got.limited) == limited
    assert got.audience and got.writable <= set(got.projects)
    # Without an audience (a private thread) the readers play no part.
    alone = access.effective(gateway, bot, grants, person(talker), person("avery"))
    assert not alone.audience and alone.limited == frozenset()
    unshared = access.effective(
        gateway, {"shared": False}, None, person(talker), None, [person("casey")]
    )
    assert not unshared.shared and not unshared.audience


def test_a_shared_thread_lists_only_the_machines_every_reader_may_use(team, monkeypatch):
    bot_id = team.shared_bot(team.casey, grants=None)
    put_shared(team, bot_id, team.blake)
    tid = open_shared(team, team.avery, bot_id)
    machines = [
        {"id": "m1", "label": "One", "grants": {}},
        {"id": "m2", "label": "Two", "grants": {}},
    ]
    allowed = {team.avery: {"m1", "m2"}, team.blake: {"m1"}, team.casey: {"m1", "m2"}}
    monkeypatch.setattr(
        access.machines,
        "list_machines",
        lambda gateway, principal: [m for m in machines if m["id"] in allowed[principal.user_id]],
    )
    version = team.store.access_get(bot_id, team.avery)["version"]
    team.store.access_set(
        bot_id, team.avery, version, [entry("machine", "m1", "use"), entry("machine", "m2", "use")]
    )
    bot = team.store.roster(team.avery, bot_id)[0]
    message = {"id": 1, "text": "hello"}
    gateway, workspace = team.hub.hub.gateway, team.workspace
    private = tools.Turn(gateway, workspace, team.avery, bot, message, "r1")
    assert {m["id"] for m in private.machines()} == {"m1", "m2"}  # the owner's own thread
    shared = tools.Turn(gateway, workspace, team.avery, bot, message, "r2", thread_id=tid)
    assert {m["id"] for m in shared.machines()} == {"m1"}  # Blake reads the thread
