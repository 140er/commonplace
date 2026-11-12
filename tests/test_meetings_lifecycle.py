"""Meeting-join bots, lifecycle over the fake adapter: invite, announcement, consent, end.

Real HTTP against the console hub for the person and owner surfaces; the worker runs inline over
scripted fake meetings, so nothing touches a platform, a network or an audio device. The consent
rule under test: announce first, capture nothing until the announcement is confirmed delivered,
otherwise leave and keep nothing.
"""

import logging
import threading
import time
from pathlib import Path

import pytest

from commonplace.meetings import adapter, engine, store, worker
from tests._console_helpers import console_hub_fixture  # noqa: F401
from tests.test_commonplace_boards import mcp
from tests.test_meetings_foundation import (  # noqa: F401
    data_files,
    enable,
    go_live,
    keys_and_providers,
    store_for,
)

PASSCODE = "sentinel-passcode-5d2e84b1"
PCM = b"SENTINEL-PCM-0123456789"
LINK = f"https://meet.example.test/m/room-1234?pwd={PASSCODE}"
LINK_TWO = "https://meet.example.test/m/room-5678"
ANNOUNCEMENT = (
    "I'm a notes bot, not a person. {who} invited me to listen and take notes. The audio is "
    "transcribed on their own server. Any host can remove me; type 'bot stop' to make me leave "
    "and discard everything. Ask {who} about the notes."
)


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


@pytest.fixture(autouse=True)
def inline_worker(monkeypatch):
    monkeypatch.setattr(worker, "AUTOSTART", False)


@pytest.fixture(name="fake")
def fake_adapter(keys_and_providers):  # noqa: F811
    return keys_and_providers


WHO = "Sam Lee"


def ready(hub):
    hub.hub.db._conn.execute("UPDATE users SET name=? WHERE id=?", (WHO, hub.member_user))
    hub.hub.db._conn.commit()
    connection_id = go_live(hub)
    assert hub.request(hub.member, "PATCH", "/api/meetings/me", {"enabled": True})[0] == 200
    return connection_id


def invite(hub, link=LINK, who=None, **extra):
    body = {
        "link": link,
        "title": "Weekly sync",
        "attested": True,
        "request_id": f"req-{len(extra)}-{abs(hash(link)) % 10**6}-{time.time_ns()}",
        **extra,
    }
    status, out, raw = hub.request(who or hub.member, "POST", "/api/meetings", body)
    return status, out, raw


def new_meeting(hub, **extra):
    status, body, _ = invite(hub, **extra)
    assert status == 201, body
    return body["meeting"]["id"]


def drive(hub, fake, clock=None, sink=None):
    sink = sink or Sink(fake)
    gateway = hub.hub.gateway
    run = worker.Worker(gateway, clock=clock or time.time, sink_factory=lambda w, m: sink)
    started = run.beat(hub.hub.workspace, inline=True)
    return started, sink


def state_of(hub, meeting_id):
    row = store_for(hub).run_row(meeting_id)
    return row["state"], row["end_reason"]


def count(hub, table, meeting_id):
    with store_for(hub)._tx(write=False) as conn:
        return conn.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE meeting_id=?", (meeting_id,)
        ).fetchone()["n"]


def announce_text(hub):
    return ANNOUNCEMENT.format(who=WHO)


# --- off until configured -----------------------------------------------------------------------


def test_nothing_runs_without_the_switches(console_hub, fake, monkeypatch):
    hub = console_hub
    monkeypatch.setattr(worker, "AUTOSTART", True)
    gateway = hub.hub.gateway
    assert worker.Worker(gateway).beat(hub.hub.workspace, inline=True) == []
    worker.resume(gateway)
    assert not [t for t in threading.enumerate() if t.name.startswith("meeting-")]
    assert data_files(hub) == []
    assert invite(hub)[0] == 404  # meetings are off: absent for a member
    assert invite(hub, who=hub.owner)[0] == 409
    enable(hub)
    assert invite(hub)[1]["code"] == "not_opted_in"
    assert hub.request(hub.member, "PATCH", "/api/meetings/me", {"enabled": True})[0] == 200
    assert invite(hub)[1]["code"] == "no_connection"
    assert fake.joins == [] and fake.posts == []
    assert store_for(hub).meetings_for(hub.member_user) == []
    assert worker.Worker(gateway).beat(hub.hub.workspace, inline=True) == []
    assert not [t for t in threading.enumerate() if t.name.startswith("meeting-")]


def test_an_invite_alone_joins_nothing(console_hub, fake):
    hub = console_hub
    ready(hub)
    meeting_id = new_meeting(hub)
    assert state_of(hub, meeting_id) == ("requested", None)
    assert fake.joins == [] and fake.posts == []
    assert not [t for t in threading.enumerate() if t.name.startswith("meeting-")]


# --- invite -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra,code",
    [
        ({"attested": False}, "attestation_required"),
        ({"attested": "yes"}, "attestation_required"),
        ({"link": "https://elsewhere.example.test/m/room-1234"}, "invalid_link"),
        ({"link": "http://meet.example.test/m/room-1234"}, "invalid_link"),
        ({"link": 5}, "invalid_link"),
        ({"title": ""}, "invalid_title"),
        ({"title": "x" * 121}, "invalid_title"),
        ({"disclosure": "all"}, "invalid_disclosure"),
        ({"board": ""}, "invalid_board"),
        ({"request_id": "short"}, "invalid_request_id"),
        ({"start_at": "tomorrow"}, "invalid_start"),
        ({"start_at": "2020-01-01T00:00:00Z"}, "invalid_start"),
        ({"start_at": "2099-01-01T00:00:00Z"}, "invalid_start"),
        ({"keep_transcript": True}, "transcript_not_allowed"),
        ({"keep_transcript": "yes"}, "invalid_value"),
        ({"connection_id": "nope"}, "unknown_connection"),
        ({"bot_name": "Boss"}, "invalid_field"),
    ],
)
def test_invites_are_validated_and_leak_nothing(console_hub, fake, extra, code):
    hub = console_hub
    ready(hub)
    body = {"link": LINK, "title": "T", "attested": True, "request_id": "req-valid-1", **extra}
    status, out, raw = hub.request(hub.member, "POST", "/api/meetings", body)
    assert status in (400, 409), out
    assert out["code"] == code
    assert PASSCODE.encode() not in raw
    assert store_for(hub).meetings_for(hub.member_user) == []


def test_a_provider_that_cannot_announce_is_refused(console_hub, fake):
    hub = console_hub
    ready(hub)
    fake.caps = adapter.Caps(chat_send=False)
    status, out, _ = invite(hub)
    assert (status, out["code"]) == (409, "no_chat_send")


def test_a_repeated_invite_is_one_meeting_and_the_link_is_never_echoed(console_hub, fake, caplog):
    hub = console_hub
    ready(hub)
    caplog.set_level(logging.DEBUG)
    body = {"link": LINK, "title": "Sync", "attested": True, "request_id": "retry-me-1234"}
    first = hub.request(hub.member, "POST", "/api/meetings", body)
    again = hub.request(hub.member, "POST", "/api/meetings", {**body, "title": "Other"})
    assert (first[0], again[0]) == (201, 200)
    assert first[1]["meeting"]["id"] == again[1]["meeting"]["id"]
    meeting = first[1]["meeting"]
    assert meeting["state"] == "requested" and meeting["bot_name"] == f"Notes bot for {WHO}"
    assert meeting["title"] == "Sync" and meeting["announced_at"] is None
    assert len(store_for(hub).meetings_for(hub.member_user)) == 1
    for raw in (first[2], again[2], hub.get(hub.member, "/api/meetings")[2]):
        assert PASSCODE.encode() not in raw and b"join_ref" not in raw
    assert PASSCODE not in caplog.text
    audit = repr([dict(r) for r in hub.hub.db.audit_entries(limit=100)])
    assert PASSCODE not in audit and "meetings.invite" in audit
    assert PASSCODE.encode() not in b"".join(p.read_bytes() for p in data_files(hub))


def test_the_daily_workspace_limit_and_the_attested_time_are_recorded(console_hub, fake):
    hub = console_hub
    ready(hub)
    assert hub.request(hub.owner, "PUT", "/api/meetings/settings", {"daily_meetings": 1})[0] == 200
    meeting = hub.get(hub.member, "/api/meetings")[1]
    assert meeting == {"meetings": []}
    first = new_meeting(hub)
    status, out, _ = invite(hub, LINK_TWO)
    assert (status, out["code"]) == (429, "daily_limit")
    with store_for(hub)._tx(write=False) as conn:
        row = conn.execute("SELECT attested_at, created_at FROM meetings WHERE id=?", (first,))
        row = row.fetchone()
    assert row["attested_at"] == row["created_at"]


# --- the consent rule -------------------------------------------------------------------------


def test_the_announcement_comes_first_and_capture_only_after_it_was_sent(console_hub, fake):
    hub = console_hub
    ready(hub)
    meeting_id = new_meeting(hub)
    fake.frames = [adapter.Frame(PCM), adapter.Frame(PCM)]
    fake.script = [
        adapter.Event("participant_joined", "e1", "p1", text="Alex"),
        adapter.Wait(),
        adapter.Event("meeting_ended", "e2"),
    ]
    started, sink = drive(hub, fake)
    assert started == [meeting_id]
    assert fake.log[:4] == ["join", "post", "audio", "frame"]
    assert fake.posts[0][0] == announce_text(hub)
    assert fake.audio_opened_after_posts == 1
    assert sink.frames == [PCM, PCM] and sink.finished == 1 and sink.discarded == 0
    assert state_of(hub, meeting_id) == ("done", "done")
    assert fake.joins == [(f"Notes bot for {WHO}", meeting_id)]
    assert store_for(hub).events_for(meeting_id) == [
        "requested",
        "joining",
        "announcing",
        "live",
        "ending",
        "processing",
        "done",
    ]
    assert fake.leaves == [f"handle-{meeting_id}"]
    meeting = store_for(hub).meeting(meeting_id, hub.member_user)
    assert meeting["announced_at"] and meeting["started_at"] and meeting["ended_at"]
    with store_for(hub)._tx(write=False) as conn:
        assert conn.execute("SELECT join_ref FROM meetings").fetchone()["join_ref"] is None
        outbox = dict(conn.execute("SELECT id, state, ext_post FROM meeting_outbox").fetchone())
    assert outbox["state"] == "sent" and outbox["ext_post"] == f"post-{outbox['id']}"


def test_the_bot_never_asks_for_audio_before_the_announcement_and_unconfirmed_means_leave(
    console_hub, fake
):
    """A failed, errored or unconfirmed announcement: no frame, no audio stream, nothing stored."""
    hub = console_hub
    ready(hub)
    cases = [
        ("chat-off", None),
        ("exception", [ConnectionError("lost")]),
        ("failed-twice", [adapter.PostResult(False), adapter.PostResult(False)]),
    ]
    for label, results in cases:
        fake.__init__()
        adapter.register(fake)
        fake.frames = [adapter.Frame(PCM)]
        fake.script = [adapter.Event("participant_joined", "e1", "p1", text="Alex")]
        fake.post_results = list(results or [])
        meeting_id = new_meeting(hub, link=f"https://meet.example.test/m/case-{label}-1")
        if label == "chat-off":
            fake.caps = adapter.Caps(chat_send=False)
        _, sink = drive(hub, fake)
        assert state_of(hub, meeting_id) == (
            "failed:no_announcement",
            "failed:no_announcement",
        ), label
        assert sink.frames == [] and fake.audio_opened_after_posts is None, label
        assert "audio" not in fake.log and "frame" not in fake.log, label
        assert fake.leaves == [f"handle-{meeting_id}"], label
        for table in ("meeting_segments", "meeting_participants"):
            assert count(hub, table, meeting_id) == 0, label
        assert store_for(hub).meeting(meeting_id, hub.member_user)["announced_at"] is None
        assert store_for(hub).meeting(meeting_id, hub.member_user)["started_at"] is None
        with store_for(hub)._tx(write=False) as conn:
            ref = conn.execute("SELECT join_ref FROM meetings WHERE id=?", (meeting_id,))
            assert ref.fetchone()["join_ref"] is None


def test_an_unknown_announcement_outcome_is_not_retried_and_one_definite_failure_is(
    console_hub, fake
):
    hub = console_hub
    ready(hub)
    meeting_id = new_meeting(hub)
    fake.post_results = [adapter.PostResult(False)]  # one definite failure, then it goes out
    fake.script = [adapter.Event("meeting_ended", "e1")]
    _, sink = drive(hub, fake)
    assert state_of(hub, meeting_id) == ("done", "done")
    assert len(fake.posts) == 1  # delivered once, under one idempotency key
    with store_for(hub)._tx(write=False) as conn:
        row = conn.execute("SELECT state, attempts FROM meeting_outbox").fetchone()
    assert (row["state"], row["attempts"]) == ("sent", 2)
    fake.__init__()
    adapter.register(fake)
    second = new_meeting(hub, link=LINK_TWO)
    fake.post_results = [TimeoutError("maybe sent")]
    drive(hub, fake)
    assert state_of(hub, second) == ("failed:no_announcement",) * 2
    assert len(fake.posts) == 0 and fake.leaves == [f"handle-{second}"]
    with store_for(hub)._tx(write=False) as conn:
        ob = conn.execute("SELECT state FROM meeting_outbox WHERE meeting_id=?", (second,))
        assert ob.fetchone()["state"] == "unknown"


def test_a_rejoin_cannot_announce_twice(console_hub, fake):
    hub = console_hub
    ready(hub)
    meeting_id = new_meeting(hub)
    fake.script = [adapter.Event("meeting_ended", "e1")]
    drive(hub, fake)
    meetings = store_for(hub)
    assert meetings.outbox_begin(meeting_id, "announce", "announce")[1] == "sent"
    assert meetings.outbox_state(meeting_id, "announce", "announce") == "sent"


# --- lobby ------------------------------------------------------------------------------------


def test_the_lobby_is_respected_and_waited_for_only_so_long(console_hub, fake):
    hub = console_hub
    ready(hub)
    admitted = new_meeting(hub)
    fake.join_result = adapter.Lobby("lobby-handle")
    fake.script = [
        adapter.Event("tick"),
        adapter.Event("admitted", "e1"),
        adapter.Event("meeting_ended", "e2"),
    ]
    drive(hub, fake)
    assert state_of(hub, admitted) == ("done", "done")
    assert "lobby" in store_for(hub).events_for(admitted)
    assert fake.log.index("join") < fake.log.index("post")  # joined, was admitted, then announced

    fake.__init__()
    adapter.register(fake)
    refused = new_meeting(hub, link=LINK_TWO)
    fake.join_result = adapter.Lobby("lobby-handle")
    fake.script = [adapter.Event("lobby_timeout", "e1")]
    _, sink = drive(hub, fake)
    assert state_of(hub, refused) == ("failed:not_admitted",) * 2
    assert fake.posts == [] and sink.frames == []

    fake.__init__()
    adapter.register(fake)
    clock = Clock()
    waited = new_meeting(hub, link="https://meet.example.test/m/lobby-wait-1")
    fake.join_result = adapter.Lobby("lobby-handle")
    fake.script = [adapter.Event("tick"), lambda: clock.advance(601), adapter.Event("tick")]
    drive(hub, fake, clock=clock)
    assert state_of(hub, waited) == ("failed:not_admitted",) * 2
    assert fake.posts == []


def test_a_join_that_fails_is_retried_once_and_a_rejection_is_final(console_hub, fake):
    hub = console_hub
    ready(hub)
    flaky = new_meeting(hub)
    fake.join_errors = 1
    fake.script = [adapter.Event("meeting_ended", "e1")]
    drive(hub, fake)
    assert state_of(hub, flaky) == ("done", "done") and len(fake.joins) == 2

    fake.__init__()
    adapter.register(fake)
    down = new_meeting(hub, link=LINK_TWO)
    fake.join_errors = 5
    drive(hub, fake)
    assert state_of(hub, down) == ("failed:provider_unavailable",) * 2
    assert fake.posts == []

    fake.__init__()
    adapter.register(fake)
    rejected = new_meeting(hub, link="https://meet.example.test/m/rejected-1")
    fake.join_result = adapter.Reject("locked")
    drive(hub, fake)
    assert state_of(hub, rejected) == ("failed:join_rejected",) * 2 and fake.leaves == []


# --- removal, stop and leave ------------------------------------------------------------------


def test_a_removed_bot_stops_capture_and_discards_everything(console_hub, fake):
    hub = console_hub
    ready(hub)
    meeting_id = new_meeting(hub)
    fake.frames = [adapter.Frame(PCM)]
    meetings = store_for(hub)

    def plant_segment():  # what a later slice would have stored by now
        with meetings._tx() as conn:
            conn.execute(
                "INSERT INTO meeting_segments VALUES (?,1,NULL,0,10,'words')", (meeting_id,)
            )

    fake.script = [
        adapter.Event("participant_joined", "e1", "p1", text="Alex"),
        adapter.Wait(),
        plant_segment,
        adapter.Event("removed", "e2"),
        adapter.Event("meeting_ended", "e3"),  # never reached
    ]
    _, sink = drive(hub, fake)
    assert state_of(hub, meeting_id) == ("discarded", "host_removed")
    assert sink.discarded == 1 and sink.finished == 0
    for table in ("meeting_segments", "meeting_participants", "meeting_notes"):
        assert count(hub, table, meeting_id) == 0
    assert len(fake.posts) == 1  # the announcement only: a removed bot cannot post a notice
    assert fake.leaves == [f"handle-{meeting_id}"]
    assert meetings.meeting(meeting_id, hub.member_user)["end_reason"] == "host_removed"


def test_anyone_typing_bot_stop_ends_capture_and_discards_with_one_notice(console_hub, fake):
    hub = console_hub
    ready(hub)
    meeting_id = new_meeting(hub)
    fake.frames = [adapter.Frame(PCM)]
    fake.script = [
        # not commands: a message that quotes the phrase, the bot's own announcement echoed back
        adapter.Event("chat", "c1", "p1", text="please bot stop recording?"),
        adapter.Event("chat", "c2", "p1", text=announce_text(hub)),
        adapter.Event("chat", "c3", "p1", text="hello"),
        adapter.Wait(),
        adapter.Event("chat", "c4", "p2", text="  Bot   STOP! "),
        adapter.Event("meeting_ended", "e1"),
    ]
    _, sink = drive(hub, fake)
    assert state_of(hub, meeting_id) == ("discarded", "stop_requested")
    assert sink.discarded == 1 and sink.finished == 0
    assert [text for text, _ in fake.posts] == [announce_text(hub), engine.NOTICES["stopped"]]
    assert fake.leaves == [f"handle-{meeting_id}"]
    for table in ("meeting_segments", "meeting_participants"):
        assert count(hub, table, meeting_id) == 0


def test_a_stop_before_the_bot_was_admitted_leaves_without_posting(console_hub, fake):
    hub = console_hub
    ready(hub)
    meeting_id = new_meeting(hub)
    fake.join_result = adapter.Lobby("lobby-handle")
    fake.script = [adapter.Event("chat", "c1", "p1", text="bot stop")]
    drive(hub, fake)
    assert state_of(hub, meeting_id) == ("discarded", "stop_requested")
    assert fake.posts == []


def test_the_inviter_can_leave_and_the_owner_can_stop_and_nobody_else(console_hub, fake):
    hub = console_hub
    ready(hub)
    cancelled = new_meeting(hub)
    for who in (hub.owner, hub.t1, None):  # not the inviter: absent, and the owner cannot read it
        assert hub.request(who, "POST", f"/api/meetings/{cancelled}/leave")[0] in (401, 404)
        assert hub.get(who, f"/api/meetings/{cancelled}")[0] in (401, 404)
    assert hub.request(hub.member, "POST", f"/api/meetings/{cancelled}/stop")[0] == 404
    status, body, _ = hub.request(hub.member, "POST", f"/api/meetings/{cancelled}/leave")
    assert (status, body["meeting"]["state"]) == (200, "cancelled")  # before it ever joined
    assert fake.joins == []

    leaving = new_meeting(hub, link=LINK_TWO)
    fake.frames = [adapter.Frame(PCM)]
    meetings = store_for(hub)

    def inviter_leaves():
        hub.request(hub.member, "POST", f"/api/meetings/{leaving}/leave")

    fake.script = [adapter.Event("tick"), adapter.Wait(), inviter_leaves, adapter.Event("tick")]
    _, sink = drive(hub, fake)
    assert state_of(hub, leaving) == ("done", "inviter_left")
    assert sink.finished == 1 and sink.discarded == 0  # leaving keeps what was captured
    assert fake.posts[-1][0] == engine.NOTICES["leaving"]
    assert meetings.outbox_state(leaving, "leave_notice", "leaving") == "sent"

    fake.__init__()
    adapter.register(fake)
    stopped = new_meeting(hub, link="https://meet.example.test/m/owner-stop-1")
    fake.frames = [adapter.Frame(PCM)]

    def owner_stops():
        status, body, _ = hub.request(hub.owner, "POST", f"/api/meetings/{stopped}/stop")
        assert (status, set(body["meeting"])) == (200, {"id", "state"})

    fake.script = [adapter.Event("tick"), adapter.Wait(), owner_stops, adapter.Event("tick")]
    _, sink = drive(hub, fake)
    assert state_of(hub, stopped) == ("discarded", "stop_requested")
    assert sink.discarded == 1 and fake.posts[-1][0] == engine.NOTICES["stopped"]
    assert hub.request(hub.member, "POST", f"/api/meetings/{stopped}/stop")[0] == 404


@pytest.mark.parametrize("how", ["owner_stop", "inviter_leave"])
def test_a_stop_or_leave_during_the_announcement_is_not_overwritten(console_hub, fake, how):
    """Consent race: the request lands while the announcement is being posted. Capture must not
    open, and a stop still discards."""
    hub = console_hub
    ready(hub)
    meeting_id = new_meeting(hub)
    fake.frames = [adapter.Frame(PCM)]
    fake.script = [adapter.Event("tick"), adapter.Wait(), adapter.Event("meeting_ended", "e1")]

    def during_post():
        if how == "owner_stop":
            assert hub.request(hub.owner, "POST", f"/api/meetings/{meeting_id}/stop")[0] == 200
        else:
            assert hub.request(hub.member, "POST", f"/api/meetings/{meeting_id}/leave")[0] == 200
        fake.on_post = None

    fake.on_post = during_post
    _, sink = drive(hub, fake)
    assert sink.frames == [] and fake.audio_opened_after_posts is None
    if how == "owner_stop":
        assert state_of(hub, meeting_id) == ("discarded", "stop_requested")
        assert sink.discarded == 1 and sink.finished == 0
        assert [t for t, _ in fake.posts][-1] == engine.NOTICES["stopped"]
    else:
        assert state_of(hub, meeting_id) == ("done", "inviter_left")
        assert sink.discarded == 0
    assert fake.leaves == [f"handle-{meeting_id}"]
    assert "live" not in store_for(hub).events_for(meeting_id)


@pytest.mark.parametrize("after", [None, "ending", "processing", "finish"])
def test_a_stop_at_any_step_of_a_normal_end_still_discards(console_hub, fake, after):
    """A stop lands just before the end is recorded (``None``), or right after the ``ending`` or
    ``processing`` step, or just before the final write: never turned into ``done``."""
    hub = console_hub
    ready(hub)
    meeting_id = new_meeting(hub)
    fake.frames = [adapter.Frame(PCM)]
    meetings = store_for(hub)
    real_advance, real_finish = meetings.advance, meetings.finish
    stop = lambda now: meetings.request_end(meeting_id, "stop_requested", now)  # noqa: E731

    def advance(mid, to, now, **fields):
        if after is None and to == "ending":
            stop(now)
        result = real_advance(mid, to, now, **fields)
        if to == after:
            stop(now)
        return result

    def finish(mid, state, reason, now, *, discard):
        if after == "finish" and not discard:
            stop(now)
        return real_finish(mid, state, reason, now, discard=discard)

    meetings.advance, meetings.finish = advance, finish
    fake.script = [adapter.Event("tick"), adapter.Wait(), adapter.Event("meeting_ended", "e1")]
    try:
        _, sink = drive(hub, fake)
    finally:
        meetings.advance, meetings.finish = real_advance, real_finish
    assert state_of(hub, meeting_id) == ("discarded", "stop_requested")
    assert sink.discarded == 1
    for table in ("meeting_segments", "meeting_participants"):
        assert count(hub, table, meeting_id) == 0


def test_a_stopped_meeting_cannot_be_finished_keeping_what_it_captured(console_hub, fake):
    hub = console_hub
    ready(hub)
    meeting_id = new_meeting(hub)
    meetings = store_for(hub)
    assert meetings.claim(meeting_id, time.time(), engine.LEASE_S)
    meetings.request_end(meeting_id, "stop_requested", time.time())
    assert meetings.finish(meeting_id, "done", "done", time.time(), discard=False) is False
    assert state_of(hub, meeting_id) == ("removed", "stop_requested")
    assert meetings.finish(meeting_id, "discarded", "stop_requested", time.time(), discard=True)
    assert state_of(hub, meeting_id) == ("discarded", "stop_requested")


def test_max_minutes_ends_a_meeting(console_hub, fake):
    hub = console_hub
    ready(hub)
    assert hub.request(hub.owner, "PUT", "/api/meetings/settings", {"max_minutes": 5})[0] == 200
    meeting_id = new_meeting(hub)
    clock = Clock()
    fake.frames = [adapter.Frame(PCM)]
    fake.script = [
        adapter.Event("tick"),
        adapter.Wait(),
        lambda: clock.advance(301),
        adapter.Event("tick"),
        adapter.Event("tick"),
    ]
    _, sink = drive(hub, fake, clock=clock)
    assert state_of(hub, meeting_id) == ("timeout", "timeout")
    assert sink.finished == 1 and fake.leaves == [f"handle-{meeting_id}"]


def test_late_joiners_are_told_at_most_once_per_ten_minutes(console_hub, fake):
    hub = console_hub
    ready(hub)
    meeting_id = new_meeting(hub)
    clock = Clock()
    fake.script = [
        adapter.Event("participant_joined", "e1", "p1", text="Alex"),  # too soon: no reminder
        lambda: clock.advance(601),
        adapter.Event("participant_joined", "e2", "p2", text="Sam"),  # reminder
        adapter.Event("participant_joined", "e3", "p3", text="Kim"),  # within 10 minutes: none
        adapter.Event("meeting_ended", "e4"),
    ]
    drive(hub, fake, clock=clock)
    assert state_of(hub, meeting_id) == ("done", "done")
    texts = [text for text, _ in fake.posts]
    assert len(texts) == 2 and texts[1].startswith("Reminder: I'm a notes bot, not a person.")


# --- the kill switches and the standing checks ------------------------------------------------


def test_disabling_the_workspace_makes_an_active_meeting_leave_and_discard(console_hub, fake):
    hub = console_hub
    ready(hub)
    meeting_id = new_meeting(hub)
    fake.frames = [adapter.Frame(PCM)]

    def kill_switch():
        assert hub.request(hub.owner, "PUT", "/api/meetings/settings", {"enabled": False})[0] == 200

    fake.script = [adapter.Event("tick"), adapter.Wait(), kill_switch, adapter.Event("tick")]
    _, sink = drive(hub, fake)
    assert state_of(hub, meeting_id) == ("cancelled", "cancelled")
    assert sink.discarded == 1 and sink.finished == 0
    assert fake.leaves == [f"handle-{meeting_id}"]
    assert count(hub, "meeting_participants", meeting_id) == 0
    assert drive(hub, fake)[0] == []  # off: the worker starts nothing, even for a new invite


@pytest.mark.parametrize("what", ["person", "connection", "membership"])
def test_losing_the_right_to_run_ends_a_meeting_at_the_next_beat(
    console_hub, fake, what, monkeypatch
):
    hub = console_hub
    connection_id = ready(hub)
    meeting_id = new_meeting(hub)

    def revoke():
        if what == "person":
            r = hub.request(hub.member, "PATCH", "/api/meetings/me", {"enabled": False})
        elif what == "connection":
            r = hub.request(
                hub.owner,
                "PATCH",
                f"/api/meetings/connections/{connection_id}",
                {"enabled": False},
            )
        else:
            real = hub.hub.db.members
            monkeypatch.setattr(
                hub.hub.db,
                "members",
                lambda ref: [m for m in real(ref) if m["id"] != hub.member_user],
            )
            return
        assert r[0] == 200

    fake.frames = [adapter.Frame(PCM)]
    fake.script = [adapter.Event("tick"), adapter.Wait(), revoke, adapter.Event("tick")]
    _, sink = drive(hub, fake)
    assert state_of(hub, meeting_id) == ("cancelled", "cancelled"), what
    assert sink.discarded == 1 and fake.leaves == [f"handle-{meeting_id}"]
    assert fake.posts[-1][0] == engine.NOTICES["stopped"]


# --- crashes ------------------------------------------------------------------------------------


def make_orphan(hub, fake, meeting_id, state, announced):
    """What a crashed worker leaves: a running state, a lapsed lease, a bot that is in the meeting."""
    fake.handles[meeting_id] = adapter.JoinHandle(f"handle-{meeting_id}")
    with store_for(hub)._tx() as conn:
        conn.execute(
            "UPDATE meetings SET state=?, lease_at=?, announced_at=? WHERE id=?",
            (state, time.time() - 600, "2026-01-01T00:00:00Z" if announced else None, meeting_id),
        )
        conn.execute(
            "INSERT INTO meeting_outbox(id, meeting_id, kind, ref, state, attempts) "
            "VALUES ('o1', ?, 'announce', 'announce', 'sending', 1)",
            (meeting_id,),
        )


@pytest.mark.parametrize("state", ["joining", "announcing", "live"])
def test_a_crash_ends_the_meeting_interrupted_and_never_rejoins(console_hub, fake, state):
    hub = console_hub
    ready(hub)
    meeting_id = new_meeting(hub)
    make_orphan(hub, fake, meeting_id, state, announced=state == "live")
    fake.script = [adapter.Event("meeting_ended", "e1")]
    started, sink = drive(hub, fake)
    assert started == []  # nothing is (re)started
    assert state_of(hub, meeting_id) == ("interrupted", "interrupted")
    assert fake.leaves == [f"handle-{meeting_id}"]  # the bot is asked to leave
    assert fake.joins == [] and fake.posts == [] and sink.frames == []
    with store_for(hub)._tx(write=False) as conn:
        assert conn.execute("SELECT join_ref FROM meetings").fetchone()["join_ref"] is None
        assert conn.execute("SELECT state FROM meeting_outbox").fetchone()["state"] == "unknown"
    assert drive(hub, fake)[0] == []  # a later beat does nothing either
    assert fake.joins == []


def test_a_worker_error_mid_meeting_fails_closed(console_hub, fake, caplog):
    hub = console_hub
    ready(hub)
    meeting_id = new_meeting(hub)
    fake.frames = [adapter.Frame(PCM)]

    def boom():
        raise RuntimeError(f"provider said {PASSCODE}")

    fake.script = [adapter.Event("tick"), adapter.Wait(), boom]
    _, sink = drive(hub, fake)
    assert state_of(hub, meeting_id) == ("failed:internal", "failed:internal")
    assert sink.discarded == 1 and fake.leaves == [f"handle-{meeting_id}"]
    assert PASSCODE not in caplog.text  # only the exception type is logged


def test_a_meeting_that_fires_twice_runs_once_and_concurrency_is_capped(console_hub, fake):
    hub = console_hub
    ready(hub)
    first, second = new_meeting(hub), new_meeting(hub, link=LINK_TWO)
    meetings = store_for(hub)
    assert meetings.claim(first, time.time(), engine.LEASE_S) is True
    assert meetings.claim(first, time.time(), engine.LEASE_S) is False  # a second fire does nothing
    assert meetings.claim(second, time.time(), engine.LEASE_S) is False  # max_concurrent is 1
    meetings.finish(first, "done", "done", time.time(), discard=False)
    assert meetings.claim(second, time.time(), engine.LEASE_S) is True
    assert engine.Run(hub.hub.gateway, hub.hub.workspace, first).run() == "done"
    assert fake.joins == []


def test_a_scheduled_meeting_waits_for_its_time(console_hub, fake):
    hub = console_hub
    ready(hub)
    when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    meeting_id = new_meeting(hub, start_at=when)
    assert drive(hub, fake)[0] == [] and state_of(hub, meeting_id)[0] == "requested"
    clock = Clock()
    clock.advance(3600 - 119)
    fake.script = [adapter.Event("meeting_ended", "e1")]
    assert drive(hub, fake, clock=clock)[0] == [meeting_id]
    assert state_of(hub, meeting_id) == ("done", "done")


# --- worker liveness ---------------------------------------------------------------------------


def test_an_invite_arriving_as_the_beat_goes_idle_is_not_stranded(console_hub, fake, monkeypatch):
    hub = console_hub
    ready(hub)
    monkeypatch.setattr(worker, "AUTOSTART", True)
    monkeypatch.setattr(worker, "BEAT_S", 0.05)
    run = worker.Worker(hub.hub.gateway)
    meetings = store_for(hub)
    started = []
    monkeypatch.setattr(run, "beat", lambda ws, inline=False: started.append(ws) or [])
    real_pending = meetings.pending
    first = {"done": False}

    def pending():  # the loop looks, finds nothing; then an invite commits and kicks
        answer = real_pending()
        if not first["done"]:
            first["done"] = True
            new_meeting(hub)
            assert run.kick(hub.hub.workspace) is False  # the loop still counts as running
        return answer

    monkeypatch.setattr(meetings, "pending", pending)
    assert run.kick(hub.hub.workspace) is True
    deadline = time.time() + 5
    while time.time() < deadline and len(started) < 2:
        time.sleep(0.02)
    assert len(started) >= 2  # the loop kept beating for the new meeting


def test_capped_meetings_do_not_spawn_a_thread_per_beat(console_hub, fake):
    hub = console_hub
    ready(hub)
    first, second = new_meeting(hub), new_meeting(hub, link=LINK_TWO)
    assert store_for(hub).claim(first, time.time(), engine.LEASE_S)  # max_concurrent is 1
    run = worker.Worker(hub.hub.gateway)
    assert run.beat(hub.hub.workspace) == [] and run._runs == {}
    assert state_of(hub, second)[0] == "requested"


def test_the_lease_is_renewed_while_the_provider_is_slow(console_hub, fake, monkeypatch):
    hub = console_hub
    ready(hub)
    monkeypatch.setattr(engine, "LEASE_S", 0.3)
    meeting_id = new_meeting(hub)
    seen = {}

    def slow_join():
        seen["claimed"] = store_for(hub).run_row(meeting_id)["lease_at"]
        time.sleep(1.0)
        seen["after"] = store_for(hub).run_row(meeting_id)["lease_at"]

    fake.join_hook = slow_join
    fake.script = [adapter.Event("meeting_ended", "e1")]
    drive(hub, fake)
    assert seen["after"] - seen["claimed"] > 0.5  # beaten during the call, not only after it
    assert state_of(hub, meeting_id) == ("done", "done")


# --- privacy: access, audio on disk, logs ------------------------------------------------------


def test_only_the_inviter_reads_a_meeting_and_the_owner_sees_counts(console_hub, fake, caplog):
    hub = console_hub
    ready(hub)
    caplog.set_level(logging.DEBUG)
    meeting_id = new_meeting(hub)
    fake.frames = [adapter.Frame(PCM)]
    fake.script = [
        adapter.Event("participant_joined", "e1", "p1", text="Alex"),
        adapter.Wait(),
        adapter.Event("meeting_ended", "e2"),
    ]
    drive(hub, fake)
    status, body, raw = hub.get(hub.member, f"/api/meetings/{meeting_id}")
    assert status == 200 and body["meeting"]["state"] == "done"
    assert PASSCODE.encode() not in raw
    for who in (hub.owner, hub.t1, hub.tm, None):
        assert hub.get(who, f"/api/meetings/{meeting_id}")[0] in (401, 404), who
    assert hub.get(hub.other, f"/api/meetings/{meeting_id}")[0] in (404, 409)  # another workspace
    assert hub.get(hub.owner, "/api/meetings")[1] == {"meetings": []}  # the owner's own list
    overview = hub.get(hub.owner, "/api/meetings/overview")[1]["overview"]
    assert overview["states"] == {"done": 1}
    assert "Weekly sync" not in repr(overview)
    token = hub.hub.token(
        "status", user=hub.member_user, profile="full", scopes=["events:read"], trust="standard"
    )
    ok, text = mcp(hub.hub, token, "meeting_status")
    assert ok and meeting_id in text and "Weekly sync" not in text and PASSCODE not in text
    # no audio-like file under the data directory, no sentinel pcm or link anywhere
    for path in Path(hub.hub.supervisor.root.parent).rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert PCM not in data and PASSCODE.encode() not in data, path
    assert PCM.decode() not in caplog.text and PASSCODE not in caplog.text
    assert "Alex" not in caplog.text


def test_every_end_is_audited_without_text(console_hub, fake):
    hub = console_hub
    ready(hub)
    meeting_id = new_meeting(hub)
    fake.script = [adapter.Event("chat", "c1", "p1", text="bot stop")]
    drive(hub, fake)
    rows = [r for r in hub.hub.db.audit_entries(limit=100) if r["action"] == "meetings.end"]
    assert len(rows) == 1 and "stop_requested" in repr(dict(rows[0]))
    assert meeting_id in repr(dict(rows[0])) and PASSCODE not in repr(dict(rows[0]))


def test_the_display_name_is_sanitized_and_only_an_exact_stop_counts():
    assert engine.bot_name("Sam <b>Lee</b> `x`\nQ") == "Notes bot for Sam bLee/b x Q"
    assert engine.bot_name("") == "Notes bot for a team member"
    assert len(engine.display_label("x" * 200)) == 40
    assert engine.is_stop("Bot stop") and engine.is_stop(" bot   stop!! ")
    assert not engine.is_stop("bot stop please") and not engine.is_stop(None)
    assert not engine.is_stop("stop") and not engine.is_stop(ANNOUNCEMENT)
    assert store.is_ended("failed:x") and store.is_ended("done") and not store.is_ended("live")
