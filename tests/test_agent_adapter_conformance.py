"""Adapter conformance: any agent type takes part through the documented protocol.

Each agent is registered the documented way, then driven over HTTP against the
production gateway through the reference adapter (docs/agent-adapter.md)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from commonplace import events, sessions
from commonplace.adapter import (
    NOTHING_TO_TAKE,
    AdapterError,
    AgentAdapter,
    Claim,
    ClaimLost,
    parse_claim,
)
from commonplace.cli import main
from commonplace.client import Client, ClientError, ToolRefused, _tool_text
from tests._commonplace_helpers import hub_fixture  # noqa: F401
from tests.test_commonplace_boards import service

# A second agent type the product has never heard of, next to a built-in one.
FAKE = ("fake-agent", "--preset", "coding")
BUILT_IN = ("claude-code",)
PAST = "2000-01-01T00:00:00Z"


class World:
    def __init__(self, hub, capsys, monkeypatch):
        self.hub, self.capsys, self.monkeypatch = hub, capsys, monkeypatch
        self.owner = service(hub, sessions.person_principal(hub.db, hub.admin, hub.workspace))
        self.owner.board_add(slug="work", wing="shared")

    def agent(self, name, client, *flags, clock=None):
        """Register a connection with `token add`, then connect its adapter."""
        data = str(Path(self.hub.db.path).parent)
        command = ["--data", data, "token", "add", name, "--workspace", self.hub.workspace]
        assert main([*command, "--client", client, "--by", "owner@example.com", *flags]) == 0
        variable = "ADAPTER_TOKEN_" + name.upper()
        self.monkeypatch.setenv(variable, self.capsys.readouterr().out.strip())
        tools = Client(f"http://127.0.0.1:{self.hub.port}", variable, timeout=30)
        adapter = AgentAdapter(tools, board="work", **({"clock": clock} if clock else {}))
        assert "rules_seq" in adapter.connect()
        return adapter

    def card(self, title="Ship the change", body="Everything the agent needs."):
        return self.owner.task_add(board="work", title=title, body=body)["number"]

    def show(self, number):
        return self.owner.task_show(number)

    def kinds(self, number):
        return [event["kind"] for event in self.show(number)["events"]]

    def miss_heartbeat(self, number):
        """The claim's deadline passes without a heartbeat from its holder."""
        with self.owner.store.transaction() as conn:
            conn.execute("UPDATE tasks SET claim_until=? WHERE id=?", (PAST, number))


@pytest.fixture
def world(hub, capsys, monkeypatch):
    return World(hub, capsys, monkeypatch)


@pytest.mark.parametrize("agent_type", [FAKE, BUILT_IN], ids=["fake-agent", "built-in"])
def test_conformance_take_heartbeat_work_done(world, agent_type):
    agent = world.agent("worker", *agent_type)
    assert agent.take() is None
    number = world.card(title='Say "hi"\nnow', body="first line\nsecond line")

    before = datetime.now(timezone.utc)
    claim = agent.take()
    assert (claim.number, claim.title, claim.body) == (
        number,
        'Say "hi"\nnow',
        "first line\nsecond line",
    )
    # The claim is the board's 30 minutes, so its heartbeat falls due after 20.
    assert timedelta(minutes=29) < claim.until - before <= timedelta(minutes=30, seconds=2)
    assert not claim.heartbeat_due(before + timedelta(minutes=19))
    assert claim.heartbeat_due(before + timedelta(minutes=21))
    held = world.show(number)
    assert (held["status"], held["assignee"], held["claim_expired"]) == (
        "doing",
        "conn:worker",
        False,
    )
    assert agent.take() is None

    with world.owner.store.transaction() as conn:
        conn.execute("UPDATE tasks SET claim_until='2099-01-01T00:00:00Z' WHERE id=?", (number,))
    renewed = agent.heartbeat(claim)
    assert renewed.number == number and renewed.until < datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert world.show(number)["claim_until"] == renewed.until.strftime("%Y-%m-%dT%H:%M:%SZ")

    reported = agent.progress(renewed, "Writing the greeting")
    assert world.show(number)["status_line"] == "Writing the greeting"
    assert reported.until >= renewed.until

    assert agent.done(reported, "Greeting written") == "done"
    final = world.show(number)
    assert (final["status"], final["claim_until"]) == ("done", None)
    assert final["events"][-1]["actor"] == "conn:worker"
    assert world.kinds(number) == ["add", "claim", "renew", "edit", "move"]
    with pytest.raises(ClaimLost):
        agent.heartbeat(reported)


def test_work_loop_heartbeats_only_when_due(world):
    now = [datetime.now(timezone.utc)]
    agent = world.agent("worker", *FAKE, clock=lambda: now[0])
    number = world.card()
    seen = []

    def run(claim, beat):
        seen.append(claim.number)
        beat()  # Not due: no call to the hub.
        assert world.kinds(number).count("renew") == 0
        now[0] += timedelta(minutes=21)
        assert beat().renewed_at == now[0]
        assert world.kinds(number).count("renew") == 1
        beat()
        return "Finished by the work loop"

    claim, status = agent.work(run)
    assert (seen, claim.number, status) == ([number], number, "done")
    assert world.kinds(number).count("renew") == 1
    assert world.show(number)["status_line"] == "Finished by the work loop"
    assert agent.work(run) is None and seen == [number]


def test_work_loop_failure_keeps_the_card_held(world):
    agent = world.agent("worker", *FAKE)
    number = world.card()

    def run(_claim, _beat):
        raise RuntimeError("agent crashed")

    with pytest.raises(RuntimeError):
        agent.work(run)
    held = world.show(number)
    assert (held["status"], held["assignee"]) == ("doing", "conn:worker")
    # Taking a card the agent already holds resumes it.
    assert agent.take(task=number).number == number


def test_missed_heartbeat_expires_the_claim_and_work_returns(world):
    fake = world.agent("fake", *FAKE)
    other = world.agent("other", *BUILT_IN)
    number = world.card()
    claim = fake.take()
    assert not claim.expired(datetime.now(timezone.utc))
    assert claim.expired(claim.until)

    world.miss_heartbeat(number)
    expired = world.show(number)
    # Expiry marks the card and keeps its holder: nothing is released silently.
    assert (expired["status"], expired["assignee"], expired["claim_expired"]) == (
        "doing",
        "conn:fake",
        True,
    )
    assert world.kinds(number).count("expire") == 1
    event = events.after(world.hub.db, world.hub.workspace)[-1]
    assert (event["kind"], event["actor"], event["data"]["target"]) == (
        "card.expired",
        "system",
        "conn:fake",
    )
    # It reaches the owner as a stuck claim, and no other agent can take it.
    assert [card["number"] for card in world.owner.needs_me()["stale"]] == [number]
    assert other.take() is None
    with pytest.raises(ToolRefused):
        other.take(task=number)

    # The owner returns the work to the queue; any agent type can take it from there.
    returned = world.owner.task_update(number, status="todo", version=expired["version"])
    assert (returned["status"], returned["assignee"], returned["claim_expired"]) == (
        "todo",
        None,
        False,
    )
    retaken = other.take()
    assert retaken.number == number and world.show(number)["assignee"] == "conn:other"
    # The agent that went quiet learns it on its next call and must stop.
    with pytest.raises(ClaimLost):
        fake.heartbeat(claim)
    with pytest.raises(ToolRefused):
        fake.done(claim)
    assert other.done(retaken) == "done"
    assert world.kinds(number) == ["add", "claim", "expire", "move", "claim", "move"]


def test_expired_claim_is_renewed_by_its_holder(world):
    fake = world.agent("fake", *FAKE)
    number = world.card()
    claim = fake.take()
    world.miss_heartbeat(number)
    assert world.show(number)["claim_expired"]

    renewed = fake.heartbeat(claim)
    current = world.show(number)
    assert not current["claim_expired"] and current["assignee"] == "conn:fake"
    assert renewed.until > datetime.now(timezone.utc)
    assert world.owner.needs_me()["stale"] == []
    assert fake.done(renewed) == "done"
    assert world.kinds(number) == ["add", "claim", "expire", "renew", "move"]


def test_release_gives_unfinished_work_back(world):
    fake = world.agent("fake", *FAKE)
    other = world.agent("other", *BUILT_IN)
    number = world.card()
    claim = fake.take()

    assert fake.release(claim, "Out of budget; the branch is pushed.") == "todo"
    released = world.show(number)
    assert (released["status"], released["assignee"], released["claim_until"]) == (
        "todo",
        None,
        None,
    )
    with pytest.raises(ClaimLost):
        fake.heartbeat(claim)
    assert other.take().number == number


def test_two_agent_types_never_take_the_same_card(world):
    fake = world.agent("fake", *FAKE)
    other = world.agent("other", *BUILT_IN)
    numbers = {world.card(title=f"Card {index}") for index in range(4)}
    taken = [agent.take().number for agent in (fake, other, fake, other)]
    assert set(taken) == numbers
    assert fake.take() is None and other.take() is None
    holders = {world.show(number)["assignee"] for number in taken[::2]}
    assert holders == {"conn:fake"}


def test_preset_gives_any_client_name_the_built_in_policy(world):
    world.agent("fake", *FAKE)
    world.agent("builtin", *BUILT_IN)
    world.agent("plain", "fake-agent")
    policy = {
        row["name"]: world.hub.db.authenticate_connection(row["id"]).policy
        for row in world.hub.db.connections(world.hub.workspace)
    }
    assert policy["fake"] == policy["builtin"]
    assert policy["fake"].trust == "trusted"
    # Without the preset an unknown client keeps today's restricted default.
    assert (policy["plain"].trust, policy["plain"].write_wing) == ("standard", "inbox")


class ScriptedTools:
    """Replies in order; an exception instance is raised instead."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    write_tool = call_tool


NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def test_claim_parsing_and_heartbeat_schedule():
    assert parse_claim(NOTHING_TO_TAKE, NOW) is None
    claim = parse_claim(
        '#7 "A \\"quoted\\"  yours until 2001-01-01T00:00:00Z title"  yours until '
        '2026-01-01T12:30:00Z  parent:#3\nbody: "body: not this\\nline"\ngoal: Launch',
        NOW,
    )
    assert (claim.number, claim.title, claim.body) == (
        7,
        'A "quoted"  yours until 2001-01-01T00:00:00Z title',
        "body: not this\nline",
    )
    assert claim.until == NOW + timedelta(minutes=30)
    assert claim.heartbeat_at() == NOW + timedelta(minutes=20)
    assert claim.text.endswith("goal: Launch")

    person_style = parse_claim('#8 "No deadline"  yours\nbody: ""', NOW)
    assert person_style.until is None and person_style.heartbeat_at() is None
    assert not person_style.heartbeat_due(NOW + timedelta(days=1))
    assert not person_style.expired(NOW + timedelta(days=1))


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "ok #7 doing",
        '#7 "Title"  theirs\nbody: ""',
        '#7 "Title"  yours',
        '#7 "Title"  yours\nbody: ""\nbody: ""',
        '#7 Title  yours\nbody: ""',
        '#7 "Title"  yours\nbody: 5',
        '#0 "Title"  yours\nbody: ""',
    ],
)
def test_unexpected_take_reply_is_an_adapter_error(reply):
    with pytest.raises(AdapterError):
        parse_claim(reply, NOW)


def test_adapter_calls_only_the_documented_tools():
    held = Claim(7, "Title", "Body", NOW + timedelta(minutes=30), NOW)
    tools = ScriptedTools(
        "context",
        '#7 "Title"  yours until 2026-01-01T12:30:00Z\nbody: "Body"',
        "ok #7 doing  until 2026-01-01T12:50:00Z\nApprovals: none",
        "ok #7 doing  until 2026-01-01T12:55:00Z",
        "ok #7 review",
        "ok #7 todo",
    )
    agent = AgentAdapter(tools, board="work", clock=lambda: NOW)
    assert agent.connect() == "context"
    taken = agent.take(task=7)
    assert (taken.number, taken.title, taken.body, taken.until, taken.renewed_at) == (
        7,
        "Title",
        "Body",
        held.until,
        NOW,
    )
    assert agent.heartbeat(held).until == NOW + timedelta(minutes=50)
    assert agent.progress(held, "Halfway").until == NOW + timedelta(minutes=55)
    # A board whose completions wait for a reviewer answers review, not done.
    assert agent.done(held) == "review"
    assert agent.release(held, "Stopping") == "todo"
    assert tools.calls == [
        ("session_start", {"board": "work"}),
        ("task_take_next", {"board": "work", "task": 7}),
        ("task_heartbeat", {"task": 7}),
        ("task_update", {"task": 7, "status_line": "Halfway"}),
        ("task_update", {"task": 7, "status": "done"}),
        ("task_handoff", {"task": 7, "to": "anyone", "note": "Stopping"}),
    ]


def test_only_a_refusal_loses_the_claim():
    held = Claim(7, "Title", "Body", NOW + timedelta(minutes=30), NOW)
    unreachable = ClientError("Cannot reach Commonplace")
    tools = ScriptedTools(unreachable, ToolRefused("Commonplace tool failed"), "ok #8 doing")
    agent = AgentAdapter(tools)
    # A transport failure says nothing about the claim: the agent retries.
    with pytest.raises(ClientError) as failure:
        agent.heartbeat(held)
    assert failure.value is unreachable
    with pytest.raises(ClaimLost):
        agent.heartbeat(held)
    # An answer about another card is a protocol error, never a renewal.
    with pytest.raises(AdapterError):
        agent.heartbeat(held)


def _reply(text, **extra):
    return {"result": {"content": [{"type": "text", "text": text}], "isError": True, **extra}}


def test_client_separates_a_refusal_from_an_unknown_failure():
    with pytest.raises(ToolRefused, match="Commonplace tool failed"):
        _tool_text(_reply("no: #7 is held by conn:other until 2026-01-01T12:30:00Z"))
    # Rules changes stay the one refusal that is retried.
    with pytest.raises(ToolRefused, match="rules changed"):
        _tool_text(_reply("no: rules_changed: read the rules again"))
    for unknown in (_reply("Worker timed out"), _reply("no: refused", error="timeout")):
        with pytest.raises(ClientError, match="Commonplace tool failed") as failure:
            _tool_text(unknown)
        assert not isinstance(failure.value, ToolRefused)
