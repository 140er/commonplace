"""Meeting-join bots, capture: frames become transcript segments on the local engine, in memory.

A stub engine stands in for the hub's transcription engine (the real one runs only in the opt-in
voice tests), the audio is a generated PCM fixture (a short 220 Hz tone, repeated with pauses), and
meetings run over the in-memory fake provider: no network, no audio device, no platform.
"""

import ast
import inspect
import logging
import threading
import time
from pathlib import Path

import pytest

from commonplace import voice
from commonplace.meetings import adapter, capture, store, worker
from tests._console_helpers import console_hub_fixture  # noqa: F401
from tests.test_meetings_foundation import (  # noqa: F401
    LocalEngine,
    data_files,
    enable,
    keys_and_providers,
    store_for,
)
from tests.test_meetings_lifecycle import (  # noqa: F401
    Clock,
    count,
    drive,
    inline_worker,
    invite,
    new_meeting,
    ready,
    state_of,
)
from tests.test_meetings_notes import fake_adapter_fixture, model_fixture  # noqa: F401

FIXTURE = Path(__file__).parent / "fixtures" / "meetings" / "tone-220hz-0.5s.pcm"
TONE = FIXTURE.read_bytes()
PAUSE = b"\0" * len(TONE)
BPS = capture.BPS
SPOKEN = "sentinel-spoken-words-b83f"


def stream(seconds):
    """``seconds`` of 16 kHz mono audio: the generated tone, then a pause, over and over."""
    return (TONE + PAUSE) * seconds


def frames(pcm, who=None, size=BPS):
    return [adapter.Frame(pcm[i : i + size], who) for i in range(0, len(pcm), size)]


def live_meeting(hub, meeting_id="m1", state="live", user=None):
    """A meeting row that is listening, for tests of the capture on its own."""
    with store_for(hub)._tx() as conn:
        conn.execute(
            "INSERT INTO meetings(id,user_id,connection_id,provider,title,bot_name,state,"
            "attested_at,request_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                meeting_id,
                user or hub.member_user,
                "c",
                "fake",
                "t",
                "b",
                state,
                "x",
                meeting_id,
                "x",
            ),
        )
    return meeting_id


def sink_for(hub, meeting_id="m1", clock=None, threaded=False):
    return capture.CaptureSink(
        hub.hub.gateway,
        hub.hub.workspace,
        meeting_id,
        clock=clock or time.time,
        threaded=threaded,
    )


def rows(hub, meeting_id="m1"):
    return store_for(hub).segments(meeting_id)


def installed(hub, **kwargs):
    voice_engine = hub.hub.gateway.voice_engine = LocalEngine(**kwargs)
    return voice_engine


# --- the segmenter and the seam -------------------------------------------------------------


def test_windows_are_short_overlap_and_are_cut_at_a_pause():
    seg = capture.Segmenter()
    out = seg.feed(stream(25))
    assert len(out) == 1
    first = out[0]
    assert len(first.pcm) <= capture.WINDOW_S * BPS  # well below the engine's 60 s call limit
    assert first.start_ms == 0 and capture.WINDOW_S - 4 <= first.end_ms / 1000 <= capture.WINDOW_S
    assert first.end_ms % 1000 >= 500  # inside a pause, not in the middle of a tone
    tail = seg.flush()
    assert tail.start_ms == first.end_ms - capture.OVERLAP_S * 1000  # no word lost at the seam
    assert tail.end_ms == 25000
    assert seg.flush() is None


def test_continuous_speech_is_cut_at_the_window_and_odd_frames_stay_aligned():
    loud = TONE * 70  # 35 s, no pause anywhere
    whole = capture.Segmenter().feed(loud)
    odd = capture.Segmenter()
    pieces = [odd.feed(loud[i : i + 4999]) for i in range(0, len(loud), 4999)]
    got = [w for piece in pieces for w in piece]
    assert [(w.start_ms, w.end_ms) for w in whole] == [(w.start_ms, w.end_ms) for w in got]
    assert whole[0].end_ms == capture.WINDOW_S * 1000
    assert len(whole[0].pcm) % 2 == 0


def test_a_very_short_tail_is_not_transcribed():
    seg = capture.Segmenter()
    seg.feed(TONE[: capture.BPS // 4])
    assert seg.flush() is None


def test_the_overlap_is_stitched_out_and_a_seam_the_engine_heard_differently_keeps_all():
    assert capture.stitch("we ship the beta on", "beta on Friday. Then") == "Friday. Then"
    assert capture.stitch("ship it", "Ship, it!") == ""
    assert capture.stitch("one two three", "four five") == "four five"
    assert capture.stitch("", "hello there") == "hello there"
    assert capture.stitch("one two", "...") == "..."


# --- per-speaker and mixed streams ----------------------------------------------------------


def test_segments_carry_speaker_ids_only_when_the_stream_does(console_hub):
    hub = console_hub
    ready(hub)
    installed(hub)
    live_meeting(hub, "per")
    live_meeting(hub, "mix")
    each = sink_for(hub, "per")
    for who in ("p1", "p2"):
        for frame in frames(stream(25), who):
            each.frame("per", frame)
    each.finish("per")
    assert {r["ext_participant"] for r in rows(hub, "per")} == {"p1", "p2"}
    assert len(rows(hub, "per")) == 4  # two windows per speaker, each its own stream
    mixed = sink_for(hub, "mix")
    for frame in frames(stream(25)):
        mixed.frame("mix", frame)
    mixed.finish("mix")
    assert rows(hub, "mix") and {r["ext_participant"] for r in rows(hub, "mix")} == {None}


def test_the_engine_strips_a_speaker_id_a_mixed_stream_has_no_right_to(
    console_hub, fake_adapter, model
):
    hub = console_hub
    ready(hub)
    voice_engine = installed(hub)
    model.reply = {"items": []}
    fake_adapter.caps = adapter.Caps(chat_send=True, chat_receive=True, per_speaker_audio=False)
    fake_adapter.frames = frames(stream(25), "p1")
    fake_adapter.script = [adapter.Event("participant_joined", "e1", "p1", text="Alex")]
    fake_adapter.script += [adapter.Wait(), adapter.Event("meeting_ended", "e2")]
    seen = []
    original = store.MeetingStore.add_segment

    def spy(self, meeting_id, ext, *rest):
        seen.append(ext)
        return original(self, meeting_id, ext, *rest)

    store.MeetingStore.add_segment = spy
    try:
        meeting_id = new_meeting(hub, keep_transcript=False)
        worker.Worker(hub.hub.gateway).beat(hub.hub.workspace, inline=True)
    finally:
        store.MeetingStore.add_segment = original
    assert state_of(hub, meeting_id)[0] == "done"
    assert voice_engine.calls and seen and set(seen) == {None}


# --- gaps, delay and the budget -------------------------------------------------------------


def test_a_delayed_engine_records_a_gap_not_silence(console_hub):
    hub = console_hub
    ready(hub)
    voice_engine = installed(hub)
    live_meeting(hub)
    clock = Clock()
    sink = sink_for(hub, clock=clock)
    for frame in frames(stream(25)):
        sink.frame("m1", frame)
    clock.advance(90)  # the engine never got to it: more than a minute behind real time
    assert sink.drain() == 1
    [gap] = rows(hub)
    assert gap["text"] == capture.GAP and gap["ext_participant"] is None
    assert (gap["t_start_ms"], gap["t_end_ms"] // 1000) == (0, 16)
    assert voice_engine.calls == []  # the audio was dropped, not transcribed late
    sink.finish("m1")  # the tail is behind too: the same gap goes on, it is not a second one
    [gap] = rows(hub)
    assert gap["t_end_ms"] == 25000
    assert store_for(hub).events_for("m1") == [capture.DELAYED]  # one notice for the inviter


def test_the_transcript_recovers_after_a_gap_and_a_stall_is_one_gap_segment(console_hub):
    hub = console_hub
    ready(hub)
    voice_engine = installed(hub)
    live_meeting(hub)
    clock = Clock()
    sink = sink_for(hub, clock=clock)
    for frame in frames(stream(70)):  # three windows queued while the engine is stuck
        sink.frame("m1", frame)
    clock.advance(100)
    sink.drain()  # the two oldest are past a minute: gaps; the newest is still in time
    sink.finish("m1")
    texts = [r["text"] for r in rows(hub)]
    assert texts[0] == capture.GAP and capture.GAP not in texts[1:]
    assert rows(hub)[0]["t_end_ms"] > 30000  # a stall of two windows is one gap segment
    assert len(voice_engine.calls) == len(texts) - 1  # and everything after it was said
    assert [r["seq"] for r in rows(hub)] == list(range(1, len(texts) + 1))


def test_a_short_meeting_is_transcribed_on_the_threaded_path(console_hub):
    hub = console_hub
    ready(hub)
    voice_engine = installed(hub)
    live_meeting(hub)
    sink = sink_for(hub, threaded=True)  # what production runs
    for frame in frames(stream(10)):  # under one window: no thread was started by a frame
        sink.frame("m1", frame)
    sink.finish("m1")
    [only] = rows(hub)
    assert only["text"].startswith("window") and only["t_end_ms"] == 10000
    assert len(voice_engine.calls) == 1


def test_a_failure_outside_the_engine_call_is_a_failed_window_not_a_dead_thread(console_hub):
    hub = console_hub
    ready(hub)
    installed(hub)
    live_meeting(hub)
    sink = sink_for(hub, threaded=True)

    def boom():
        raise RuntimeError(f"{SPOKEN} from the store")

    sink.meetings.settings = boom  # read before the engine is called
    for frame in frames(stream(70)):
        sink.frame("m1", frame)
    sink.finish("m1")
    assert {r["text"] for r in rows(hub)} == {capture.GAP}
    assert sink.broken is True  # counted toward the three-in-a-row rule
    assert sink._thread is not None and not sink._thread.is_alive()  # ended, not died mid-queue


def test_a_window_the_engine_fails_on_is_a_gap_and_a_broken_engine_ends_the_meeting(console_hub):
    hub = console_hub
    ready(hub)
    voice_engine = installed(hub)
    voice_engine.fail = RuntimeError(f"{SPOKEN} leaked into an error")
    live_meeting(hub)
    sink = sink_for(hub)
    for frame in frames(stream(70)):
        sink.frame("m1", frame)
    sink.drain()
    assert {r["text"] for r in rows(hub)} == {capture.GAP}
    assert sink.broken is True  # the run ends the meeting as failed:transcription (below)


def test_a_broken_engine_ends_the_meeting_and_keeps_nothing(console_hub, fake_adapter, model):
    hub = console_hub
    ready(hub)
    meeting_id = new_meeting(hub)
    sink = sink_for(hub, meeting_id)
    sink.broken = True
    fake_adapter.script = [adapter.Event("participant_joined", "e1", "p1", text="Alex")]
    fake_adapter.script += [adapter.Event("participant_joined", "e2", "p2", text="Blair")]
    run = worker.Worker(hub.hub.gateway, sink_factory=lambda w, m: sink)
    run.beat(hub.hub.workspace, inline=True)
    assert state_of(hub, meeting_id) == ("failed:transcription", "failed:transcription")
    assert rows(hub, meeting_id) == [] and model.calls == []


def test_a_gap_after_the_meeting_ended_is_not_written_back(console_hub):
    hub = console_hub
    ready(hub)
    installed(hub)
    live_meeting(hub)
    sink = sink_for(hub)
    for frame in frames(stream(25)):
        sink.frame("m1", frame)
    sink.discard("m1")  # a stop: everything held is dropped
    assert sink.drain() == 0
    assert store_for(hub).add_segment("m1", None, 0, 1, "late") == 1  # live: it would be taken
    with store_for(hub)._tx() as conn:
        conn.execute("UPDATE meetings SET state='discarded' WHERE id='m1'")
    assert store_for(hub).add_segment("m1", None, 0, 1, "late") is None  # ended: refused


def test_meetings_use_at_most_max_concurrent_engines_at_once(console_hub):
    hub = console_hub
    ready(hub)
    voice_engine = installed(hub)
    voice_engine.gate = threading.Event()
    live_meeting(hub, "a")
    live_meeting(hub, "b")
    sinks = [sink_for(hub, name, threaded=True) for name in ("a", "b")]
    for sink, name in zip(sinks, ("a", "b")):
        for frame in frames(stream(25)):
            sink.frame(name, frame)
    deadline = time.time() + 5
    while not voice_engine.calls and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.3)  # the second meeting had every chance to start
    assert voice_engine.active == 1 and len(voice_engine.calls) == 1  # max_concurrent is 1
    voice_engine.gate.set()
    for sink, name in zip(sinks, ("a", "b")):
        sink.finish(name)
    assert voice_engine.peak == 1 and len(voice_engine.calls) == 4
    assert rows(hub, "a") and rows(hub, "b")


def test_meetings_wait_for_dictation_between_windows(console_hub):
    hub = console_hub
    ready(hub)
    voice_engine = installed(hub)
    live_meeting(hub)
    sink = sink_for(hub)
    for frame in frames(stream(25)):
        sink.frame("m1", frame)
    done = threading.Thread(target=sink.drain)
    with voice.dictating():  # a dictation request is in flight: it has the engine
        done.start()
        time.sleep(0.5)
        assert voice_engine.calls == [] and done.is_alive()
    done.join(5)
    assert not done.is_alive() and len(voice_engine.calls) == 1
    assert rows(hub)


# --- audio never touches disk ---------------------------------------------------------------


def test_the_capture_has_no_way_to_write_audio_to_disk():
    tree = ast.parse(inspect.getsource(capture))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not imported & {
        "os",
        "io",
        "pathlib",
        "tempfile",
        "shutil",
        "subprocess",
        "socket",
        "mmap",
    }
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and hasattr(node.func, "id")
    }
    assert not calls & {"open", "exec", "eval", "__import__"}


def test_audio_never_touches_disk_or_the_logs(console_hub, fake_adapter, model, tmp_path, caplog):
    hub = console_hub
    ready(hub)
    marker = bytes(range(1, 255)) * 2  # a pattern no tone or pause fixture contains
    voice_engine = installed(hub, text=lambda n: f"{SPOKEN} {n}")
    voice_engine.fail = None
    model.reply = {"items": []}
    caplog.set_level(logging.DEBUG)
    fake_adapter.frames = frames((marker + stream(1)) * 14 + stream(12))
    fake_adapter.script = [adapter.Event("participant_joined", "e1", "p1", text="Alex")]
    fake_adapter.script += [adapter.Wait(), adapter.Event("meeting_ended", "e2")]
    meeting_id = new_meeting(hub, keep_transcript=False)
    before = {p for p in Path(tmp_path).rglob("*")}
    worker.Worker(hub.hub.gateway).beat(hub.hub.workspace, inline=True)
    assert state_of(hub, meeting_id)[0] == "done" and voice_engine.calls
    roots = {Path(tmp_path), data_files(hub)[0].parent}
    for root in roots:
        for path in root.rglob("*"):
            if path.is_file():
                assert path.suffix not in (".pcm", ".wav", ".raw", ".opus", ".mp3"), path
                assert marker not in path.read_bytes(), path
    assert not {p for p in Path(tmp_path).rglob("*.pcm")} - before
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert SPOKEN not in text and marker.decode("latin-1") not in text


# --- retention ------------------------------------------------------------------------------

REPLY = lambda data: {  # noqa: E731
    "items": [
        {
            "kind": "summary",
            "text": "They talked.",
            "evidence": [[data["segments"][0]["seq"], data["segments"][-1]["seq"]]],
        }
    ]
}


def captured(hub, fake, **extra):
    fake.frames = frames(stream(25), "p1")
    fake.script = [adapter.Event("participant_joined", "e1", "p1", text="Alex")]
    fake.script += [adapter.Wait(), adapter.Event("meeting_ended", "e2")]
    fake.caps = adapter.Caps(chat_send=True, chat_receive=True, per_speaker_audio=True)
    meeting_id = new_meeting(hub, **extra)
    worker.Worker(hub.hub.gateway).beat(hub.hub.workspace, inline=True)
    return meeting_id


def test_segments_are_deleted_after_notes_unless_kept(console_hub, fake_adapter, model):
    hub = console_hub
    ready(hub)
    installed(hub)
    model.reply = REPLY
    meeting_id = captured(hub, fake_adapter)
    assert state_of(hub, meeting_id)[0] == "done"
    assert model.calls  # the notes were made from the captured segments
    assert count(hub, "meeting_notes", meeting_id) == 1
    assert count(hub, "meeting_segments", meeting_id) == 0  # and the working segments are gone
    assert hub.get(hub.member, f"/api/meetings/{meeting_id}/transcript")[0] == 404


def test_a_transcript_is_stored_only_when_owner_and_inviter_both_allowed_it(
    console_hub, fake_adapter, model
):
    hub = console_hub
    ready(hub)
    installed(hub)
    model.reply = REPLY
    # the inviter's tick is refused while the owner has not allowed transcripts
    assert hub.request(hub.member, "PATCH", "/api/meetings/me", {"keep_transcript": True})[0] == 409
    owner_only = captured(hub, fake_adapter)  # the owner's allowance, no tick: nothing is kept
    assert enable(hub, transcript_ok=True)["settings"]["transcript_ok"] is True
    ticked_off = captured(hub, fake_adapter, link="https://meet.example.test/m/room-2222")
    assert count(hub, "meeting_segments", owner_only) == 0
    assert count(hub, "meeting_segments", ticked_off) == 0
    both = captured(
        hub, fake_adapter, link="https://meet.example.test/m/room-3333", keep_transcript=True
    )
    assert count(hub, "meeting_segments", both) > 0
    status, body, _ = hub.get(hub.member, f"/api/meetings/{both}/transcript")
    assert status == 200 and body["kept"] is True
    assert {s["speaker"] for s in body["segments"]} == {"Alex"}
    assert all(s["text"].startswith("window") for s in body["segments"])
    # the owner taking the allowance back stops the transcript being served and lets the sweep go
    assert enable(hub, transcript_ok=False)["settings"]["transcript_ok"] is False
    assert hub.get(hub.member, f"/api/meetings/{both}/transcript")[0] == 404
    assert store_for(hub).sweep(time.time() + 25 * 3600) > 0
    assert count(hub, "meeting_segments", both) == 0


def test_a_kept_transcript_can_be_deleted_alone_and_the_sweep_is_the_backstop(
    console_hub, fake_adapter, model
):
    hub = console_hub
    ready(hub)
    installed(hub)
    enable(hub, transcript_ok=True)
    model.reply = REPLY
    kept = captured(hub, fake_adapter, keep_transcript=True)
    assert count(hub, "meeting_segments", kept) > 0
    assert hub.request(hub.member, "DELETE", f"/api/meetings/{kept}/transcript")[0] == 200
    assert count(hub, "meeting_segments", kept) == 0 and count(hub, "meeting_notes", kept) == 1
    # notes that could not be made leave the working segments for the day's sweep, then they go
    model.resolved = "no_model"
    waiting = captured(hub, fake_adapter, link="https://meet.example.test/m/room-4444")
    assert count(hub, "meeting_segments", waiting) > 0
    assert store_for(hub).sweep(time.time() + 3600) == 0
    assert store_for(hub).sweep(time.time() + 25 * 3600) > 0
    assert count(hub, "meeting_segments", waiting) == 0


def test_a_stop_discards_the_captured_segments_for_real(console_hub, fake_adapter, model):
    hub = console_hub
    ready(hub)
    installed(hub)
    model.reply = REPLY
    fake_adapter.frames = frames(stream(25), "p1")
    fake_adapter.script = [adapter.Wait(), adapter.Event("chat", "c1", "p9", text="bot stop")]
    fake_adapter.caps = adapter.Caps(chat_send=True, chat_receive=True, per_speaker_audio=True)
    meeting_id = new_meeting(hub, keep_transcript=False)
    worker.Worker(hub.hub.gateway).beat(hub.hub.workspace, inline=True)
    assert state_of(hub, meeting_id) == ("discarded", "stop_requested")
    assert count(hub, "meeting_segments", meeting_id) == 0 and model.calls == []


# --- needs_local_transcription --------------------------------------------------------------


@pytest.mark.parametrize("why", ["unavailable", "off", "missing"])
def test_without_local_transcription_a_meeting_cannot_be_invited(console_hub, fake_adapter, why):
    hub = console_hub
    ready(hub)
    voice_engine = installed(hub)
    if why == "unavailable":
        voice_engine.available = staticmethod(lambda: False)
    elif why == "off":
        hub.hub.gateway.voice_config = voice.load_config({"COMMONPLACE_VOICE": "off"})
    else:
        hub.hub.gateway.voice_engine = None
    assert capture.engine_for(hub.hub.gateway) is None
    status, body, raw = invite(hub)
    assert status == 409 and body["code"] == "needs_local_transcription"
    assert store_for(hub).meetings_for(hub.member_user) == []
    assert fake_adapter.joins == []
    assert not [t for t in threading.enumerate() if t.name.startswith("meeting-")]


def test_a_meeting_that_lost_its_engine_ends_before_the_bot_joins(
    console_hub, fake_adapter, monkeypatch
):
    hub = console_hub
    ready(hub)
    installed(hub)
    meeting_id = new_meeting(hub)
    hub.hub.gateway.voice_engine = None  # the engine went away between the invite and the start
    worker.Worker(hub.hub.gateway).beat(hub.hub.workspace, inline=True)
    assert state_of(hub, meeting_id) == (
        "failed:needs_local_transcription",
        "failed:needs_local_transcription",
    )
    assert fake_adapter.joins == [] and fake_adapter.posts == []  # it never joined or announced


# --- clean workspace ------------------------------------------------------------------------


def test_a_clean_workspace_starts_no_capture_and_creates_nothing(console_hub, monkeypatch):
    hub = console_hub
    monkeypatch.setattr(worker, "AUTOSTART", True)
    voice_engine = installed(hub)
    gateway = hub.hub.gateway
    assert worker.Worker(gateway).beat(hub.hub.workspace, inline=True) == []
    worker.resume(gateway)
    assert invite(hub)[0] == 404 and invite(hub, who=hub.owner)[0] == 409
    assert data_files(hub) == []
    assert not [t for t in threading.enumerate() if t.name.startswith("meeting-")]
    assert voice_engine.calls == []
    # switched on, still nothing: no meeting, no worker, no capture, no engine call
    enable(hub)
    assert worker.Worker(gateway).beat(hub.hub.workspace, inline=True) == []
    assert not [t for t in threading.enumerate() if t.name.startswith("meeting-")]
    assert voice_engine.calls == []


def test_a_delayed_transcript_shows_on_the_inviters_meeting_page_only(console_hub):
    hub = console_hub
    ready(hub)
    installed(hub)
    live_meeting(hub)
    status, body, _ = hub.get(hub.member, "/api/meetings/m1".replace("m1", "0" * 32))
    assert status == 404
    store_for(hub).note_event("m1", capture.DELAYED, time.time())
    with store_for(hub)._tx() as conn:
        conn.execute("UPDATE meetings SET id=? WHERE id='m1'", ("1" * 32,))
        conn.execute("UPDATE meeting_events SET meeting_id=? WHERE meeting_id='m1'", ("1" * 32,))
    status, body, _ = hub.get(hub.member, f"/api/meetings/{'1' * 32}")
    assert status == 200 and body["meeting"]["notices"] == [capture.DELAYED]
    assert hub.get(hub.owner, f"/api/meetings/{'1' * 32}")[0] == 404  # not the owner's to read
