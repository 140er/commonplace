"""The in-meeting chat journal: one row per addressed message, the caps, and proposed actions.

A mixin of :class:`commonplace.meetings.store.MeetingStore` (it uses the store's ``_tx``). Only a
message the adapter marked ``addressed`` is ever written here; everything else in the chat is
dropped before it reaches the store. A row is keyed by the provider's event id, so a replayed event
is one row and answers once. The question and the answer live only in this row, for the inviter,
and go when the meeting is deleted or discarded. Nothing here returns a sealed value, writes
``meetings.state`` or creates a card: a request to add something becomes a *proposed* action note
the inviter approves later.
"""

from __future__ import annotations

import hashlib
import json
import time

from commonplace.meetings import lifecycle

ANSWER_CAP = 20  # answers (and refusals) per meeting
COOLDOWN_S = 30  # between one participant's answered questions
COUNTED = ("received", "answered", "refused")  # states that use up the cap


def _stamp(now) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))


class ChatMixin:
    """Methods added to ``MeetingStore``."""

    def chat_intake(
        self,
        meeting_id,
        ext_event,
        ext_participant,
        question,
        now: float,
        *,
        cap=ANSWER_CAP,
        cooldown_s=COOLDOWN_S,
    ) -> str:
        """Journal one addressed message and say what to do with it: ``accepted`` (answer it),
        ``duplicate`` (this event was seen: nothing happens again), ``cooldown`` or ``capped`` (kept
        as ``ignored``, without the question) or ``closed`` (the meeting is not listening: nothing
        is written)."""
        stamped = _stamp(now)
        with self._tx() as conn:
            row = conn.execute("SELECT state FROM meetings WHERE id=?", (meeting_id,)).fetchone()
            if row is None or row["state"] not in lifecycle.CAPTURING:
                return "closed"
            if conn.execute(
                "SELECT 1 FROM meeting_chat WHERE meeting_id=? AND ext_event=?",
                (meeting_id, ext_event),
            ).fetchone():
                return "duplicate"
            used = conn.execute(
                "SELECT COUNT(*) AS n FROM meeting_chat WHERE meeting_id=? AND state IN "
                "('received','answered','refused')",
                (meeting_id,),
            ).fetchone()["n"]
            recent = conn.execute(
                "SELECT 1 FROM meeting_chat WHERE meeting_id=? AND ext_participant IS ? "
                "AND state<>'ignored' AND created_at>?",
                (meeting_id, ext_participant, _stamp(now - cooldown_s)),
            ).fetchone()
            verdict = "capped" if used >= cap else "cooldown" if recent else "accepted"
            conn.execute(
                "INSERT INTO meeting_chat(meeting_id, ext_event, ext_participant, state, question, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (
                    meeting_id,
                    ext_event,
                    ext_participant,
                    "received" if verdict == "accepted" else "ignored",
                    question if verdict == "accepted" else None,
                    stamped,
                    stamped,
                ),
            )
            return verdict

    def chat_finish(self, meeting_id, ext_event, state, now: float, answer=None) -> bool:
        """Close a ``received`` row as ``answered``, ``refused``, ``ignored`` or ``failed:<code>``.
        An update only: after a discard has deleted the row nothing is written back."""
        keep = state in ("answered", "refused") or state.startswith("failed:")
        with self._tx() as conn:
            return (
                conn.execute(
                    "UPDATE meeting_chat SET state=?, answer=?, updated_at=?, "
                    "question=CASE WHEN ? THEN question END "
                    "WHERE meeting_id=? AND ext_event=? AND state='received'",
                    (state, answer, _stamp(now), int(keep), meeting_id, ext_event),
                ).rowcount
                == 1
            )

    def chat_rows(self, meeting_id) -> list[dict]:
        with self._tx(write=False) as conn:
            rows = (
                conn.execute(
                    "SELECT ext_event, ext_participant, state, question, answer FROM meeting_chat "
                    "WHERE meeting_id=? ORDER BY created_at, rowid",
                    (meeting_id,),
                ).fetchall()
                if conn
                else []
            )
            return [dict(r) for r in rows]

    def recent_segments(self, meeting_id, limit) -> list[dict]:
        """The newest ``limit`` segments, oldest first: what the meeting said most recently."""
        with self._tx(write=False) as conn:
            rows = (
                conn.execute(
                    "SELECT seq, ext_participant, t_start_ms, text FROM meeting_segments "
                    "WHERE meeting_id=? ORDER BY seq DESC LIMIT ?",
                    (meeting_id, limit),
                ).fetchall()
                if conn
                else []
            )
            return [dict(r) for r in reversed(rows)]

    def participant_label(self, meeting_id, ext_participant):
        """The provider's display name for a participant (untrusted text), or None."""
        if not ext_participant:
            return None
        with self._tx(write=False) as conn:
            row = (
                conn.execute(
                    "SELECT label FROM meeting_participants WHERE meeting_id=? "
                    "AND ext_participant=?",
                    (meeting_id, ext_participant),
                ).fetchone()
                if conn
                else None
            )
            return row["label"] if row else None

    def open_notes(self, meeting_id) -> list[dict]:
        """The draft and proposed notes of this meeting so far (generated text, labelled as such)."""
        with self._tx(write=False) as conn:
            rows = (
                conn.execute(
                    "SELECT kind, text FROM meeting_notes WHERE meeting_id=? "
                    "AND state IN ('draft','proposed') ORDER BY created_at, rowid",
                    (meeting_id,),
                ).fetchall()
                if conn
                else []
            )
            return [dict(r) for r in rows]

    def add_proposed_action(self, meeting_id, ext_event, text, asker, model, now: float):
        """A request typed into the chat, kept as a *proposed* action tagged with the asker's label.
        It creates no card: only the inviter's later approval does. One note per chat event, and only
        while the meeting is listening. Returns the note id, or None."""
        note_id = hashlib.sha256(f"chat-action:{meeting_id}:{ext_event}".encode()).hexdigest()[:32]
        evidence = {"ranges": [], "speakers": [], "chat": ext_event, "asker": asker}
        with self._tx() as conn:
            row = conn.execute("SELECT state FROM meetings WHERE id=?", (meeting_id,)).fetchone()
            if row is None or row["state"] not in lifecycle.CAPTURING:
                return None
            conn.execute(
                "INSERT OR IGNORE INTO meeting_notes(id, meeting_id, kind, text, evidence, state, "
                "edited, model, created_at) VALUES (?,?,'action',?,?,'proposed',0,?,?)",
                (
                    note_id,
                    meeting_id,
                    text,
                    json.dumps(evidence, separators=(",", ":")),
                    model,
                    _stamp(now),
                ),
            )
            return note_id
