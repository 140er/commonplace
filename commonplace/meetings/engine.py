"""One meeting's lifecycle over an adapter: join, announce, capture gate, end, discard.

The consent rule is the whole point of this module and is kept in one place:

* nothing is captured until the in-meeting announcement is ``sent`` (adapter-confirmed); the
  capture gate (:class:`Capture`) is closed until then and audio is not even requested from the
  adapter, so a frame can neither be processed nor buffered earlier;
* if the announcement cannot be confirmed (chat send missing, a post that failed twice, an unknown
  outcome) the bot leaves and the meeting ends ``failed:no_announcement`` with nothing stored;
* a host removing the bot, anyone typing ``bot stop``, the owner's stop, or any worker error closes
  the gate first, discards what was captured, leaves, and ends the meeting.

What may happen to a meeting, and in which state, is the transition table in ``lifecycle.py``, and
the store has one function that writes the state (``MeetingStore._transition``): this module only
asks for steps and ends, and a step the table refuses (a stop landed, the meeting ended) is the
worker's cue to stop. The worker's ends go through :meth:`Run._conclude` (a meeting it can no
longer drive is concluded as failed there too); an orphan, whose worker is gone, ends through
:func:`recover`. Text from the meeting (chat, display names) is data: it is never logged, and only
an exact ``bot stop`` has any effect. Nothing here names a provider.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Optional

from commonplace.meetings import adapter, answers, lifecycle, notes, store

logger = logging.getLogger("commonplace.meetings")

LEASE_S = 45  # a running meeting whose worker has not beaten for this long is orphaned
LOBBY_S = 600
JOIN_TRIES = 2
ANNOUNCE_TRIES = 2
REMINDER_S = 600
LABEL_MAX = 80
BOT_STOP = re.compile(r"bot stop[.!]*\Z")

ANNOUNCEMENT = {
    "en": (
        "I'm a notes bot, not a person. {inviter} invited me to listen and take notes. The audio "
        "is transcribed on their own server. Any host can remove me; type 'bot stop' to make me "
        "leave and discard everything. Ask {inviter} about the notes."
    )
}
REMINDER = {
    "en": (
        "Reminder: I'm a notes bot, not a person. {inviter} invited me to listen and take "
        "notes. Any host can remove me; type 'bot stop' to make me leave and discard everything."
    )
}
NOTICES = {
    "stopped": "Notes bot: capture has stopped and nothing was kept. I am leaving.",
    "leaving": "Notes bot: I am leaving now.",
}
_MARKUP = re.compile(r"[<>&*_`\[\]()\\|~#@{}]")


def display_label(name, fallback="a team member") -> str:
    """A person's name as the bot shows it: no control characters or markup, short."""
    text = _MARKUP.sub("", "".join(c if c.isprintable() else " " for c in str(name or "")))
    text = " ".join(text.split())[:40].strip()
    return text or fallback


def bot_name(inviter_name) -> str:
    return f"Notes bot for {display_label(inviter_name)}"


def is_stop(text) -> bool:
    """Only an exact ``bot stop`` counts: the announcement quotes the phrase, so a message that
    merely contains it (or the bot's own echoed post) is not a command."""
    return isinstance(text, str) and bool(BOT_STOP.fullmatch(" ".join(text.lower().split())))


class Sink:
    """Where captured audio goes. This base drops frames (tests use it); the capture sink
    (``capture.CaptureSink``) is what a real hub runs. ``discard`` and ``finish`` are the two ways
    out: the first drops everything held, the second keeps it for the notes."""

    def ready(self) -> bool:
        """Can this sink transcribe at all. False ends the meeting before the bot joins."""
        return True

    def frame(self, meeting_id: str, frame: adapter.Frame) -> None:
        return None

    def discard(self, meeting_id: str) -> None:
        return None

    def finish(self, meeting_id: str) -> None:
        return None


class Capture:
    """The capture gate. Closed until the announcement was delivered, and closed again, under the
    same lock a frame takes, before anything is discarded: no frame is processed after the gate
    closes, and none before it opens."""

    def __init__(self, sink: Sink, meeting_id: str):
        self._sink, self._meeting_id = sink, meeting_id
        self._lock = threading.Lock()
        self.open = False

    def allow(self, verify=None) -> bool:
        """Open the gate, unless ``verify`` (run under the lock) says the meeting is no longer in
        the state that permits capture: a stop that landed first keeps it closed."""
        with self._lock:
            if verify is not None and not verify():
                return False
            self.open = True
            return True

    def feed(self, frame) -> bool:
        with self._lock:
            if not self.open:
                return False
            self._sink.frame(self._meeting_id, frame)
            return True

    def close(self, *, discard: bool) -> None:
        with self._lock:
            self.open = False
            if discard:
                self._sink.discard(self._meeting_id)


class _Finish(Exception):
    def __init__(self, state, reason, *, discard, notice=None):
        super().__init__(reason)
        self.state, self.reason, self.discard, self.notice = state, reason, discard, notice


class _Gone(Exception):
    """The meeting ended elsewhere (the kill switch, a recovery) or cannot be driven any further:
    leave and drop what is held. If the store still holds it active it is ended as a failure."""


class Run:
    """Drives one meeting from ``requested`` to its end. ``run`` blocks until then."""

    def __init__(self, gateway, workspace_id, meeting_id, *, clock=time.time, sink=None):
        self.gateway, self.workspace_id, self.meeting_id = gateway, workspace_id, meeting_id
        self.clock = clock
        self.meetings = store.open_meetings(gateway, workspace_id)
        self.sink = sink or Sink()
        self.capture = Capture(self.sink, meeting_id)
        self.provider: Optional[adapter.Adapter] = None
        self.connection: Optional[adapter.Connection] = None
        self.handle = None
        self.row: dict = {}
        self._audio: Optional[threading.Thread] = None
        self._audio_failed = False
        self._lobby_until = 0.0
        self._t0 = 0.0
        self._reminded_at = 0.0
        self._reminded_count = 0
        self._answers: Optional[answers.Answerer] = None

    # --- entry ----------------------------------------------------------------------------

    def run(self) -> str:
        """The meeting's final state. A meeting that is not ``requested`` (a second fire) or that
        the workspace has no room for yet is left as it is."""
        now = self.clock()
        if not self.meetings.claim(self.meeting_id, now, LEASE_S):
            return self.meetings.run_row(self.meeting_id)["state"]
        self._t0 = now
        stop = threading.Event()
        threading.Thread(
            target=self._heartbeat, args=(stop,), name="meeting-lease", daemon=True
        ).start()
        try:
            self._lifecycle()
        except _Finish as end:
            self._conclude(end)
        except (_Gone, store.Missing):  # ended elsewhere (a no-op below) or cannot go on
            self._conclude(_Finish("failed:internal", "failed:internal", discard=True))
        except Exception as exc:  # fail closed: capture off, leave, discard
            logger.warning("meeting worker failed type=%s", type(exc).__name__)
            self._conclude(_Finish("failed:internal", "failed:internal", discard=True))
        finally:
            stop.set()
        return self.meetings.run_row(self.meeting_id)["state"]

    def _heartbeat(self, stop):
        """Keep the lease fresh while a provider call blocks (a join or a post can take longer than
        the lease). It stops by itself a little after the hard time limit, so a wedged call cannot
        hold a meeting forever."""
        while not stop.wait(LEASE_S / 3):
            if (
                self.clock() - self._t0
                > self.meetings.settings()["max_minutes"] * 60 + LEASE_S + notes.BUDGET_S
            ):
                return
            try:
                if not self.meetings.renew(self.meeting_id, self.clock()):
                    return
            except Exception as exc:
                logger.warning("meeting lease renewal failed type=%s", type(exc).__name__)

    # --- the steps ------------------------------------------------------------------------

    def _lifecycle(self):
        self.row = self.meetings.run_row(self.meeting_id)
        self.connection = self.meetings.open_connection(self.row["connection_id"])
        self.provider = adapter.get(self.connection.provider)
        if self.provider is None:
            raise _Finish(
                "failed:provider_unavailable", "failed:provider_unavailable", discard=True
            )
        if not self.sink.ready():  # no local engine: not joining, and nothing to discard
            raise _Finish(
                "failed:needs_local_transcription",
                "failed:needs_local_transcription",
                discard=True,
            )
        self._guard()
        parsed = self.provider.parse_link(self.meetings.open_join_ref(self.meeting_id))
        if not isinstance(parsed, adapter.JoinRef):
            raise _Finish("failed:bad_link", "failed:bad_link", discard=True)
        outcome = self._join(parsed)
        self.handle = outcome.handle
        events = iter(self.provider.events(self.connection, self.handle))
        if isinstance(outcome, adapter.Lobby):
            self._wait_for_admission(events)
        self._announce()
        self._live(events)

    def _join(self, parsed):
        outcome = None
        for _ in range(JOIN_TRIES):
            try:
                outcome = self.provider.join(
                    self.connection, parsed, self.row["bot_name"], self.meeting_id
                )
                break
            except Exception as exc:
                logger.warning("meeting join failed type=%s", type(exc).__name__)
        if outcome is None:
            raise _Finish(
                "failed:provider_unavailable", "failed:provider_unavailable", discard=True
            )
        if isinstance(outcome, adapter.Reject) or not isinstance(
            outcome, (adapter.JoinHandle, adapter.Lobby)
        ):
            raise _Finish("failed:join_rejected", "failed:join_rejected", discard=True)
        return outcome

    def _wait_for_admission(self, events):
        """Wait in the lobby for a host to admit the bot; never bypass it, never wait forever."""
        self._move("lobby")
        self._lobby_until = self.clock() + LOBBY_S
        for event in events:
            self._guard()
            if event.kind == "admitted":
                return
            if event.kind == "lobby_timeout":
                break
            self._common_exit(event, announced=False)
        raise _Finish("failed:not_admitted", "failed:not_admitted", discard=True)

    def _announce(self):
        """Post the fixed announcement and require it to be ``sent`` before the gate opens."""
        if not self.provider.caps.chat_send:
            raise _Finish("failed:no_announcement", "failed:no_announcement", discard=True)
        self._move("announcing")
        inviter = self._inviter()
        text = ANNOUNCEMENT.get(self._language(), ANNOUNCEMENT["en"]).format(inviter=inviter)
        for _ in range(ANNOUNCE_TRIES):
            outcome = self._post("announce", "announce", text)
            if outcome != "failed":
                break
        if outcome != "sent":  # failed twice, or unknown: not confirmed, so not listening
            raise _Finish("failed:no_announcement", "failed:no_announcement", discard=True)
        now = self.clock()
        stamped = store.stamp_ts(now)
        self._move("live", announced_at=stamped, started_at=stamped)
        self._reminded_at = now
        self._reminded_count = self.meetings.participants_present(self.meeting_id)
        if not self.capture.allow(self._is_live):
            self._guard()  # a stop or a leave landed first: it ends the meeting, nothing opens
            raise _Gone
        self._audio = threading.Thread(target=self._pump_audio, name="meeting-audio", daemon=True)
        self._audio.start()
        if self.provider.caps.chat_receive:
            self._answers = self._answerer()

    def _live(self, events):
        for event in events:
            self._guard()
            self._common_exit(event, announced=True)
            if event.kind in ("participant_joined", "participant_left"):
                self._participant(event)
            elif event.kind == "chat" and self._answers is not None:
                self._answers.offer(event)
            self._maybe_remind()
        raise _Finish("done", "done", discard=False)  # the stream closed: the meeting is over

    def _common_exit(self, event, *, announced):
        """The events that end a meeting wherever it is: removal, the end, ``bot stop``."""
        if event.kind == "removed":
            raise _Finish("discarded", "host_removed", discard=True)
        if event.kind == "meeting_ended":
            if announced:
                raise _Finish("done", "done", discard=False)
            raise _Finish("cancelled", "cancelled", discard=True)
        if event.kind == "chat" and is_stop(event.text):
            raise _Finish("discarded", "stop_requested", discard=True, notice="stopped")

    def _participant(self, event):
        ext = event.ext_participant
        if not ext:
            return
        now = self.clock()
        if event.kind == "participant_left":
            self.meetings.participant_left(self.meeting_id, ext, now)
            return
        label = " ".join(str(event.text or "").split())[:LABEL_MAX] or "participant"
        self.meetings.participant_seen(self.meeting_id, ext, label, event.host, now)

    def _maybe_remind(self):
        """Late joiners are told too: when more people are present than at the last announcement,
        repeat it, at most once per ``REMINDER_S``."""
        now = self.clock()
        present = self.meetings.participants_present(self.meeting_id)
        if present > self._reminded_count and now - self._reminded_at >= REMINDER_S:
            self._reminded_at, self._reminded_count = now, present
            text = REMINDER.get(self._language(), REMINDER["en"]).format(inviter=self._inviter())
            self._post("reminder", f"r{int(now)}", text)

    def _answerer(self) -> answers.Answerer:
        return answers.Answerer(
            self.gateway,
            self.workspace_id,
            self.row,
            meetings=self.meetings,
            clock=self.clock,
            post=self._post,
            live=self._is_live,
            allowed=self._allowed,
            inviter=self._inviter,
            max_text=self.provider.caps.max_text,
        )

    def _pump_audio(self):
        try:
            for frame in self.provider.audio(self.connection, self.handle):
                if frame.ext_participant is not None and not self.provider.caps.per_speaker_audio:
                    frame = adapter.Frame(frame.pcm)  # a mixed stream carries no speaker
                if not self.capture.feed(frame):
                    return
        except Exception as exc:
            logger.warning("meeting audio failed type=%s", type(exc).__name__)
            self._audio_failed = True

    # --- guards and helpers -----------------------------------------------------------------

    def _guard(self):
        """Every beat: the lease, the switches and limits, and what others asked for."""
        now = self.clock()
        if not self.meetings.renew(self.meeting_id, now):
            raise _Gone
        row = self.meetings.run_row(self.meeting_id)
        if lifecycle.is_ended(row["state"]):
            raise _Gone
        if self._audio_failed:
            raise _Finish("failed:audio", "failed:audio", discard=True)
        if getattr(self.sink, "broken", False):  # the engine failed window after window
            raise _Finish("failed:transcription", "failed:transcription", discard=True)
        if row["state"] == lifecycle.REMOVED:
            raise _Finish(
                "discarded", row["end_reason"] or "stop_requested", discard=True, notice="stopped"
            )
        if row["state"] in (lifecycle.ENDING, lifecycle.PROCESSING):
            raise _Finish(
                "done", row["end_reason"] or "inviter_left", discard=False, notice="leaving"
            )
        if not self._allowed():
            raise _Finish("cancelled", "cancelled", discard=True, notice="stopped")
        if row["state"] == lifecycle.LOBBY and now > self._lobby_until:
            raise _Finish("failed:not_admitted", "failed:not_admitted", discard=True)
        limit = self.meetings.settings()["max_minutes"] * 60
        if now - self._t0 > limit:
            raise _Finish("timeout", "timeout", discard=False, notice="leaving")

    def _allowed(self) -> bool:
        """Re-checked every beat: the workspace, the person, their membership and the connection."""
        if not self.meetings.enabled() or not self.meetings.connection_enabled(
            self.row["connection_id"]
        ):
            return False
        if not self.meetings.person(self.row["user_id"])["enabled"]:
            return False
        try:
            members = self.gateway.db.members(self.workspace_id)
        except Exception:
            return False
        return any(m["id"] == self.row["user_id"] for m in members)

    def _move(self, state, **fields):
        """Record the next state. If the table refuses it, someone asked for the end meanwhile (or
        it ended): the guard says which, and ends the meeting accordingly."""
        if not self.meetings.advance(self.meeting_id, state, self.clock(), **fields):
            self._guard()
            raise _Gone

    def _is_live(self) -> bool:
        return self.meetings.state_of(self.meeting_id) == lifecycle.LIVE

    def _inviter(self) -> str:
        try:
            return display_label(self.gateway.db.user(self.row["user_id"])["name"])
        except Exception:
            return "a team member"

    def _language(self) -> str:
        return self.meetings.person(self.row["user_id"])["language"]

    def _post(self, kind, ref, text) -> str:
        """One chat post through the outbox: ``sent``, ``failed`` (definitely not delivered, safe
        to try again) or ``unknown`` (maybe delivered, never blindly retried)."""
        outbox_id, prior = self.meetings.outbox_begin(self.meeting_id, kind, ref)
        if prior == "sent":
            return "sent"
        if prior not in ("new", "queued"):
            return "unknown"
        try:
            result = self.provider.post_chat(self.connection, self.handle, text, outbox_id)
        except Exception as exc:
            logger.warning("meeting post failed type=%s", type(exc).__name__)
            self.meetings.outbox_mark(outbox_id, "unknown")
            return "unknown"
        if getattr(result, "ok", False) is True:
            self.meetings.outbox_mark(outbox_id, "sent", str(result.ext_post)[:200])
            return "sent"
        self.meetings.outbox_mark(outbox_id, "queued")
        return "failed"

    # --- the way out ----------------------------------------------------------------------

    def _leave(self):
        if self.handle is None or self.provider is None:
            return
        try:
            self.provider.leave(self.connection, self.handle)
        except Exception as exc:
            logger.warning("meeting leave failed type=%s", type(exc).__name__)

    def _join_audio(self):
        if self._audio is not None and self._audio is not threading.current_thread():
            self._audio.join(timeout=2)

    def _conclude(self, end: _Finish):
        """Close the gate first, say goodbye if the bot told the meeting it was there, leave, then
        record the end. The record is the table's: a meeting that already ended stays as it is, and
        one that was stopped (or whose keep-end the table refuses) ends discarded."""
        if self._answers is not None:
            if end.discard:
                self._answers.close()  # a stop, a removal or a failure: no answer after this
            else:
                self._answers.drain()  # questions already accepted are answered while still live
        self.capture.close(discard=end.discard)  # the gate first: nothing more is captured
        told = self.meetings.outbox_state(self.meeting_id, "announce", "announce") == "sent"
        if end.notice and told and self.handle is not None:
            kind = "leave_notice" if end.notice == "leaving" else "notice"
            self._post(kind, end.notice, NOTICES[end.notice])
        self._leave()
        self._join_audio()
        now = self.clock()
        if not end.discard:
            if self._finish_kept(end, now):
                self._audit()
                return
            state = self.meetings.state_of(self.meeting_id)
            if lifecycle.is_ended(state):
                return
            # Refused while still active: a stop landed at one of the steps, and a stop wins
            # whatever the step, so what was captured is discarded.
            self.sink.discard(self.meeting_id)
            end = (
                _Finish("discarded", "stop_requested", discard=True)
                if state == lifecycle.REMOVED
                else _Finish("failed:internal", "failed:internal", discard=True)
            )
        try:
            ended = self.meetings.finish(self.meeting_id, end.state, end.reason, now, discard=True)
        except store.Missing:
            return
        if ended:
            self._audit()

    def _finish_kept(self, end, now) -> bool:
        """``ending`` (wipes the sealed link), ``processing``, then the end state, keeping what was
        captured. Each is an event of the table; False as soon as one is refused."""
        if not self.meetings.advance(
            self.meeting_id, lifecycle.ENDING, now, wipe_ref=True, reason=end.reason
        ):
            return False
        self.sink.finish(self.meeting_id)
        if not self.meetings.advance(self.meeting_id, lifecycle.PROCESSING, now):
            return False
        self._notes()
        return self.meetings.finish(self.meeting_id, end.state, end.reason, now, discard=False)

    def _notes(self):
        """The notes, made while the meeting is ``processing``. A failure here never blocks the
        end: the segments wait for the beat's retry and the sweep."""
        try:
            notes.generate(self.gateway, self.workspace_id, self.meeting_id, now=self.clock())
        except Exception as exc:
            logger.warning("meeting notes failed type=%s", type(exc).__name__)

    def _audit(self):
        """One audit row for the end, with the reason the store recorded (the first cause wins)."""
        try:
            self.gateway.db.audit(
                action="meetings.end",
                decision="ok",
                workspace_id=self.workspace_id,
                user_id=self.row.get("user_id", ""),
                reason=self.meetings.run_row(self.meeting_id)["end_reason"] or "",
                target_ids=[self.meeting_id],
            )
        except Exception as exc:
            logger.warning("meeting audit failed type=%s", type(exc).__name__)


def recover(gateway, workspace_id, meeting_id, *, clock=time.time) -> Optional[str]:
    """A running meeting whose worker is gone: the bot leaves, the sealed link is wiped, posts that
    may or may not have gone out are marked ``unknown`` and the meeting ends ``interrupted``
    (what it had stored is kept, as the design note's recovery says; the audio buffer died with the
    worker), or ``discarded`` if a stop had been requested: a stop always discards. It never
    rejoins: listening again would need a new announcement and a person's decision. Returns the
    state it was ended in, or None if the meeting had ended meanwhile."""
    meetings = store.open_meetings(gateway, workspace_id)
    row = meetings.run_row(meeting_id)
    try:
        connection = meetings.open_connection(row["connection_id"])
        provider = adapter.get(connection.provider)
        handle = provider.find(connection, meeting_id) if provider else None
        if handle is not None:
            provider.leave(connection, handle)
    except Exception as exc:
        logger.warning("meeting recovery leave failed type=%s", type(exc).__name__)
    meetings.outbox_unknown(meeting_id)
    ended = meetings.recover(meeting_id, clock())
    if ended is None:
        return None
    state, reason = ended
    try:
        gateway.db.audit(
            action="meetings.end",
            decision="ok",
            workspace_id=workspace_id,
            user_id=row["user_id"],
            reason=reason,
            target_ids=[meeting_id],
        )
    except Exception as exc:
        logger.warning("meeting audit failed type=%s", type(exc).__name__)
    return state
