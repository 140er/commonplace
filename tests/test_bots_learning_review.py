"""Review of what the bots learned (card #464, slice S5): the list, the thread blocks, the tools
and the read-only app scope.

A clean workspace: Avery (owner) and Blake (member), each with their own General bot, and a scripted
model. Names and words are generic.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from commonplace import bots_grant, sessions
from commonplace.bots import blocks, learned, tools_learning
from commonplace.bots.store import Conflict
from commonplace.control_db import ControlError
from tests._bots_helpers import bots_hub_fixture  # noqa: F401
from tests.test_bots_app_grant import app, machinery, set_policy_scopes
from tests.test_bots_learning_extract import Rig as ExtractRig, item as quote_item, reply
from tests.test_bots_learning_recall import (  # noqa: F401
    ask as ask_bot,
    rig_fixture,
    teach,
    turn_on,
)
from tests.test_bots_learning_shared import all_on, rows, shared_item, tap
from tests.test_bots_turn import Fake, call, configure, names, thread

HOSE = "My garden hose is green and long."
SHED = "Our garden shed key hangs by the door."
PLAIN = "Please always keep answers short and in plain language."
LEARNING_TOOLS = {"learned_list", "forget_learned", "forget_all_learned", "pause_learning"}


def listing(rig, auth=None, path="/api/learned"):
    status, body, _ = rig.get(auth or rig.owner, path)
    assert status == 200, body
    return {i["id"]: i for i in body["items"]}


def tool_names(rig, index=-1):
    return {t["name"] for t in rig.fake.calls[index]["tools"]}


# Acceptance 1: the review list shows every item with its source and scope


def test_the_review_list_shows_every_item_with_group_source_and_scope(rig):
    bot = turn_on(rig)
    all_on(rig)
    person = teach(rig, bot, PLAIN, kind="preference", topic="style")
    proposed = teach(rig, bot, SHED, kind="fact", topic="shed", state="proposed")
    first = teach(
        rig, bot, "Our staging is called the staging box.", kind="correction", topic="env"
    )
    second = teach(
        rig, bot, "Our staging is called the sandbox.", kind="correction", topic="env", dedupe=True
    )
    stale = teach(rig, bot, "I always take the early train.", kind="fact", topic="train")
    _original, copy = shared_item(rig, bot, HOSE)
    note = rig.store().memory_add(bot, rig.avery, "likes tea")
    old = (datetime.now(timezone.utc) - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with rig.store()._tx() as conn:
        conn.execute("UPDATE bot_learned SET last_seen_at=? WHERE id=?", (old, stale))
    items = listing(rig)
    assert {i["group"] for i in items.values()} == {
        "person",
        "shared",
        "proposed",
        "earlier",
        "old",
    }
    assert items[proposed]["group"] == "proposed" and items[first]["group"] == "earlier"
    assert items[second]["group"] == "person" and items[stale]["group"] == "old"
    assert items[copy]["group"] == "shared" and items[copy]["scope"] == "shared"
    assert items[person]["scope"] == "person" and items[f"n{note['id']}"]["kind"] == "note"
    message = items[person]["source_message_id"]
    assert items[person]["source"] == {
        "bot_id": bot,
        "bot_name": "General",
        "message_id": message,
        "at": items[person]["source_at"],
    }
    assert items[person]["text"] == PLAIN
    # The proposed item is only listed, never used: a filter finds it by state.
    only = listing(rig, path="/api/learned?state=proposed")
    assert set(only) == {proposed}


def test_the_list_is_the_persons_own_and_pages_by_limit(rig):
    bot = turn_on(rig)
    blake_bot = turn_on(rig, "blake")
    mine = [
        teach(rig, bot, f"I like tea number {n} very much.", topic=f"tea {n}") for n in range(3)
    ]
    theirs = teach(rig, blake_bot, HOSE, "blake", kind="fact", topic="hose")
    assert set(listing(rig)) == set(mine) and set(listing(rig, rig.member)) == {theirs}
    assert len(listing(rig, path="/api/learned?limit=2")) == 2


# Acceptance 2: accept, reject, edit and forget work from the list and from the thread


def test_accept_reject_and_edit_a_proposal_from_the_list(rig):
    bot = turn_on(rig)
    keep = teach(rig, bot, PLAIN, topic="style", state="proposed")
    drop = teach(rig, bot, SHED, kind="fact", topic="shed", state="proposed")
    status, body, _ = rig.request(
        rig.owner, "PATCH", f"/api/learned/{keep}", {"text": "Please keep answers short."}
    )
    assert (status, body["item"]["state"], body["item"]["text_by"]) == (
        200,
        "proposed",
        "person_edit",
    )
    status, body, _ = rig.request(rig.owner, "POST", f"/api/learned/{keep}/accept", {})
    assert (status, body["item"]["state"], body["item"]["group"]) == (200, "active", "person")
    assert body["item"]["text"] == "Please keep answers short."
    assert rig.request(rig.owner, "POST", f"/api/learned/{keep}/accept", {})[0] == 409
    assert rig.request(rig.owner, "POST", f"/api/learned/{keep}/reject", {})[0] == 409
    status, body, _ = rig.request(rig.owner, "POST", f"/api/learned/{drop}/reject", {})
    assert (status, body) == (200, {"ok": True})
    assert drop not in listing(rig)
    assert rig.request(rig.owner, "POST", f"/api/learned/{drop}/reject", {})[0] == 404
    # The rejected sentence is blocked from coming back.
    with pytest.raises(learned.Refused):
        teach(rig, bot, SHED, kind="fact", topic="shed", state="proposed")
    # Forget from the list is a real delete.
    assert rig.request(rig.owner, "DELETE", f"/api/learned/{keep}")[0] == 200
    assert listing(rig) == {}


def test_a_proposal_replaces_an_older_item_of_its_topic_only_when_accepted(rig):
    bot = turn_on(rig)
    old = teach(rig, bot, "Our staging is called the staging box.", kind="fact", topic="env")
    new = teach(
        rig, bot, "Our staging is called the sandbox.", kind="fact", topic="env", state="proposed"
    )
    assert listing(rig)[old]["group"] == "person"
    assert rig.request(rig.owner, "POST", f"/api/learned/{new}/accept", {})[0] == 200
    assert listing(rig)[old]["group"] == "earlier" and listing(rig)[new]["group"] == "person"


def test_accept_needs_the_workspace_switch_and_only_the_owner_of_the_item(rig):
    bot = turn_on(rig)
    blake_bot = turn_on(rig, "blake")
    mine = teach(rig, bot, PLAIN, topic="style", state="proposed")
    theirs = teach(rig, blake_bot, SHED, "blake", kind="fact", topic="shed", state="proposed")
    for action in ("accept", "reject"):
        assert rig.request(rig.owner, "POST", f"/api/learned/{theirs}/{action}", {})[0] == 404
        assert rig.request(rig.member, "POST", f"/api/learned/{mine}/{action}", {})[0] == 404
    assert rig.request(rig.owner, "PATCH", "/api/learning/settings", {"enabled": False})[0] == 200
    status, body, _ = rig.request(rig.owner, "POST", f"/api/learned/{mine}/accept", {})
    assert (status, body["code"]) == (409, "learning_off")
    assert rig.request(rig.owner, "POST", f"/api/learned/{mine}/accept", {"x": 1})[0] == 400
    assert listing(rig)[mine]["state"] == "proposed"


@pytest.fixture(name="runrig")
def runrig_fixture(bots_hub, monkeypatch):
    from commonplace.chat import model_stream

    fake = Fake()
    monkeypatch.setattr(model_stream, "stream", fake)
    configure(bots_hub)
    return ExtractRig(bots_hub, fake)


def event_blocks(rig):
    """The blocks of the event messages in Avery's thread, as the API shows them."""
    messages = thread(rig.hub, rig.bot)["messages"]
    return [(m["text"], b) for m in messages if m["role"] == "event" for b in m["blocks"]]


def test_an_ask_run_puts_a_proposal_in_the_thread_and_the_person_answers_there(runrig):
    runrig.on("ask")
    (message,) = runrig.say(PLAIN)
    runrig.run(script=reply(quote_item(message, "keep answers short and in plain language")))
    ((text, block),) = event_blocks(runrig)
    assert text == "I could keep this for next time. Do you want that?"
    assert block["type"] == "learn_proposal" and block["state"] == "open"
    assert block["item"]["text"] == "keep answers short and in plain language"
    assert set(block["item"]) == {"id", "text", "scope", "kind", "at", "edited"}
    item_id = block["id"]
    hub = runrig.hub
    # Edit then accept, from the thread's own routes.
    assert (
        hub.request(hub.owner, "PATCH", f"/api/learned/{item_id}", {"text": "keep it short"})[0]
        == 200
    )
    (_, block) = event_blocks(runrig)[0]
    assert block["item"]["edited"] is True and block["state"] == "open"
    assert hub.request(hub.owner, "POST", f"/api/learned/{item_id}/accept", {})[0] == 200
    (_, block) = event_blocks(runrig)[0]
    assert (block["state"], block["item"]["text"]) == ("accepted", "keep it short")
    # Forgetting it makes the block gone; nothing of the words stays in the stored block.
    assert hub.request(hub.owner, "DELETE", f"/api/learned/{item_id}")[0] == 200
    assert event_blocks(runrig)[0][1] == {"type": "learn_proposal", "gone": True}
    stored = runrig.sql("SELECT blocks FROM bot_messages WHERE role='event'")
    assert stored == [(json.dumps([{"type": "learn_proposal", "id": item_id}]),)]


def test_rejecting_a_proposal_in_the_thread_deletes_it(runrig):
    runrig.on("ask")
    (message,) = runrig.say(PLAIN)
    runrig.run(script=reply(quote_item(message, "keep answers short and in plain language")))
    item_id = event_blocks(runrig)[0][1]["id"]
    hub = runrig.hub
    assert hub.request(hub.owner, "POST", f"/api/learned/{item_id}/reject", {})[0] == 200
    assert event_blocks(runrig)[0][1]["gone"] is True
    assert runrig.items() == []


def test_an_auto_run_shows_what_it_learned_with_a_forget_and_nothing_else(runrig):
    runrig.on("auto")
    (message,) = runrig.say(PLAIN)
    runrig.run(script=reply(quote_item(message, "keep answers short and in plain language")))
    ((text, block),) = event_blocks(runrig)
    assert text == "I learned something from this conversation."
    assert (block["type"], block["how"]) == ("learned", "learned")
    (shown,) = block["items"]
    assert shown["text"] == "keep answers short and in plain language"
    assert set(shown) == {"id", "scope", "kind", "said", "text", "at"}
    hub = runrig.hub
    assert hub.request(hub.owner, "DELETE", f"/api/learned/{shown['id']}")[0] == 200
    assert event_blocks(runrig)[0][1] == {"type": "learned", "gone": True}


def test_a_repeated_sentence_and_a_dropped_item_announce_nothing_new(runrig):
    runrig.on("auto")
    (message,) = runrig.say(PLAIN)
    runrig.run(script=reply(quote_item(message, "keep answers short and in plain language")))
    again = runrig.say(PLAIN)[0]
    runrig.run(
        now=datetime.now(timezone.utc) + timedelta(minutes=30),
        script=reply(
            quote_item(again, "keep answers short and in plain language"),
            quote_item(again, "this sentence is not in the message"),
        ),
    )
    assert len(event_blocks(runrig)) == 1 and len(runrig.items()) == 1


def test_off_by_default_nothing_is_posted_to_the_thread(runrig):
    runrig.say(PLAIN)
    assert runrig.run() is None and event_blocks(runrig) == []


def test_a_block_of_a_foreign_or_missing_item_hydrates_to_gone(rig):
    bot = turn_on(rig)
    blake_bot = turn_on(rig, "blake")
    theirs = teach(rig, blake_bot, SHED, "blake", kind="fact", topic="shed", state="proposed")
    reader = blocks.Reader(rig.hub.gateway, rig.person(rig.avery))
    for stored in ({"type": "learn_proposal", "id": theirs}, {"type": "learn_proposal", "id": 7}):
        assert blocks.hydrate(reader, [stored]) == [{"type": "learn_proposal", "gone": True}]
    note = rig.store().memory_add(bot, rig.avery, "likes tea")
    stored = {"type": "learn_proposal", "id": f"n{note['id']}"}
    assert blocks.hydrate(reader, [stored]) == [{"type": "learn_proposal", "gone": True}]
    assert blocks.hydrate(reader, [{"type": "learned", "ids": [theirs]}]) == [
        {"type": "learned", "gone": True}
    ]


# Acceptance 3: a model-initiated forget needs a tap


def test_the_learning_tools_are_not_offered_until_the_workspace_turns_learning_on(rig):
    bot = rig.by_board(rig.owner)["general"]["id"]
    rig.fake.script = [[("text", "Hello.")]]
    ask_bot(rig, bot, "Hello there", "request-0201")
    assert not LEARNING_TOOLS & tool_names(rig)
    turn_on(rig)
    rig.fake.script = [[("text", "Hello.")]]
    ask_bot(rig, bot, "Hello again", "request-0202")
    assert LEARNING_TOOLS <= tool_names(rig)


def test_the_model_can_ask_to_forget_but_only_the_tap_deletes(rig):
    bot = turn_on(rig)
    item_id = teach(rig, bot, HOSE, kind="fact", topic="hose")
    rig.fake.script = [[call("forget_learned", id=item_id)], [("text", "Waiting for you.")]]
    events = ask_bot(rig, bot, "Please forget what I said about the hose", "request-0211")
    assert names(events)[-1] == "done"
    assert item_id in listing(rig)
    ((pending, label, state, tool),) = rows(rig, "SELECT id,label,state,tool FROM bot_pending")
    assert (state, tool) == ("open", "forget_learned")
    assert label == f"Forget this for good: “{HOSE}”. It cannot be undone."
    assert tap(rig, bot, pending, accept=False)[0] == 200
    assert item_id in listing(rig)
    # A fresh request, a fresh chip: this one is tapped.
    rig.fake.script = [[call("forget_learned", id=item_id)], [("text", "Waiting for you.")]]
    ask_bot(rig, bot, "Please forget the hose again", "request-0212")
    (pending,) = [r[0] for r in rows(rig, "SELECT id FROM bot_pending WHERE state='open'")]
    assert tap(rig, bot, pending)[0] == 200
    assert listing(rig) == {}
    # The second tap is a conflict, and the sentence cannot be learned again.
    assert tap(rig, bot, pending)[0] in (404, 409)
    with pytest.raises(learned.Refused):
        teach(rig, bot, HOSE, kind="fact", topic="hose")


def test_the_model_cannot_forget_an_item_that_is_not_the_persons(rig):
    bot = turn_on(rig)
    blake_bot = turn_on(rig, "blake")
    theirs = teach(rig, blake_bot, SHED, "blake", kind="fact", topic="shed")
    rig.fake.script = [[call("forget_learned", id=theirs)], [("text", "I cannot.")]]
    events = ask_bot(rig, bot, "Please forget the shed", "request-0221")
    assert names(events)[-1] == "done"
    assert rows(rig, "SELECT COUNT(*) FROM bot_pending") == [(0,)]
    assert theirs in listing(rig, rig.member)
    results = [m for m in rig.fake.calls[-1]["messages"] if "tool" in json.dumps(m).lower()]
    assert tools_learning.FORGET_SAYS["missing"] in json.dumps(results)


def test_forget_everything_by_the_model_is_a_chip_with_counts_and_deletes_only_after_the_tap(rig):
    bot = turn_on(rig)
    teach(rig, bot, HOSE, kind="fact", topic="hose")
    teach(rig, bot, PLAIN, kind="preference", topic="style", state="proposed")
    rig.store().memory_add(bot, rig.avery, "likes tea")
    rig.fake.script = [[call("forget_all_learned")], [("text", "Waiting for you.")]]
    ask_bot(rig, bot, "Forget everything you learned about me", "request-0231")
    assert len(listing(rig)) == 3
    ((pending, label),) = rows(rig, "SELECT id,label FROM bot_pending WHERE state='open'")
    assert "2 learned items and 1 notes" in label and "cannot be undone" in label
    assert tap(rig, bot, pending)[0] == 200
    assert listing(rig) == {}


def test_learned_list_returns_only_the_askers_own_words(rig):
    bot = turn_on(rig)
    blake_bot = turn_on(rig, "blake")
    mine = teach(rig, bot, HOSE, kind="fact", topic="hose")
    teach(rig, blake_bot, SHED, "blake", kind="fact", topic="shed")
    rig.fake.script = [[call("learned_list")], [("text", "Here you go.")]]
    ask_bot(rig, bot, "What did you learn about me?", "request-0241")
    seen = json.dumps(rig.fake.calls[-1]["messages"])
    assert mine in seen and "green and long" in seen and "shed key" not in seen


def test_pause_learning_only_pauses_and_never_changes_the_mode(rig):
    bot = turn_on(rig)
    rig.fake.script = [[call("pause_learning", days=3)], [("text", "Paused.")]]
    ask_bot(rig, bot, "Please stop learning for a few days", "request-0251")
    state = rig.store_learned().state(bot, rig.avery)
    assert state["paused"] is True and state["mode"] == "auto"
    assert tools_learning.LIST_MAX == 20
    for tool in tools_learning.tools.REGISTRY.values():
        if tool.name in LEARNING_TOOLS:
            assert "mode" not in tool.schema["properties"] and "user" not in json.dumps(tool.schema)
    # Out-of-range days never reach the store.
    rig.fake.script = [[call("pause_learning", days=90)], [("text", "No.")]]
    ask_bot(rig, bot, "Pause for ninety days", "request-0252")
    assert rig.store_learned().state(bot, rig.avery)["paused_until"] == state["paused_until"]


# Acceptance 4: the bots:learned read scope lists and never changes anything


def with_learned(hub, cid, user=None, *, chat=True, learned_scope=True):
    hub.db.set_connection_bots(
        cid,
        chat=chat,
        confirm=False,
        learned=learned_scope,
        actor=hub.person(user or hub.hub.admin),
        workspace_ref=hub.workspace,
    )


def test_an_app_with_the_learned_scope_lists_items_and_changes_nothing(rig):
    bot = turn_on(rig)
    kept = teach(rig, bot, PLAIN, topic="style")
    waiting = teach(rig, bot, SHED, kind="fact", topic="shed", state="proposed")
    token, cid = app(rig)
    with_learned(rig, cid)
    status, body, _ = rig.get(token, "/api/learned")
    assert status == 200 and {i["id"] for i in body["items"]} == {kept, waiting}
    assert body["items"][0]["source"]["bot_name"] == "General"
    assert rig.get(token, f"/api/bots/{bot}/learned")[0] == 200
    before = rows(rig, "SELECT id,text,state,pinned FROM bot_learned ORDER BY seq")
    writes = [
        ("PATCH", f"/api/learned/{kept}", {"text": "Something else entirely."}),
        ("PATCH", f"/api/learned/{kept}", {"pinned": True}),
        ("DELETE", f"/api/learned/{kept}", None),
        ("POST", f"/api/learned/{waiting}/accept", {}),
        ("POST", f"/api/learned/{waiting}/reject", {}),
        ("POST", f"/api/learned/{kept}/share", {"project": "work"}),
        ("POST", f"/api/bots/{bot}/learned/forget-all", {}),
        ("PATCH", f"/api/bots/{bot}/learning", {"mode": "off"}),
        ("PATCH", f"/api/bots/{bot}/learning", {"pause": 3}),
        ("PATCH", "/api/learning/settings", {"enabled": False}),
        ("PATCH", "/api/projects/work/learning", {"shared_enabled": True}),
    ]
    for method, path, payload in writes:
        status, reply, _ = rig.request(token, method, path, payload)
        assert (status, reply.get("code")) == (403, "read_only"), (method, path)
    # Everything else stays as unknown as before.
    for path in (f"/api/learned/{kept}", f"/api/bots/{bot}/learning", "/api/learning/settings"):
        assert rig.get(token, path)[0] == 404, path
    assert rows(rig, "SELECT id,text,state,pinned FROM bot_learned ORDER BY seq") == before
    assert rig.store_learned().state(bot, rig.avery)["mode"] == "auto"
    assert rig.store_learned().state(bot, rig.avery)["paused"] is False
    assert rig.store_learned().policy()["enabled"] is True


def test_an_app_with_only_bots_chat_sees_no_learned_data_at_all(rig):
    bot = turn_on(rig)
    kept = teach(rig, bot, PLAIN, topic="style")
    token, _cid = app(rig)
    for method, path in (
        ("GET", "/api/learned"),
        ("GET", f"/api/bots/{bot}/learned"),
        ("GET", f"/api/learned/{kept}"),
        ("DELETE", f"/api/learned/{kept}"),
        ("POST", f"/api/learned/{kept}/accept"),
    ):
        status, reply, _ = rig.request(token, method, path, {} if method == "POST" else None)
        assert (status, reply.get("code")) == (404, "not_found"), (method, path)
    assert kept in listing(rig)


def test_the_scope_is_read_only_for_each_persons_own_items(rig):
    bot = turn_on(rig)
    blake_bot = turn_on(rig, "blake")
    mine = teach(rig, bot, PLAIN, topic="style")
    theirs = teach(rig, blake_bot, SHED, "blake", kind="fact", topic="shed")
    token, cid = app(rig)
    with_learned(rig, cid)
    status, body, _ = rig.get(token, "/api/learned")
    assert {i["id"] for i in body["items"]} == {mine} and theirs not in json.dumps(body)
    assert rig.get(token, f"/api/learned?bot={blake_bot}")[0] == 404
    # Every write through the app is recorded as refused.
    rig.request(token, "DELETE", f"/api/learned/{mine}")
    entries = [
        e
        for e in rig.db.audit_entries(rig.workspace, limit=100)
        if e["action"] == bots_grant.AUDIT_WRITE
    ]
    assert entries and entries[0]["decision"] != "ok"


def test_the_scope_is_granted_separately_needs_chat_and_goes_with_it(rig):
    token, cid = app(rig)
    avery = rig.person(rig.avery)

    def scopes():
        return set(rig.db.authenticate_connection(cid).policy.scopes)

    assert bots_grant.LEARNED_SCOPE not in scopes()
    with_learned(rig, cid)
    assert {"bots:chat", "bots:learned"} <= scopes()
    # A later change of the other scopes leaves it as it was.
    rig.db.set_connection_bots(
        cid, chat=True, confirm=True, actor=avery, workspace_ref=rig.workspace
    )
    assert {"bots:chat", "bots:confirm", "bots:learned"} <= scopes()
    # The learned scope alone is refused, and withdrawing chat withdraws it.
    with pytest.raises(ControlError):
        rig.db.set_connection_bots(
            cid, chat=False, confirm=False, learned=True, actor=avery, workspace_ref=rig.workspace
        )
    rig.db.set_connection_bots(
        cid, chat=False, confirm=False, actor=avery, workspace_ref=rig.workspace
    )
    assert not {"bots:chat", "bots:confirm", "bots:learned"} & scopes()
    # It is useless without chat, even when stored by an older build.
    set_policy_scopes(rig, cid, "bots:learned")
    assert rig.get(token, "/api/learned")[0] == 404
    # Settings -> Agents: one PATCH, the same rules.
    token2, cid2 = app(rig, name="tablet", chat=False)
    status, body, _ = rig.request(
        rig.owner, "PATCH", f"/api/connections/{cid2}", {"bots_learned": True}
    )
    assert status == 409
    status, body, _ = rig.request(
        rig.owner, "PATCH", f"/api/connections/{cid2}", {"bots_chat": True, "bots_learned": True}
    )
    assert status == 200 and body["connection"]["bots_learned"] is True
    status, body, _ = rig.request(
        rig.owner, "PATCH", f"/api/connections/{cid2}", {"bots_learned": False}
    )
    assert (status, body["connection"]["bots_chat"], body["connection"]["bots_learned"]) == (
        200,
        True,
        False,
    )


def test_a_person_session_never_carries_the_scope_and_agent_machinery_is_refused(rig):
    for role in ("owner", "member"):
        assert not sessions.person_policy(role).has_scope(bots_grant.LEARNED_SCOPE)
    bot = rig.by_board(rig.owner)["general"]["id"]
    for label, token, cid in machinery(rig):
        # A runner token is stopped earlier still, by the gateway (403); the rest get 404.
        expected = 403 if label == "runner" else 404
        set_policy_scopes(rig, cid, "bots:chat", "bots:learned")
        for path in ("/api/learned", f"/api/bots/{bot}/learned"):
            assert rig.get(token, path)[0] == expected, (label, path)


# Acceptance 5: an agent token gets 404 on these routes


def test_an_agent_token_gets_404_on_every_learned_route(rig):
    bot = turn_on(rig)
    kept = teach(rig, bot, PLAIN, topic="style")
    waiting = teach(rig, bot, SHED, kind="fact", topic="shed", state="proposed")
    paths = [
        ("GET", "/api/learned", None),
        ("GET", f"/api/learned/{kept}", None),
        ("PATCH", f"/api/learned/{kept}", {"pinned": True}),
        ("DELETE", f"/api/learned/{kept}", None),
        ("POST", f"/api/learned/{waiting}/accept", {}),
        ("POST", f"/api/learned/{waiting}/reject", {}),
        ("POST", f"/api/learned/{kept}/share", {}),
        ("GET", f"/api/bots/{bot}/learned", None),
        ("POST", f"/api/bots/{bot}/learned/forget-all", {}),
        ("GET", f"/api/bots/{bot}/learning", None),
        ("GET", "/api/learning/settings", None),
    ]
    agent = rig.hub.token(
        "reader", scopes=["memory:read", "memory:write"], read_wings=["*"], trust="standard"
    )
    for label, token, _cid in [("plain agent", agent, None), *machinery(rig)]:
        for method, path, payload in paths:
            status, reply, _ = rig.request(token, method, path, payload)
            if label == "runner":  # a runner token is stopped earlier still, by the gateway
                assert status == 403, (label, method, path)
            else:
                assert (status, reply.get("code")) == (404, "not_found"), (label, method, path)
    assert set(listing(rig)) == {kept, waiting}
    assert rig.request(None, "GET", "/api/learned")[0] == 401


def test_the_store_methods_hold_the_state_rules(rig):
    bot = turn_on(rig)
    store = rig.store_learned()
    active = teach(rig, bot, PLAIN, topic="style")
    with pytest.raises(Conflict):
        store.accept(rig.avery, active)
    with pytest.raises(Conflict):
        store.reject(rig.avery, active)
    from commonplace.bots.learn_review import group

    row = store.get(rig.avery, active)
    assert group(row) == "person" and group({**row, "state": "proposed"}) == "proposed"
    assert group({**row, "pinned": True, "last_seen_at": "2020-01-01T00:00:00Z"}) == "person"
    assert group({**row, "replaced_by": "x"}) == "earlier"
    log = rows(rig, "SELECT action FROM bot_learn_log ORDER BY id")
    assert ("learn",) in log


def test_a_rejected_proposal_leaves_no_row_no_index_entry_and_only_a_block_and_a_log(rig):
    bot = turn_on(rig)
    marker = "My zebra lantern quartz code word is plum."
    item_id = teach(rig, bot, marker, kind="fact", topic="word", state="proposed")
    assert rig.request(rig.owner, "POST", f"/api/learned/{item_id}/reject", {})[0] == 200
    assert rows(rig, "SELECT COUNT(*) FROM bot_learned") == [(0,)]
    assert rows(
        rig, "SELECT COUNT(*) FROM bot_learned_fts WHERE bot_learned_fts MATCH 'zebra'"
    ) == [(0,)]
    assert rows(rig, "SELECT COUNT(*) FROM bot_learn_blocks") == [(1,)]
    assert rows(rig, "SELECT item_id,action FROM bot_learn_log ORDER BY id DESC LIMIT 1") == [
        (item_id, "reject")
    ]
