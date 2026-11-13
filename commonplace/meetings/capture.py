"""Capture: audio frames in, transcript segments out, on the hub's own transcription engine.

The design is ``docs/design/meeting-join-bots.md`` (section 7). A :class:`CaptureSink` is the
``engine.Sink`` of one meeting. Frames arrive from the adapter only after the announcement was
delivered (the capture gate), are cut into windows by one :class:`Segmenter` per stream and are
transcribed one window at a time by the engine ``voice.py`` already runs for dictation.

* **Audio lives in memory only.** There is no code path here that opens a file, a socket or a
  subprocess; windows are dropped as soon as they are transcribed, on discard, on finish and when
  the process dies. Neither audio nor text is ever logged: only error types.
* **Per-speaker and mixed streams.** A stream is keyed by the speaker id the frames carry. A segment
  carries that id and nothing is ever derived from a voice: a mixed stream has no id and its
  segments have none.
* **A delay is a gap, never silence.** A window that cannot be transcribed within ``MAX_LAG_S`` of
  real time (the engine is busy with dictation or other meetings), or that the engine failed on,
  is dropped and recorded as a gap segment (text ``(gap)``), so notes can say words are missing
  instead of inventing them. Real silence (an empty transcription) writes nothing.
* **Behind dictation.** Meetings take at most ``max_concurrent`` engine slots per workspace and
  wait for any dictation request in flight before each window (``voice.wait_for_dictation``).
* **Segments are working data.** They are written only while the meeting is live or ending (the
  store refuses later writes), deleted after the notes unless the transcript is kept, and by the
  sweep otherwise (``notes_store``).

The engine here is ``gateway.voice_engine``; with voice off or the extra missing there is no
transcription (:func:`engine_for` is None) and a meeting cannot be invited or started
(``needs_local_transcription``): there is no fallback to a service.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from array import array
from collections import deque
from dataclasses import dataclass
from typing import Optional

from commonplace import voice
from commonplace.meetings import engine, store

logger = logging.getLogger("commonplace.meetings")

RATE = 16000
BPS = RATE * 2  # bytes per second of 16 kHz mono int16 PCM
WINDOW_S = 20  # a window, well below the engine's 60 s call limit
OVERLAP_S = 2  # the next window starts this far before the cut, so no word is lost at the seam
SILENCE_SEARCH_S = 4  # the cut is moved to the quietest block in the last seconds of a window
BLOCK_MS = 100
SILENCE_RMS = 500  # a block quieter than this (int16 units) is a good place to cut
MIN_TAIL_S = 0.5  # shorter fresh audio at the end of a stream is not worth a transcription
MAX_LAG_S = 60  # further behind real time than this, audio is dropped with a recorded gap
FINISH_S = 120  # the longest ``finish`` waits for the queue before recording the rest as a gap
MAX_STREAMS = 64
MAX_ID = 200
TEXT_MAX = 4000
FAIL_LIMIT = 3  # consecutive engine errors after which the meeting is ended as failed
OVERLAP_WORDS = 14
GAP = "(gap)"
DELAYED = "transcript_delayed"


def engine_for(gateway):
    """The local engine meetings may use, or None (``needs_local_transcription``): the hub's voice
    engine, when voice is on for this instance and its models are installed. Never a service."""
    config = getattr(gateway, "voice_config", None)
    voice_engine = getattr(gateway, "voice_engine", None)
    if config is None or voice_engine is None or not config.enabled:
        return None
    try:
        return voice_engine if voice_engine.available() else None
    except Exception:
        return None


def _rms(block: bytes) -> float:
    samples = array("h")
    samples.frombytes(block)
    if sys.byteorder == "big":
        samples.byteswap()
    return (sum(s * s for s in samples) / max(1, len(samples))) ** 0.5


@dataclass(frozen=True)
class Window:
    start_ms: int
    end_ms: int
    pcm: bytes


class Segmenter:
    """One stream's audio into overlapping windows. Pure: bytes in, windows out, no clock."""

    def __init__(self, start_ms=0):
        self._buf = bytearray()
        self._odd = b""  # a frame may end mid-sample: the odd byte waits for the next frame
        self._base = start_ms * RATE // 1000  # sample index of the first buffered sample
        self._covered = 0  # bytes at the front of the buffer a window already carried

    def feed(self, pcm: bytes) -> list[Window]:
        data = self._odd + pcm
        self._odd = data[-1:] if len(data) % 2 else b""
        self._buf += data[: len(data) - len(data) % 2]
        out = []
        while len(self._buf) >= WINDOW_S * BPS:
            cut = self._cut()
            out.append(self._window(cut))
            keep = cut - OVERLAP_S * BPS  # the next window starts before the cut: no word is lost
            del self._buf[:keep]
            self._base += keep // 2
            self._covered = OVERLAP_S * BPS
        return out

    def _cut(self) -> int:
        """The end of the next window: the quietest block in its last seconds when one is quiet
        enough (a pause between words), else the full window."""
        end = WINDOW_S * BPS
        block = BLOCK_MS * BPS // 1000
        best, at = None, end
        for start in range(end - SILENCE_SEARCH_S * BPS, end - block + 1, block):
            level = _rms(bytes(self._buf[start : start + block]))
            if level < SILENCE_RMS and (best is None or level < best):
                best, at = level, start + block // 2
        return at - at % 2

    def _window(self, cut: int) -> Window:
        return Window(
            self._base * 1000 // RATE,
            (self._base + cut // 2) * 1000 // RATE,
            bytes(self._buf[:cut]),
        )

    def flush(self) -> Optional[Window]:
        """What is left at the end of a stream, or None when there is too little new audio."""
        fresh = len(self._buf) - self._covered
        window = self._window(len(self._buf)) if fresh >= MIN_TAIL_S * BPS else None
        self._buf.clear()
        self._covered = 0
        return window


def stitch(previous: str, text: str) -> str:
    """``text`` without the words the overlap repeated from the end of ``previous``. A seam the
    engine heard differently keeps everything: a repeated word is better than a lost one."""
    before, after = previous.split(), text.split()

    def norm(words):
        return ["".join(c for c in w.lower() if c.isalnum()) for w in words]

    for size in range(min(OVERLAP_WORDS, len(before), len(after)), 0, -1):
        if norm(before[-size:]) == norm(after[:size]) and any(norm(after[:size])):
            return " ".join(after[size:])
    return " ".join(after)


class Slots:
    """How many engines meetings use at once, per workspace. Dictation is not counted here: it has
    priority by being waited for (``voice.wait_for_dictation``), not by taking a slot."""

    def __init__(self):
        self._cv = threading.Condition()
        self._used: dict[str, int] = {}

    def acquire(self, key, limit, give_up) -> bool:
        with self._cv:
            while self._used.get(key, 0) >= max(1, limit):
                if give_up():
                    return False
                self._cv.wait(0.25)
            self._used[key] = self._used.get(key, 0) + 1
            return True

    def release(self, key):
        with self._cv:
            self._used[key] = max(0, self._used.get(key, 0) - 1)
            self._cv.notify_all()


SLOTS = Slots()


class _Stream:
    def __init__(self, start_ms, muted):
        self.segmenter = Segmenter(start_ms)
        self.muted = muted
        self.text = ""  # the last words written, for the seam
        self.end_ms = start_ms  # where this stream's last segment ended
        self.gap: Optional[tuple[int, int]] = None  # the open gap's (seq, end_ms)


class CaptureSink(engine.Sink):
    """The capture of one meeting. ``threaded=False`` leaves the transcription to explicit
    :meth:`drain` calls (tests); otherwise one daemon thread started by the first full window."""

    def __init__(self, gateway, workspace_id, meeting_id, *, clock=time.time, threaded=True):
        self.gateway, self.workspace_id, self.meeting_id = gateway, workspace_id, meeting_id
        self.clock, self.threaded = clock, threaded
        self.meetings = store.open_meetings(gateway, workspace_id)
        self.broken = False  # the engine failed ``FAIL_LIMIT`` windows in a row
        self._cv = threading.Condition()
        self._out = threading.Lock()  # one writer of segments and stream bookkeeping at a time
        self._queue: deque = deque()
        self._streams: dict[Optional[str], _Stream] = {}
        self._t0: Optional[float] = None
        self._closed = False
        self._finishing = False
        self._failures = 0
        self._delayed = False
        self._thread: Optional[threading.Thread] = None
        self._language: Optional[str] = None

    def ready(self) -> bool:
        return engine_for(self.gateway) is not None

    # --- the sink ---------------------------------------------------------------------------

    def frame(self, meeting_id, frame) -> None:
        pcm = frame.pcm
        if not pcm or self._closed:
            return
        key = str(frame.ext_participant)[:MAX_ID] if frame.ext_participant else None
        now = self.clock()
        with self._cv:
            if self._t0 is None:
                self._t0 = now
            stream = self._streams.get(key)
            if stream is None:
                stream = self._streams[key] = _Stream(
                    int((now - self._t0) * 1000), len(self._streams) >= MAX_STREAMS
                )
            windows = stream.segmenter.feed(pcm)
            for window in windows:
                if stream.muted:  # more streams than we transcribe: recorded, not dropped silently
                    self._gap(key, window)
                else:
                    self._queue.append((key, window))
            self._expire(now)
            if windows:
                self._cv.notify_all()
                self._start()

    def discard(self, meeting_id) -> None:
        """Drop everything held: the buffers, the queue and any window being transcribed (its text
        is not written). The segments are deleted by the end that follows."""
        with self._cv:
            self._closed = True
            self._queue.clear()
            self._streams.clear()
            self._cv.notify_all()

    def finish(self, meeting_id) -> None:
        """The meeting is ending and keeps what it captured: transcribe what is left, so the notes
        see all of it. What cannot be done within ``FINISH_S`` is recorded as a gap."""
        with self._cv:
            if self._closed:
                return
            for key, stream in self._streams.items():
                window = stream.segmenter.flush()
                if window is not None:
                    self._queue.append((key, window))
            self._finishing = True
            self._cv.notify_all()
            thread = self._thread
        if self.threaded:
            if thread is not None:
                thread.join(FINISH_S)
        else:
            self.drain()
        with self._cv:
            leftover, self._queue = list(self._queue), deque()
            self._closed = True
        for key, window in leftover:
            self._gap(key, window)

    # --- the queue --------------------------------------------------------------------------

    def _start(self):
        if self.threaded and self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="meeting-transcribe")
            self._thread.daemon = True
            self._thread.start()

    def _lag_ms(self, window, now) -> int:
        return int((now - (self._t0 or now)) * 1000) - window.end_ms

    def _expire(self, now):
        """Windows further behind real time than ``MAX_LAG_S`` are not transcribed: a gap. Called
        under ``self._cv``; the oldest window is the most behind."""
        while self._queue and self._lag_ms(self._queue[0][1], now) > MAX_LAG_S * 1000:
            key, window = self._queue.popleft()
            self._gap(key, window)

    def _loop(self):
        while True:
            with self._cv:
                while not self._queue and not self._finishing and not self._closed:
                    self._cv.wait(1.0)
                if self._closed or (not self._queue and self._finishing):
                    return
                if not self._queue:
                    continue
                key, window = self._queue.popleft()
            self._process(key, window)

    def drain(self) -> int:
        """Transcribe everything queued, on the calling thread. Returns the windows handled."""
        handled = 0
        while True:
            with self._cv:
                if self._closed or not self._queue:
                    return handled
                key, window = self._queue.popleft()
            self._process(key, window)
            handled += 1

    # --- one window -------------------------------------------------------------------------

    def _give_up(self) -> bool:
        return self._closed

    def _process(self, key, window):
        voice_engine = engine_for(self.gateway)
        if voice_engine is None:
            return self._failed(key, window)
        limit = self.meetings.settings()["max_concurrent"]
        if not SLOTS.acquire(self.workspace_id, limit, self._give_up):
            return None
        try:
            if not voice.wait_for_dictation(self._give_up):
                return None
            if self._lag_ms(window, self.clock()) > MAX_LAG_S * 1000:
                return self._gap(key, window)
            try:
                text = voice_engine.transcribe(self._lang(), window.pcm)
            except Exception as exc:  # the type only: a message may carry the user's words
                logger.warning("meeting transcription failed type=%s", type(exc).__name__)
                return self._failed(key, window)
        finally:
            SLOTS.release(self.workspace_id)
        self._failures = 0
        return self._segment(key, window, text)

    def _lang(self) -> str:
        if self._language is None:
            row = self.meetings.run_row(self.meeting_id)
            self._language = self.meetings.person(row["user_id"])["language"]
        return self._language

    def _failed(self, key, window):
        self._failures += 1
        if self._failures >= FAIL_LIMIT:
            self.broken = True
        self._gap(key, window)

    # --- what is written --------------------------------------------------------------------

    def _segment(self, key, window, text):
        with self._out:
            stream = self._streams.get(key)
            if stream is None or self._closed:
                return None
            body = stitch(stream.text, " ".join(str(text or "").split()))[:TEXT_MAX]
            stream.text = (stream.text + " " + body)[-400:] if body else stream.text
            if not body:  # silence: nothing was said, nothing is stored
                stream.end_ms = max(stream.end_ms, window.end_ms)
                return None
            start = max(window.start_ms, stream.end_ms)
            seq = self.meetings.add_segment(
                self.meeting_id, key, start, max(window.end_ms, start), body
            )
            if seq is not None:
                stream.end_ms, stream.gap = max(window.end_ms, start), None
            return seq

    def _gap(self, key, window):
        """Record that this window's words are missing. Back to back gaps are one segment. A
        stall is also noted once in the meeting's journal for the inviter's page."""
        with self._out:
            stream = self._streams.get(key)
            if stream is None or self._closed and not self._finishing:
                return None
            if not self._delayed and not stream.muted:
                self._delayed = True
                self.meetings.note_event(self.meeting_id, DELAYED, self.clock())
            start = max(window.start_ms, stream.end_ms)
            end = max(window.end_ms, start)
            if stream.gap is not None and self.meetings.extend_segment(
                self.meeting_id, stream.gap[0], end
            ):
                stream.gap = (stream.gap[0], end)
            else:
                seq = self.meetings.add_segment(self.meeting_id, key, start, end, GAP)
                stream.gap = (seq, end) if seq is not None else None
            stream.end_ms = end
            stream.text = ""  # the seam after a gap has nothing to stitch to
        return None
