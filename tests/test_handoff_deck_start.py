"""``/api/handoff/start``: the Deck's handoff from a stored thread, with its authority unchanged."""

import json

import pytest

from commonplace.handoff import manual
from commonplace.handoff.models import StoredThread, SuccessorRef, ThreadMeta
from commonplace.handoff.recall import ThreadNotFound
from tests.e2e_handoff.fake_t3 import source_fixture
from tests.test_handoff_mcp_tools import ToolHub

OLD, NEW = "thr_old", "thr_new"
LIVE = "mcp:source"  # the thread the fake T3 can resolve: the successor of the stored thread


class ChainRecall:
    """Stored threads with a parent chain, readable only inside their own workspace."""

    def __init__(self, workspace):
        self.workspace = workspace
        self.threads = {
            OLD: StoredThread(
                OLD, workspace, "mcp:origin", 12, 3, "h1", created_at="2026-11-01T10:00:00Z"
            ),
            NEW: StoredThread(
                NEW, workspace, "mcp:middle", 30, 6, "h2", created_at="2026-11-02T10:00:00Z"
            ),
        }
        self.parents = {NEW: OLD}
        self.unreadable = set()

    def thread(self, principal, thread_id):
        row = self.threads.get(thread_id)
        if (
            row is None
            or thread_id in self.unreadable
            or row.workspace_id != principal.workspace_id
            or not principal.policy.has_scope("threads:read")
        ):
            raise ThreadNotFound("thread not found")
        return row

    def chain(self, principal, thread_id):
        rows, seen = [], set()
        while thread_id and thread_id not in seen:
            seen.add(thread_id)
            try:
                rows.append(self.thread(principal, thread_id))
            except ThreadNotFound:
                break
            thread_id = self.parents.get(thread_id, "")
        return rows


@pytest.fixture
def deck(tmp_path, monkeypatch, request):
    hub = ToolHub(tmp_path)
    request.addfinalizer(hub.close)
    hub.setting()
    fake = source_fixture(tmp_path / "fake-t3", monkeypatch)
    monkeypatch.setattr(manual, "T3", lambda: fake.adapter)
    home = tmp_path / "home"
    (home / ".commonplace").mkdir(parents=True)
    (home / ".commonplace/handoff-client.json").write_text(
        json.dumps({"workspaces": {str(fake.project): "home"}})
    )
    monkeypatch.setenv("HOME", str(home))
    for name in ("COMMONPLACE_HANDOFF", "COMMONPLACE_AGENT", "COMMONPLACE_TASK"):
        monkeypatch.delenv(name, raising=False)
    hub.fake = fake
    hub.ctx.recall = ChainRecall(hub.workspace)
    # OLD was handed off to a middle thread; that was handed off to NEW; NEW continues in LIVE.
    _seed(hub, OLD, "mcp:middle", source="mcp:origin")
    _seed(hub, NEW, LIVE, source="mcp:middle")
    return hub


def _seed(hub, stored=OLD, live=LIVE, *, source="", state="done", agent="manual-writer", task=""):
    """A handoff of ``source`` that stored ``stored`` and registered ``live`` as its successor."""
    source = source or f"origin-{stored}"
    row, _ = hub.ctx.handoff_db.begin(
        hub.workspace,
        f"thread:{source}",
        ThreadMeta(thread_id=source, agent=agent, task_ref=task),
        connection_id="",
    )
    hub.ctx.handoff_db.mark_stored(row.id, stored, "# doc")
    if state == "launch_failed":
        hub.ctx.handoff_db.mark_launch_failed(row.id, "did not start")
    elif state == "done":
        hub.ctx.handoff_db.set_successor(row.id, SuccessorRef(f"t3:{live}", thread_id=live))
    elif state == "done-no-thread":
        hub.ctx.handoff_db.set_successor(row.id, SuccessorRef("claude:next"))
    return row


def api(hub, token, method, path, body=None):
    status, text = hub.raw(token, body if body is not None else b"", method=method, path=path)
    return status, (json.loads(text) if text else None)


def writer_token(hub, name="deck-writer", **options):
    return hub.reader_token(
        name,
        **{
            "scopes": ["threads:read", "memory:read", "handoff:write"],
            "trust": "trusted",
            **options,
        },
    )


def ask(hub, token, thread=NEW):
    return api(hub, token, "GET", f"/api/handoff/start?thread={thread}")


START = {"thread": NEW, "to": "codex/gpt-6.1-sol"}


def test_a_launching_reader_sees_the_thread_that_continues_the_work(deck):
    status, offer = ask(deck, writer_token(deck))
    assert status == 200 and offer["state"] == "ready", (status, offer)
    assert offer["continues"]["thread_id"] == LIVE and offer["handoff_id"].startswith("hof_")


@pytest.mark.parametrize(
    "options",
    [
        {"trust": "standard", "scopes": ["threads:read", "memory:read", "handoff:write"]},
        {"trust": "trusted", "scopes": ["threads:read", "memory:read"]},
    ],
)
def test_denied_connection_gets_no_action_and_nothing_starts(deck, options):
    token = writer_token(deck, "denied", **options)
    status, offer = ask(deck, token)
    assert status == 200 and offer["state"] == "unavailable" and offer["can_start"] is False
    assert "signed-in workspace member" in offer["reason"] and "continues" not in offer
    for body in (START, {**START, "confirm": True}):
        status, result = api(deck, token, "POST", "/api/handoff/start", body)
        assert status == 403 and "handoff:write" in result["error"]
    assert not deck.fake.log.exists()
    assert len(deck.ctx.handoff_db.list(deck.workspace)) == 2


def test_unreadable_and_missing_threads_look_the_same(deck):
    deck.ctx.recall.unreadable.add(NEW)
    token = writer_token(deck)
    first = ask(deck, token)
    second = ask(deck, token, "thr_missing")
    assert first == second == (404, {"error": "not found"})
    status, result = api(deck, token, "POST", "/api/handoff/start", START)
    assert (status, result) == (404, {"error": "not found"})
    assert not deck.fake.log.exists()


def test_a_token_without_thread_access_cannot_ask(deck):
    token = deck.reader_token(
        "blind", scopes=["handoff:write"], read_wings=["shared"], trust="trusted"
    )
    assert ask(deck, token) == (404, {"error": "not found"})


def test_preview_names_destination_and_what_is_carried_and_changes_nothing(deck):
    token = writer_token(deck)
    before = deck.state()
    status, result = api(
        deck,
        token,
        "POST",
        "/api/handoff/start",
        {**START, "effort": "high", "note": "Keep going with the tests."},
    )
    assert status == 200 and result["preview"] is True, result
    assert result["destination"] == {"provider": "codex", "model": "gpt-6.1-sol", "effort": "high"}
    assert result["note"] == "Keep going with the tests."
    assert result["carried"] == {
        "earlier_threads": 2,
        "earlier_messages": 42,
        "history": [NEW, OLD],
    }
    assert result["continues"]["thread_id"] == LIVE
    assert "cwd" not in result and "transcript_paths" not in result
    assert "project" not in json.dumps(result) and "provider/projects" not in json.dumps(result)
    calls = [json.loads(line)["argv"][:2] for line in deck.fake.log.read_text().splitlines()]
    assert calls == [["thread", "show"]]  # resolved, never started or settled
    assert deck.state()[:3] == before[:3]


def test_confirmed_start_saves_launches_and_settles_through_the_same_path(deck):
    token = writer_token(deck)
    status, result = api(deck, token, "POST", "/api/handoff/start", {**START, "confirm": True})
    assert status == 200 and result["preview"] is False
    assert result["state"] == "done" and result["source_settled"] is True
    assert result["successor"]["agent_ref"] == "t3:mcp:successor"
    saved = deck.ctx.handoff_db.by_key(deck.workspace, f"thread:{LIVE}")
    assert saved is not None and saved.agent == "manual-writer"
    # The thread is now followed by a later one: the action moves there.
    status, offer = ask(deck, token)
    assert offer["state"] == "unavailable" and "later thread" in offer["reason"]


def test_destination_is_validated_with_a_next_step(deck):
    status, result = api(
        deck, writer_token(deck), "POST", "/api/handoff/start", {**START, "to": "nonsense"}
    )
    assert status == 400 and "provider/model" in result["error"] and "codex/" in result["next_step"]
    assert not deck.fake.log.exists()


@pytest.mark.parametrize(
    "body",
    [
        {**START, "workspace": "other"},
        {**START, "confirm": "yes"},
        {"to": "codex/sol"},
        {"thread": OLD},
        {**START, "note": 3},
    ],
)
def test_bad_requests_are_refused_before_any_thread_work(deck, body):
    status, _ = api(deck, writer_token(deck), "POST", "/api/handoff/start", body)
    assert status == 400 and not deck.fake.log.exists()


def test_off_says_why_and_where_to_turn_it_on(deck):
    deck.setting(enabled=False)
    token = writer_token(deck)
    status, offer = ask(deck, token)
    assert offer["state"] == "unavailable" and "switched off" in offer["reason"]
    assert offer["settings_link"] == "/app/settings/memory" and offer["next_step"]
    status, result = api(deck, token, "POST", "/api/handoff/start", {**START, "confirm": True})
    assert status == 409 and "switched off" in result["error"] and result["next_step"]
    assert not deck.fake.log.exists()


def test_a_start_that_cannot_launch_says_the_old_thread_still_runs(deck):
    config = json.loads(deck.fake.config.read_text())
    deck.fake.config.write_text(json.dumps({**config, "start_fails": True}))
    status, result = api(
        deck, writer_token(deck), "POST", "/api/handoff/start", {**START, "confirm": True}
    )
    assert status == 409 and "still running" in result["error"]
    assert "T3" in result["next_step"]
    assert deck.ctx.handoff_db.by_key(deck.workspace, f"thread:{LIVE}").state == "launch_failed"


def test_checkout_not_bound_has_a_next_step(deck, monkeypatch):
    monkeypatch.setattr(manual, "workspace_for", lambda _: "another-workspace")
    status, result = api(deck, writer_token(deck), "POST", "/api/handoff/start", START)
    assert status == 409 and "not bound" in result["error"]
    assert "map its checkout" in result["next_step"]


@pytest.mark.parametrize(
    "state,expected",
    [
        ("launch_failed", "did not start"),
        ("done-no-thread", "without a thread id"),
    ],
)
def test_unfinished_handoffs_explain_themselves(deck, state, expected):
    deck.ctx.handoff_db._conn.execute("DELETE FROM handoffs")
    row = _seed(deck, NEW, source="mcp:middle", state=state)
    status, offer = ask(deck, writer_token(deck))
    assert offer["state"] == "unavailable" and expected in offer["reason"] and offer["next_step"]
    assert offer["handoff_id"] == row.id
    assert offer.get("retry", False) is (state == "launch_failed")


def test_a_thread_saved_alone_says_there_is_nothing_to_continue(deck):
    deck.ctx.handoff_db._conn.execute("DELETE FROM handoffs")
    status, offer = ask(deck, writer_token(deck))
    assert offer["state"] == "unavailable" and "saved on its own" in offer["reason"]


def test_the_action_points_at_the_later_thread_when_the_caller_can_read_it(deck):
    token = writer_token(deck)
    status, offer = ask(deck, token, OLD)
    assert offer["state"] == "unavailable" and offer["later_thread"] == NEW
    deck.ctx.recall.unreadable.add(NEW)
    status, offer = ask(deck, token, OLD)
    assert offer["later_thread"] == "" and "later thread" in offer["reason"]
