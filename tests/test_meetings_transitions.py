"""Meeting lifecycle: the transition table, its single writer and the races it closes.

Three layers, none of them needs the console hub:

* the table itself (``lifecycle.py``) is checked as data: closed, every state reachable and able to
  end, and every ``(state, event, target)`` it answers for obeys the rules;
* the store is exercised for every active state against every event, in every order with the step
  a worker has in flight (the event arrives first, in between, or after);
* a worker (``engine.Run``) is run over the fake provider with an event fired, or the worker
  crashed, just before each call it makes to the store, for several kinds of meeting, then the beat
  that recovers orphans runs. Whatever the interleaving: the journal of states is a path of the
  table, the meeting ends and the worker goes idle, a stopped meeting ends discarded, and capture
  never opens after a stop.
"""

import ast
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from commonplace.meetings import adapter, engine, lifecycle, seal, store, worker
from tests.test_meetings_foundation import keys_and_providers  # noqa: F401

OWNER, USER, WS = "owner-1", "user-1", "ws-1"
LINK = "https://meet.example.test/m/room-1234?pwd=sentinel-passcode-9c1d"
PCM = b"SENTINEL-PCM-0123456789"
NOW = 1_800_000_000.0
STAMP = "2026-01-01T00:00:00Z"
LIVE_AT = {"announced_at": STAMP, "started_at": STAMP}
KEEPS = ("done", "timeout", "interrupted")  # the ends that keep what was captured
PACKAGE = Path(store.__file__).parent


@pytest.fixture(name="fake")
def fake_adapter(keys_and_providers):  # noqa: F811
    return keys_and_providers


# --- the table, as data -------------------------------------------------------------------------

ALL_STATES = (*lifecycle.ACTIVE, *lifecycle.ENDED, "failed:x")


def listed(state, target) -> bool:
    edges = lifecycle.edges()
    return (state, target) in edges or (
        target.startswith("failed:") and (state, lifecycle.FAILED) in edges
    )


def reach_from(start, edges):
    seen, todo = {start}, [start]
    while todo:
        here = todo.pop()
        for before, after in edges:
            if before == here and after not in seen:
                seen.add(after)
                todo.append(after)
    return seen


def test_the_table_is_closed_and_every_state_can_reach_its_end():
    known = {*lifecycle.ACTIVE, *lifecycle.ENDED, lifecycle.FAILED}
    assert set(lifecycle.TABLE) == set(lifecycle.ACTIVE)  # an ended state has no row: it is final
    for cells in lifecycle.TABLE.values():
        assert set(cells) <= set(lifecycle.EVENTS)
        assert all(set(targets) <= known for targets in cells.values())
    edges = lifecycle.edges()
    reachable = reach_from(lifecycle.INITIAL, edges)
    assert reachable == known  # every state, every end, and failure: nothing is dead code
    for state in lifecycle.ACTIVE:
        assert reach_from(state, edges) & {*lifecycle.ENDED, lifecycle.FAILED}, state
    ended = {*lifecycle.ENDED, lifecycle.FAILED}
    # a stop is only left through a discarding end; ending and processing never go back
    assert {after for before, after in edges if before == lifecycle.REMOVED} == {
        lifecycle.DISCARDED
    }
    before_ending = set(lifecycle.ACTIVE[:5])  # requested, joining, lobby, announcing, live
    for state in (lifecycle.ENDING, lifecycle.PROCESSING):
        assert not {after for before, after in edges if before == state} & before_ending
    # live is entered from announcing only, a stop is only written by a stop, and what keeps
    # what was captured is reached through processing
    assert {before for before, after in edges if after == lifecycle.LIVE} == {lifecycle.ANNOUNCING}
    assert all(
        lifecycle.REMOVED not in cells.get(lifecycle.ADVANCE, ())
        for cells in lifecycle.TABLE.values()
    )
    keeping = {before for before, after in edges if after in (lifecycle.DONE, lifecycle.TIMEOUT)}
    assert keeping == {lifecycle.PROCESSING}
    assert ended.isdisjoint(lifecycle.TABLE)


@pytest.mark.parametrize("state", ALL_STATES)
def test_the_table_answers_every_event_and_target_by_the_rules(state):
    for event in lifecycle.EVENTS:
        for target in (None, *ALL_STATES):
            for have in ((), ("announced_at",)):
                step = lifecycle.plan(state, event, target, have)
                if lifecycle.is_ended(state):
                    assert step is None, (state, event, target)  # an ended meeting never changes
                    continue
                if step is None:
                    continue
                assert listed(state, step.state), (state, event, target, step)
                assert not step.discard or step.ends
                if state == lifecycle.REMOVED:  # a stop always ends discarded, whatever asked
                    assert (step.state, step.discard) == (lifecycle.DISCARDED, True)
                if step.state == lifecycle.LIVE:  # capture opens from the confirmed announcement
                    assert state == lifecycle.ANNOUNCING and "announced_at" in have
                if step.state in (lifecycle.DONE, lifecycle.TIMEOUT):
                    assert (state, event) == (lifecycle.PROCESSING, lifecycle.FINISH_KEEP)
                if step.state == lifecycle.REMOVED:
                    assert event == lifecycle.STOP
                if step.state in lifecycle.ACTIVE[1:5]:  # joining, lobby, announcing, live
                    assert state not in (lifecycle.ENDING, lifecycle.PROCESSING, lifecycle.REMOVED)


def test_a_stop_wins_in_every_state_that_is_not_already_stopped():
    for state in lifecycle.ACTIVE:
        step = lifecycle.plan(state, lifecycle.STOP)
        if state == lifecycle.REMOVED:
            assert step is None  # already stopped: nothing to do
        elif state == lifecycle.REQUESTED:
            assert (step.state, step.discard) == (lifecycle.CANCELLED, True)
        else:
            assert step.state == lifecycle.REMOVED and not step.ends
    # a stopped meeting ends discarded whichever ending is asked for, and never keeps anything
    for event, target in (
        (lifecycle.FINISH_DISCARD, "failed:x"),
        (lifecycle.FINISH_DISCARD, lifecycle.CANCELLED),
        (lifecycle.RECOVER, None),
        (lifecycle.KILL, None),
    ):
        step = lifecycle.plan(lifecycle.REMOVED, event, target)
        assert (step.state, step.discard) == (lifecycle.DISCARDED, True), (event, target)
    assert lifecycle.plan(lifecycle.REMOVED, lifecycle.FINISH_KEEP, "done") is None
    assert lifecycle.plan(lifecycle.REMOVED, lifecycle.FINISH_DISCARD, "done") is None


# --- one writer ---------------------------------------------------------------------------------


def functions_with(path, needle):
    source = path.read_text()
    tree = ast.parse(source)
    return {
        node.name: ast.get_source_segment(source, node)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and needle in (ast.get_source_segment(source, node) or "")
    }


def test_the_state_is_written_by_one_function_and_nothing_else_updates_a_meeting():
    updates = {}
    for path in PACKAGE.glob("*.py"):
        for name, text in functions_with(path, "UPDATE meetings").items():
            if "UPDATE meetings" in text:
                updates[(path.name, name)] = text
    # the transition, and the heartbeat that only renews the lease
    assert set(updates) == {("store.py", "_transition"), ("store.py", "renew")}
    assert "WHERE id=? AND state=?" in updates[("store.py", "_transition")]  # a compare-and-set
    assert "SET lease_at=? WHERE" in updates[("store.py", "renew")]
    assert "state=?" not in updates[("store.py", "renew")].split("WHERE")[0]
    inserts = {n for p in PACKAGE.glob("*.py") for n in functions_with(p, "INSERT INTO meetings(")}
    assert inserts == {"invite"}
    assert (
        "lifecycle.INITIAL"
        in functions_with(PACKAGE / "store.py", "INSERT INTO meetings(")["invite"]
    )
    for path in PACKAGE.glob("*.py"):
        if path.name != "store.py":
            assert "_transition(" not in path.read_text(), path.name
            assert "SET state" not in path.read_text(), path.name


# --- a store and a worker without the hub -------------------------------------------------------


class Db:
    """The slice of the control database a worker reads and writes."""

    def __init__(self):
        self.audits = []

    def members(self, ref):
        return [{"id": USER}]

    def user(self, user_id):
        return {"name": "Sam Lee"}

    def audit(self, **fields):
        self.audits.append(fields)


class Clock:
    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return time.time() + self.offset

    def advance(self, seconds):
        self.offset += seconds


class Sink(engine.Sink):
    def __init__(self, fake):
        self.fake, self.frames, self.discarded, self.finished = fake, [], 0, 0

    def frame(self, meeting_id, frame):
        self.fake.log.append("frame")
        self.frames.append(frame.pcm)

    def discard(self, meeting_id):
        self.discarded += 1

    def finish(self, meeting_id):
        self.finished += 1


class Rig:
    def __init__(self, tmp_path):
        data = tmp_path / "data"
        self.gateway = SimpleNamespace(
            db=Db(),
            supervisor=SimpleNamespace(
                root=data / "supervisor", workspace_dir=lambda ws: data / "workspaces" / ws
            ),
        )
        m = self.meetings = store.open_meetings(self.gateway, WS)
        m.put_settings(OWNER, {"enabled": True, "max_concurrent": 8, "max_minutes": 5})
        self.connection_id = m.add_connection(OWNER, "fake", "main", {"client_secret": "s"})["id"]
        m.record_test(self.connection_id, True)
        m.update_connection(self.connection_id, {"enabled": True})
        m.put_person(USER, {"enabled": True}, transcript_ok=False)

    def enable(self):
        self.meetings.put_settings(OWNER, {"enabled": True})

    def seed(self) -> str:
        """A requested meeting, as an invite stores it (without the invite's per-day counter)."""
        meeting_id = uuid.uuid4().hex
        blob = seal.seal(self.meetings.data_dir, WS, "join_ref", meeting_id, LINK)
        with self.meetings._tx() as conn:
            conn.execute(
                "INSERT INTO meetings(id, user_id, connection_id, provider, join_ref, ext_meeting, "
                "title, bot_name, state, attested_at, request_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    meeting_id,
                    USER,
                    self.connection_id,
                    "fake",
                    blob,
                    "room-1234",
                    "Weekly sync",
                    "Notes bot for Sam Lee",
                    lifecycle.INITIAL,
                    STAMP,
                    uuid.uuid4().hex,
                    STAMP,
                ),
            )
            self.meetings._event(conn, meeting_id, lifecycle.INITIAL, STAMP)
        return meeting_id

    def plant(self, meeting_id):
        """What capture would have stored by now: a working segment and a participant."""
        with self.meetings._tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO meeting_segments VALUES (?,1,NULL,0,10,'words')",
                (meeting_id,),
            )
            conn.execute(
                "INSERT OR IGNORE INTO meeting_participants(meeting_id, ext_participant, label, "
                "host, joined_at) VALUES (?, 'p1', 'Alex', 0, ?)",
                (meeting_id, STAMP),
            )

    def held(self, meeting_id) -> int:
        with self.meetings._tx(write=False) as conn:
            return sum(
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE meeting_id=?", (meeting_id,)
                ).fetchone()["n"]
                for table in ("meeting_segments", "meeting_participants")
            )

    def row(self, meeting_id) -> dict:
        return self.meetings.run_row(meeting_id)

    def beat(self, *, sink=None, lapsed=True):
        """The worker's beat, run inline; ``lapsed`` is a clock far enough ahead that every lease
        has run out (the worker that held them is gone)."""
        clock = (lambda: time.time() + engine.LEASE_S + 5) if lapsed else time.time
        run = worker.Worker(
            self.gateway, clock=clock, sink_factory=lambda w, m: sink or engine.Sink()
        )
        return run.beat(WS, inline=True)


# --- the store, every state against every event -------------------------------------------------


def claim(m, mid):
    return m.claim(mid, NOW, engine.LEASE_S)


def adv(to, **fields):
    return lambda m, mid: m.advance(mid, to, NOW, **fields)


def leave(m, mid):
    return m.request_end(mid, "inviter_left", NOW)


def stop(m, mid):
    return m.request_end(mid, "stop_requested", NOW)


def end_with(state, reason, *, discard):
    return lambda m, mid: m.finish(mid, state, reason, NOW, discard=discard)


def recover(m, mid):
    return m.recover(mid, NOW)


def kill(m, mid):
    return m.disable()


NORMAL_END = [
    adv(lifecycle.ENDING, wipe_ref=True, reason="timeout"),
    adv(lifecycle.PROCESSING),
    end_with("timeout", "timeout", discard=False),
]
TO_LIVE = [claim, adv(lifecycle.ANNOUNCING), adv(lifecycle.LIVE, **LIVE_AT)]
PATHS = {  # the events that bring a requested meeting to each state, by the table's own rules
    lifecycle.REQUESTED: [],
    lifecycle.JOINING: [claim],
    lifecycle.LOBBY: [claim, adv(lifecycle.LOBBY)],
    lifecycle.ANNOUNCING: [claim, adv(lifecycle.ANNOUNCING)],
    lifecycle.LIVE: TO_LIVE,
    lifecycle.ENDING: [*TO_LIVE, leave],
    lifecycle.PROCESSING: [*TO_LIVE, *NORMAL_END[:2]],
    lifecycle.REMOVED: [*TO_LIVE, stop],
}
IN_FLIGHT = {  # what a worker has in flight in each state: its next normal step
    lifecycle.REQUESTED: [claim],
    lifecycle.JOINING: [adv(lifecycle.ANNOUNCING)],
    lifecycle.LOBBY: [adv(lifecycle.ANNOUNCING)],
    lifecycle.ANNOUNCING: [adv(lifecycle.LIVE, **LIVE_AT)],
    lifecycle.LIVE: [*NORMAL_END[:2], end_with("done", "done", discard=False)],
    lifecycle.ENDING: [adv(lifecycle.PROCESSING), end_with("done", "done", discard=False)],
    lifecycle.PROCESSING: [end_with("done", "done", discard=False)],
    lifecycle.REMOVED: [end_with("discarded", "stop_requested", discard=True)],
}
EVENTS = {
    "claim": [claim],
    "advance_lobby": [adv(lifecycle.LOBBY)],
    "advance_announcing": [adv(lifecycle.ANNOUNCING)],
    "advance_live_unannounced": [adv(lifecycle.LIVE)],
    "advance_live": [adv(lifecycle.LIVE, **LIVE_AT)],
    "advance_ending": [adv(lifecycle.ENDING, wipe_ref=True, reason="timeout")],
    "advance_processing": [adv(lifecycle.PROCESSING)],
    "leave": [leave],
    "stop": [stop],
    "finish_done": [end_with("done", "done", discard=False)],
    "finish_timeout": [end_with("timeout", "timeout", discard=False)],
    "finish_kept_as_discarded": [end_with("discarded", "x", discard=False)],
    "finish_discarded": [end_with("discarded", "host_removed", discard=True)],
    "finish_cancelled": [end_with("cancelled", "cancelled", discard=True)],
    "finish_failed": [end_with("failed:internal", "failed:internal", discard=True)],
    "finish_discarding_a_kept_end": [end_with("done", "done", discard=True)],
    "recover": [recover],
    "kill": [kill],
    "max_minutes": NORMAL_END,
}


def check_journal(rig, meeting_id, label):
    """Every state the meeting was ever recorded in follows the table, and nothing follows an end."""
    journal = rig.meetings.events_for(meeting_id)
    assert journal[0] == lifecycle.INITIAL, label
    for before, after in zip(journal, journal[1:]):
        assert not lifecycle.is_ended(before), (label, journal)
        assert listed(before, after), (label, journal)
    assert journal[-1] == rig.meetings.state_of(meeting_id), (label, journal)
    return journal


def settle(rig, meeting_id, label, *, exact=True):
    """The worker goes idle: the beat's backstop ends what is still active; then check the end."""
    m = rig.meetings
    stopped = lifecycle.REMOVED in m.events_for(meeting_id)
    if m.state_of(meeting_id) == lifecycle.REQUESTED:
        m.claim(meeting_id, NOW, engine.LEASE_S)  # the beat would start it
    if not lifecycle.is_ended(m.state_of(meeting_id)):
        assert m.recover(meeting_id, NOW) is not None, label
    row = rig.row(meeting_id)
    journal = check_journal(rig, meeting_id, label)
    assert lifecycle.is_ended(row["state"]) and not m.pending(), (label, row["state"])
    if stopped:  # a stopped meeting always ends discarded, with nothing kept
        assert (row["state"], row["end_reason"]) == ("discarded", "stop_requested"), label
    if lifecycle.LIVE in journal:  # capture needs the stamped announcement
        assert row["announced_at"], label
    if row["state"] not in KEEPS:  # an end that discards leaves nothing; a keeping end leaves all
        assert rig.held(meeting_id) == 0, (label, row["state"])
    elif exact:
        assert rig.held(meeting_id) == 2, (label, row["state"])
    with pytest.raises(store.Missing):
        m.open_join_ref(meeting_id)  # every end wipes the sealed link
    return row


def final_is_final(rig, meeting_id, label):
    """No event, in no form, changes an ended meeting."""
    before = (rig.row(meeting_id), rig.meetings.events_for(meeting_id))
    for steps in EVENTS.values():
        for item in steps:
            item(rig.meetings, meeting_id)
    rig.enable()
    assert (rig.row(meeting_id), rig.meetings.events_for(meeting_id)) == before, label


@pytest.mark.parametrize("state", lifecycle.ACTIVE)
def test_every_event_against_the_step_a_worker_has_in_flight(tmp_path, fake, state):
    """The event arrives before the in-flight step, between its parts, or after it. Whatever the
    order, the states recorded are a path of the table, a stop is never undone, nothing is left
    active once the worker is idle, and an ended meeting stays as it is."""
    rig = Rig(tmp_path)
    inflight = IN_FLIGHT[state]
    for name, steps in EVENTS.items():
        for position in range(len(inflight) + 1):
            label = f"{state}/{name}/{position}"
            meeting_id = rig.seed()
            for item in PATHS[state]:
                assert item(rig.meetings, meeting_id), label  # the normal way there is legal
            assert rig.meetings.state_of(meeting_id) == state, label
            rig.plant(meeting_id)
            for item in [*inflight[:position], *steps, *inflight[position:]]:
                item(rig.meetings, meeting_id)  # a refusal is the table's answer, not a failure
            rig.enable()  # the kill switch switched the workspace off
            check_journal(rig, meeting_id, label)
            settle(rig, meeting_id, label)
            final_is_final(rig, meeting_id, label)


def test_a_stop_is_left_only_through_a_discarding_end(tmp_path, fake):
    rig = Rig(tmp_path)
    m = rig.meetings
    meeting_id = rig.seed()
    for item in PATHS[lifecycle.REMOVED]:
        item(m, meeting_id)
    rig.plant(meeting_id)
    refused = [n for n in EVENTS if n.startswith(("advance", "finish_done", "finish_timeout"))]
    refused += ["claim", "finish_kept_as_discarded", "finish_discarding_a_kept_end"]
    for name in refused:
        for item in EVENTS[name]:
            assert item(m, meeting_id) is False, name
    stop(m, meeting_id)
    leave(m, meeting_id)  # already stopped: both are no-ops
    assert (rig.row(meeting_id)["state"], rig.held(meeting_id)) == (lifecycle.REMOVED, 2)
    assert m.finish(meeting_id, "failed:internal", "failed:internal", NOW, discard=True)
    row = rig.row(meeting_id)
    assert (row["state"], row["end_reason"], rig.held(meeting_id)) == (
        "discarded",
        "stop_requested",
        0,
    )


def test_an_interrupted_meeting_keeps_what_it_captured_and_a_stopped_one_does_not(tmp_path, fake):
    """Design note, failure and replay: a crash ends the meeting ``interrupted`` and what was
    stored stays; a stop is never overtaken by a crash."""
    rig = Rig(tmp_path)
    m = rig.meetings
    crashed, stopped = rig.seed(), rig.seed()
    for meeting_id, path in ((crashed, TO_LIVE), (stopped, PATHS[lifecycle.REMOVED])):
        for item in path:
            item(m, meeting_id)
        rig.plant(meeting_id)
    assert m.recover(crashed, NOW) == ("interrupted", "interrupted")
    assert m.recover(stopped, NOW) == ("discarded", "stop_requested")
    assert (rig.held(crashed), rig.held(stopped)) == (2, 0)


# --- a worker, with an event or a crash before every call it makes ------------------------------


class Crash(BaseException):
    """The worker process dying: nothing in the engine may catch it."""


WATCHED = frozenset(
    {
        "claim",
        "run_row",
        "renew",
        "advance",
        "finish",
        "state_of",
        "outbox_begin",
        "outbox_mark",
        "outbox_state",
        "participant_seen",
    }
)


class Probe:
    """The worker's view of the store, firing ``fire(name)`` just before its ``at``-th call (the
    worker thread's own calls only: the lease heartbeat runs beside it)."""

    def __init__(self, real, at, fire):
        self._real, self._at, self._fire = real, at, fire
        self._thread = threading.current_thread()
        self.calls = 0

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        if name not in WATCHED or threading.current_thread() is not self._thread:
            return attr

        def call(*args, **kwargs):
            self.calls += 1
            if self.calls == self._at:
                self._fire(name)
            return attr(*args, **kwargs)

        return call


def scenario_ends(how):
    """A meeting that is admitted from the lobby, captures, and then ends in the given way."""
    return {
        "ended": [adapter.Event("meeting_ended", "e9")],
        "removed": [adapter.Event("removed", "e9")],
        "bot_stop": [adapter.Event("chat", "e9", "p1", text="Bot stop")],
    }[how]


def set_up(rig, fake, how):
    fake.__init__()
    adapter.register(fake)
    rig.enable()
    meeting_id = rig.seed()

    def plant():  # what the capture slice would store, only while the gate can be open
        if rig.meetings.state_of(meeting_id) == lifecycle.LIVE:
            rig.plant(meeting_id)

    fake.join_result = adapter.Lobby("lobby-1")
    fake.frames = [adapter.Frame(PCM)]
    fake.script = [
        adapter.Event("admitted", "e1"),
        adapter.Event("tick"),
        plant,
        adapter.Event("participant_joined", "e2", "p1", text="Alex"),
        adapter.Event("tick"),
        *scenario_ends(how),
    ]
    return meeting_id


FIRES = ("stop", "leave", "kill", "recover", "clock", "crash")


def execute(rig, fake, how, event, at):
    """One meeting, with ``event`` fired just before the ``at``-th store call (``at`` = None only
    counts the calls). Returns what the checks need."""
    meeting_id = set_up(rig, fake, how)
    clock, sink = Clock(), Sink(fake)
    run = engine.Run(rig.gateway, WS, meeting_id, clock=clock, sink=sink)
    real = run.meetings
    opened, fired = [], {}
    real_allow = run.capture.allow

    def allow(verify=None):
        opened.append(real_allow(verify))
        return opened[-1]

    run.capture.allow = allow

    def fire(call):
        fired.update(call=call, before=real.state_of(meeting_id), opened=any(opened))
        if event == "stop":
            real.request_end(meeting_id, "stop_requested", clock())
        elif event == "leave":
            real.request_end(meeting_id, "inviter_left", clock())
        elif event == "kill":
            real.disable()
        elif event == "recover":
            engine.recover(rig.gateway, WS, meeting_id, clock=clock)
        elif event == "clock":
            clock.advance(3600)
        else:
            raise Crash

    probe = run.meetings = Probe(real, at, fire if at else None)
    crashed = False
    try:
        run.run()
    except Crash:
        crashed = True
    run.meetings = real
    if at is not None and not fired and event != "crash":
        fire("after")  # past the last call: the meeting is over, nothing may change it
    return SimpleNamespace(
        meeting_id=meeting_id,
        calls=probe.calls,
        fired=fired,
        crashed=crashed,
        sink=sink,
        at_crash=real.state_of(meeting_id),
        joins_at_crash=len(fake.joins),
    )


def check_worker_case(rig, fake, how, event, at, label):
    out = execute(rig, fake, how, event, at)
    m, meeting_id, fired = rig.meetings, out.meeting_id, out.fired
    rig.enable()
    if out.crashed:  # the worker is gone: its lease lapses and the beat recovers the meeting
        rig.beat(sink=out.sink)
    row = settle(rig, meeting_id, label, exact=False)
    state, reason = row["state"], row["end_reason"]
    journal = m.events_for(meeting_id)
    # the consent rule, whatever happened
    if lifecycle.LIVE in journal:
        assert m.outbox_state(meeting_id, "announce", "announce") == "sent", label
    assert len(fake.joins) <= 1 and len(fake.posts) <= 3, label
    assert len([t for t, _ in fake.posts if t.startswith("I'm a notes bot")]) <= 1, label
    if "frame" in fake.log:
        assert "post" in fake.log and fake.log.index("post") < fake.log.index("frame"), label
    assert not fake.joins or fake.leaves, label  # the bot is never left in the meeting
    if out.crashed and not lifecycle.is_ended(out.at_crash):
        if out.at_crash != lifecycle.REQUESTED:  # recovery ends it and never rejoins
            assert len(fake.joins) == out.joins_at_crash, label
            want = "discarded" if out.at_crash == lifecycle.REMOVED else "interrupted"
            assert state == want, (label, out.at_crash, state)
    final_is_final(rig, meeting_id, label)
    if not fired or lifecycle.is_ended(fired["before"]):
        return
    before = fired["before"]
    effective = event != "recover" or before in lifecycle.RUNNING
    if event in ("stop", "leave", "kill", "recover") and effective and not fired["opened"]:
        # the gate was closed when the request landed, and capture never opens after it
        assert not out.sink.frames and fake.audio_opened_after_posts is None, label
    if event == "stop":
        assert state == ("cancelled" if before == lifecycle.REQUESTED else "discarded"), label
        assert reason == ("cancelled" if before == lifecycle.REQUESTED else "stop_requested"), label
    elif event == "kill":
        assert (state, reason) == ("cancelled", "cancelled"), label
    elif event == "recover" and before in lifecycle.RUNNING:
        assert state == "interrupted", label
    elif event == "leave":  # keeps what was captured, unless the worker was already discarding
        assert state == ("cancelled" if before == lifecycle.REQUESTED else state), label
        assert state in ("done", "discarded", "cancelled"), label
    elif event == "clock":
        assert state in ("timeout", "done", "discarded", "failed:not_admitted"), label


@pytest.mark.parametrize("event", FIRES)
@pytest.mark.parametrize("how", ["ended", "removed", "bot_stop"])
def test_an_event_or_a_crash_before_any_store_call_of_a_worker(tmp_path, fake, how, event):
    rig = Rig(tmp_path)
    baseline = execute(rig, fake, how, event, None)
    assert baseline.calls > 20 and not baseline.crashed
    assert (
        rig.row(baseline.meeting_id)["state"]
        == {
            "ended": "done",
            "removed": "discarded",
            "bot_stop": "discarded",
        }[how]
    )
    for at in range(1, baseline.calls + 2):  # the last one fires after the worker is done
        check_worker_case(rig, fake, how, event, at, f"{how}/{event}/{at}")


def test_an_orphaned_stopped_meeting_is_finished_discarded_and_the_worker_goes_idle(tmp_path, fake):
    """The bug class's last find: a stop, then the worker dies. The beat must end the meeting
    discarded, not leave it ``removed`` and re-run recovery on every beat forever."""
    rig = Rig(tmp_path)
    m = rig.meetings
    meeting_id = rig.seed()
    now = time.time()
    assert m.claim(meeting_id, now, engine.LEASE_S)
    assert m.advance(meeting_id, lifecycle.ANNOUNCING, now)
    assert m.advance(meeting_id, lifecycle.LIVE, now, **LIVE_AT)
    rig.plant(meeting_id)
    m.request_end(meeting_id, "stop_requested", now)
    fake.handles[meeting_id] = adapter.JoinHandle("h")
    assert rig.beat(lapsed=False) == [] and rig.row(meeting_id)["state"] == "removed"  # not yet
    assert rig.beat() == []  # the lease ran out: nothing is started, the meeting is ended
    row = rig.row(meeting_id)
    assert (row["state"], row["end_reason"]) == ("discarded", "stop_requested")
    assert fake.leaves == ["h"] and fake.joins == [] and rig.held(meeting_id) == 0
    assert not m.pending() and m.expired(now + 10**6, engine.LEASE_S) == []
    assert engine.recover(rig.gateway, WS, meeting_id) is None  # a later beat has nothing to do
    assert [a["reason"] for a in rig.gateway.db.audits] == ["stop_requested"]
