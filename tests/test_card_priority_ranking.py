"""Card priority ranking: the Coworker ranks, the owner always wins, starts go by priority.

Starts run on the real trigger path (``triggers.run`` over cards made through the board
service); ranking runs through the board service and the MCP tools. Time is the ``now`` the
sweep is given, so nothing sleeps.
"""

import pytest

from commonplace.boards.service import BoardService
from commonplace.boards.store import BoardStore
from commonplace.extensions import ToolError
from commonplace.orch import durable, start_retry, triggers
from commonplace.runner import jobs
from tests._phase_b_helpers import cap
from tests._split_helpers import world
from tests.test_commonplace_boards import (
    api,
    env as env,
    hub_fixture as hub_fixture,
    mcp,
    service,
)
from tests.test_orch_durable import BOARD as ORCH_BOARD, ROLE, Env as OrchEnv, at as orch_at
from tests.test_orch_runner import BOARD

T0 = "2030-01-01T00:00:00Z"


def at(seconds):
    return jobs._after(T0, seconds)


def started(w, *cards):
    return [bool(w.jobs_for(card)) for card in cards]


# --- priority-ordered starts ---------------------------------------------------------------


def test_the_sweep_starts_the_most_urgent_card_when_one_slot_is_free(hub):
    w = world(hub, on=False, runner=True)
    cap(w, 1)
    low = w.card(priority="low")
    normal = w.card()
    urgent = w.card(priority="urgent")
    triggers.run(w.js, now=T0)
    assert started(w, low, normal, urgent) == [False, False, True]
    assert sorted(m["card"] for m in w.misses()) == sorted([low, normal])
    assert {m["code"] for m in w.misses()} == {"at_cap"}
    # The cursor moved past every handled event, so nothing is started or missed twice.
    assert w.cursor()[0] == w.max_event()
    again = triggers.run(w.js, now=T0)
    assert again[BOARD] == [] and len(w.misses()) == 2 and len(w.all_jobs()) == 1


def test_equal_priorities_keep_their_order(hub):
    w = world(hub, on=False, runner=True)
    cap(w, 2)
    first, second, third = w.card(), w.card(), w.card()
    triggers.run(w.js, now=T0)
    assert started(w, first, second, third) == [True, True, False]


def test_retries_start_in_priority_order_and_respect_the_agent_limit(hub):
    w = world(hub, on=False, runner=True)
    cap(w, 1)
    holder = w.card(priority="urgent")
    low = w.card(priority="low")
    high = w.card(priority="high")
    normal = w.card()
    triggers.run(w.js, now=T0)
    assert started(w, holder, low, high, normal) == [True, False, False, False]
    cap(w, 2)  # one more slot
    report = triggers.run(w.js, now=at(start_retry.BACKOFF[0]))
    assert [r["card"] for r in report[BOARD]][:1] == [high]
    assert started(w, low, high, normal) == [False, True, False]
    assert len(w.all_jobs()) == 2  # the limit held for the others


def test_a_blocked_card_does_not_take_the_slot_of_a_ready_one(hub):
    w = world(hub, on=True, runner=True)
    cap(w, 1)
    blocker = w.card()
    blocked = w.card(priority="urgent", blocked_by=[blocker])
    ready = w.card(priority="high")
    triggers.run(w.js, now=T0)
    assert not w.jobs_for(blocked)
    assert {m["code"] for m in w.misses() if m["card"] == blocked} == {"blocked_by"}
    assert w.jobs_for(ready) and not w.jobs_for(blocker)


def test_a_revisited_event_never_starts_a_card_twice(hub):
    w = world(hub, on=False, runner=True)
    cap(w, 3)
    low = w.card(priority="low")
    urgent = w.card(priority="urgent")
    triggers.run(w.js, now=T0)
    before = len(w.all_jobs())
    assert before == 2
    # A sweep that stopped mid-batch leaves the cursor below events already started.
    with w.st.transaction() as conn:
        conn.execute("UPDATE orch_cursor SET cursor=0 WHERE board=?", (BOARD,))
    triggers.run(w.js, now=T0)
    assert len(w.all_jobs()) == before
    assert len(w.jobs_for(low)) == len(w.jobs_for(urgent)) == 1
    assert w.cursor()[0] == w.max_event()


# --- owner precedence and the Coworker's ranking ------------------------------------------


@pytest.fixture
def coordinator(hub, env):
    _token, agent, _person, _cookie, owner = env
    owner.board_set("work", coordinator=agent.actor)
    return service(hub, agent), owner


def test_new_cards_are_unranked_until_someone_ranks_them(hub, env):
    _token, agent, _person, _cookie, owner = env
    assert owner.task_add("Plain", board="work")["priority_source"] == "default"
    assert service(hub, agent).task_add("Agent", board="work")["priority_source"] == "default"
    ranked = owner.task_add("Ranked", board="work", priority="high")
    assert (ranked["priority"], ranked["priority_source"]) == ("high", "person")


def test_the_coworker_ranks_an_unranked_card_with_a_reason_in_the_history(hub, coordinator):
    coworker, owner = coordinator
    card = owner.task_add("Plain", board="work")
    done = coworker.task_update(
        card["number"],
        version=card["version"],
        priority="urgent",
        priority_reason="Blocks the release",
    )
    assert (done["priority"], done["priority_source"]) == ("urgent", "coworker")
    events = owner.task_show(card["number"])["events"]
    assert any(
        e["text"] == "Priority set to urgent by the Coworker: Blocks the release" for e in events
    )
    # The Coworker may refresh its own ranking.
    again = coworker.task_update(
        card["number"], version=done["version"], priority="low", priority_reason="Shipped"
    )
    assert (again["priority"], again["priority_source"]) == ("low", "coworker")


@pytest.mark.parametrize("reason", [None, "", "  ", "two\nlines", "x" * 201])
def test_the_coworker_needs_a_one_line_reason(hub, coordinator, reason):
    coworker, owner = coordinator
    card = owner.task_add("Plain", board="work")
    args = {} if reason is None else {"priority_reason": reason}
    with pytest.raises(ToolError, match="priority_reason"):
        coworker.task_update(card["number"], version=card["version"], priority="high", **args)
    assert owner.task_show(card["number"])["priority"] == "normal"


def test_a_persons_priority_is_never_overridden_by_the_coworker(hub, coordinator):
    coworker, owner = coordinator
    card = owner.task_add("Plain", board="work")
    ranked = coworker.task_update(
        card["number"], version=card["version"], priority="urgent", priority_reason="Looks urgent"
    )
    # The owner changes it: from then on it is the person's.
    mine = owner.task_update(card["number"], version=ranked["version"], priority="low")
    assert (mine["priority"], mine["priority_source"]) == ("low", "person")
    with pytest.raises(ToolError, match="a person set this priority"):
        coworker.task_update(
            card["number"], version=mine["version"], priority="urgent", priority_reason="Again"
        )
    shown = owner.task_show(card["number"])
    assert (shown["priority"], shown["priority_source"]) == ("low", "person")


def test_an_owner_priority_equal_to_the_default_still_counts_as_set(hub, coordinator):
    coworker, owner = coordinator
    card = owner.task_add("Plain", board="work")
    mine = owner.task_update(card["number"], version=card["version"], priority="normal")
    assert (mine["priority"], mine["priority_source"]) == ("normal", "person")
    assert mine["version"] == card["version"] + 1
    with pytest.raises(ToolError, match="a person set this priority"):
        coworker.task_update(
            card["number"], version=mine["version"], priority="high", priority_reason="Bump"
        )


def test_only_the_boards_coworker_and_the_owner_rank(hub, coordinator):
    _coworker, owner = coordinator
    card = owner.task_add("Plain", board="work")
    stranger = hub.token(
        "stranger",
        profile="full",
        scopes=["memory:read", "memory:write"],
        read_wings=["shared", "work"],
        write_wings=["shared", "work"],
        trust="trusted",
    )
    ok, text = mcp(
        hub, stranger, "task_update", task=card["number"], priority="urgent", priority_reason="x"
    )
    assert not ok and "workspace owner or the project's Coworker" in text
    assert owner.task_show(card["number"])["priority_source"] == "default"


def test_a_reason_without_a_priority_is_refused(hub, coordinator):
    coworker, owner = coordinator
    card = owner.task_add("Plain", board="work")
    with pytest.raises(ToolError, match="priority_reason needs a priority"):
        coworker.task_update(card["number"], version=card["version"], priority_reason="Why")


def test_a_person_needs_no_reason(hub, coordinator):
    _coworker, owner = coordinator
    card = owner.task_add("Plain", board="work")
    with pytest.raises(ToolError, match="priority_reason is for the Coworker"):
        owner.task_update(
            card["number"], version=card["version"], priority="high", priority_reason="Why"
        )


def test_tool_lines_name_who_ranked_a_card(hub, env, coordinator):
    token, _agent, _person, _cookie, owner = env
    mine = owner.task_add("Mine", board="work", priority="high")
    plain = owner.task_add("Plain", board="work")
    ok, text = mcp(hub, token, "task_show", task=mine["number"])
    assert ok and "priority:high priority_by:person" in text
    ok, text = mcp(hub, token, "task_show", task=plain["number"])
    assert ok and "priority_by" not in text
    ok, text = mcp(
        hub, token, "task_update", task=plain["number"], priority="low", priority_reason="Later"
    )
    assert ok
    ok, text = mcp(hub, token, "task_show", task=plain["number"])
    assert ok and "priority:low priority_by:coworker" in text


# --- the Coworker's ranking list -----------------------------------------------------------


def test_the_checkpoint_lists_the_cards_the_coworker_may_rank(hub):
    env = OrchEnv(hub)
    env.owner_board.board_set(ORCH_BOARD, coordinator=env.a.actor)
    coworker = BoardService(env.ctx(env.a))
    unranked = env.owner_board.task_add("unranked", board=ORCH_BOARD)
    ranked = env.owner_board.task_add("ranked", board=ORCH_BOARD, priority="high")["number"]
    mine = env.owner_board.task_add("mine", board=ORCH_BOARD, status="backlog")
    refreshed = coworker.task_update(
        mine["number"], version=mine["version"], priority="low", priority_reason="Later"
    )
    env.owner_board.task_add("doing", board=ORCH_BOARD, status="doing", assignee=env.owner.actor)
    cp = durable.checkpoint(env.ctx(env.a), ORCH_BOARD, ROLE, now=orch_at(10_000))
    assert cp["rankable"] == [
        {
            "card": unranked["number"],
            "title": "unranked",
            "status": "todo",
            "priority": "normal",
            "priority_source": "default",
        },
        {
            "card": refreshed["number"],
            "title": "mine",
            "status": "backlog",
            "priority": "low",
            "priority_source": "coworker",
        },
    ]
    assert ranked not in {c["card"] for c in cp["rankable"]}


def test_the_rest_api_shows_who_ranked_a_card(hub, env):
    _token, _agent, _person, cookie, owner = env
    card = owner.task_add("Plain", board="work")
    status, body = api(hub, cookie, "GET", f"/api/cards/{card['number']}")
    assert status == 200 and body["priority_source"] == "default"
    status, body = api(
        hub,
        cookie,
        "PATCH",
        f"/api/cards/{card['number']}",
        {"version": card["version"], "priority": "high"},
    )
    assert status == 200 and body["priority_source"] == "person"


def test_priorities_owners_set_before_the_column_existed_stay_theirs(hub, coordinator):
    coworker, owner = coordinator
    high = owner.task_add("Set by the owner", board="work", priority="high")
    plain = owner.task_add("Never ranked", board="work")
    with owner.store.transaction() as conn:
        conn.execute("ALTER TABLE tasks DROP COLUMN priority_by")
    BoardStore(owner.ctx.workspace_dir())  # opening migrates additively
    with pytest.raises(ToolError, match="a person set this priority"):
        coworker.task_update(
            high["number"], version=high["version"], priority="low", priority_reason="Later"
        )
    assert owner.task_show(high["number"])["priority_source"] == "person"
    assert owner.task_show(plain["number"])["priority_source"] == "default"
