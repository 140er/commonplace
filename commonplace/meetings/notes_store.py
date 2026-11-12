"""The inviter's notes, and what a meeting holds for them: segments, note runs, filing claims.

A mixin of :class:`commonplace.meetings.store.MeetingStore` (it uses the store's ``_tx``), so the
store file keeps to switches, invites and the lifecycle. Everything here is read and written for the
inviter only: a method that takes ``user_id`` raises ``Missing`` for anyone else's meeting. Notes
are generated text and are stored as such; a person's edit is the only way a note becomes theirs.
Nothing here writes ``meetings.state`` and nothing returns a sealed value.
"""

from __future__ import annotations

import json
import time
import uuid

from commonplace.meetings import lifecycle

SCHEMA = """
CREATE TABLE IF NOT EXISTS meeting_note_runs (
  meeting_id TEXT PRIMARY KEY,
  state TEXT NOT NULL CHECK (state IN ('done','unavailable','failed')),
  code TEXT NOT NULL DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0,
  notes INTEGER NOT NULL DEFAULT 0,
  dropped INTEGER NOT NULL DEFAULT 0,
  at TEXT NOT NULL
);
"""

KINDS = ("summary", "decision", "action", "open_question")
# Where notes may be written: the end of a normal end and the endings that keep what was captured.
KEPT_ENDS = (lifecycle.DONE, lifecycle.TIMEOUT, lifecycle.INTERRUPTED)
NOTES_STATES = (lifecycle.PROCESSING, *KEPT_ENDS)
OPEN_STATES = {
    "summary": "draft",
    "decision": "draft",
    "open_question": "draft",
    "action": "proposed",
}
FILED_STATES = {
    "summary": "kept",
    "decision": "kept",
    "open_question": "kept",
    "action": "approved",
}
SWEEP_S = 24 * 3600  # working segments never outlive this without the inviter's kept transcript
RETRY_S = 600
TRIES = 3


def _store():
    from commonplace.meetings import store  # the store imports this module: resolved late

    return store


def _refused(code, message):
    return _store().Refused(code, message)


def _stamp(now=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))


class NotesMixin:
    """Methods added to ``MeetingStore``."""

    # --- segments and the transcript ------------------------------------------------------

    def segments(self, meeting_id) -> list[dict]:
        with self._tx(write=False) as conn:
            rows = (
                conn.execute(
                    "SELECT seq, ext_participant, t_start_ms, t_end_ms, text FROM meeting_segments "
                    "WHERE meeting_id=? ORDER BY seq",
                    (meeting_id,),
                ).fetchall()
                if conn
                else []
            )
            return [dict(r) for r in rows]

    @staticmethod
    def _keeps(conn, row) -> bool:
        """A transcript is kept only when the owner allows it and the inviter ticked it."""
        allowed = conn.execute("SELECT transcript_ok FROM meeting_settings").fetchone()
        return bool(row["keep_transcript"] and allowed and allowed["transcript_ok"])

    def transcript(self, meeting_id, user_id) -> dict:
        """The verbatim record of other people's speech, when kept (else ``kept`` is False)."""
        with self._tx(write=False) as conn:
            row = self._owned(conn, meeting_id, user_id)
            if not self._keeps(conn, row) or row["state"] not in KEPT_ENDS:
                return {"kept": False, "segments": []}
            labels = self._labels(conn, meeting_id)
            rows = conn.execute(
                "SELECT seq, ext_participant, t_start_ms, t_end_ms, text FROM meeting_segments "
                "WHERE meeting_id=? ORDER BY seq",
                (meeting_id,),
            ).fetchall()
            if not rows:
                return {"kept": False, "segments": []}
        return {
            "kept": True,
            "segments": [
                {
                    "seq": r["seq"],
                    "speaker": labels.get(r["ext_participant"]) if r["ext_participant"] else None,
                    "start_ms": r["t_start_ms"],
                    "end_ms": r["t_end_ms"],
                    "text": r["text"],
                }
                for r in rows
            ],
        }

    def delete_transcript(self, meeting_id, user_id) -> int:
        """Delete the working or kept segments of a meeting that is no longer running. The notes
        are untouched."""
        with self._tx() as conn:
            row = self._owned(conn, meeting_id, user_id)
            if lifecycle.is_active(row["state"]):
                raise _refused("meeting_active", "The meeting is still running")
            return conn.execute(
                "DELETE FROM meeting_segments WHERE meeting_id=?", (meeting_id,)
            ).rowcount

    def sweep(self, now: float) -> int:
        """Delete the working segments of meetings that ended a day ago and whose transcript is
        not kept (the backstop when notes never completed). Returns the rows deleted."""
        cutoff = _stamp(now - SWEEP_S)
        removed = 0
        with self._tx() as conn:
            marks = ",".join("?" * len(KEPT_ENDS))
            for row in conn.execute(
                f"SELECT * FROM meetings WHERE state IN ({marks}) AND ended_at <= ? AND id IN "
                "(SELECT meeting_id FROM meeting_segments)",
                (*KEPT_ENDS, cutoff),
            ).fetchall():
                if not self._keeps(conn, row):
                    removed += conn.execute(
                        "DELETE FROM meeting_segments WHERE meeting_id=?", (row["id"],)
                    ).rowcount
        return removed

    def holds_segments(self, now: float) -> bool:
        """Is there a finished meeting whose segments are waiting for notes or the sweep."""
        marks = ",".join("?" * len(NOTES_STATES))
        with self._tx(write=False) as conn:
            if conn is None:
                return False
            for row in conn.execute(
                f"SELECT * FROM meetings WHERE state IN ({marks}) AND id IN "
                "(SELECT meeting_id FROM meeting_segments)",
                NOTES_STATES,
            ).fetchall():
                if not self._keeps(conn, row):
                    return True
        return False

    # --- note runs and generation ---------------------------------------------------------

    def note_run(self, meeting_id) -> dict | None:
        with self._tx(write=False) as conn:
            row = (
                conn.execute(
                    "SELECT state, code, attempts, notes, dropped, at FROM meeting_note_runs "
                    "WHERE meeting_id=?",
                    (meeting_id,),
                ).fetchone()
                if conn
                else None
            )
        return dict(row) if row else None

    @staticmethod
    def _run(conn, meeting_id, state, code, notes, dropped, now):
        conn.execute(
            "INSERT INTO meeting_note_runs(meeting_id, state, code, attempts, notes, dropped, at) "
            "VALUES (?,?,?,1,?,?,?) ON CONFLICT(meeting_id) DO UPDATE SET code=excluded.code, "
            "state=excluded.state, attempts=attempts+1, notes=notes+excluded.notes, "
            "dropped=excluded.dropped, at=excluded.at",
            (meeting_id, state, code, notes, dropped, _stamp(now)),
        )

    def record_run(self, meeting_id, state, code, now: float) -> bool:
        """A generation that made nothing (no model, a refusal, a failure, an empty meeting).
        The segments stay for a retry and, if the transcript is not kept, for the sweep."""
        with self._tx() as conn:
            row = conn.execute("SELECT state FROM meetings WHERE id=?", (meeting_id,)).fetchone()
            if row is None or row["state"] not in NOTES_STATES:
                return False
            self._run(conn, meeting_id, state, code, 0, 0, now)
            if code == "empty":  # nothing was captured: nothing waits for a retry
                conn.execute("DELETE FROM meeting_segments WHERE meeting_id=?", (meeting_id,))
            return True

    def store_notes(
        self, meeting_id, items: list[dict], model: str, dropped: int, code, now
    ) -> bool:
        """Write a generated set as drafts, only while the meeting still keeps what it captured
        (a stop that landed first discarded it, and nothing is written back), and delete the
        working segments unless the transcript is kept. A regeneration adds a set; the earlier
        one stays until the inviter dismisses it."""
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
            if row is None or row["state"] not in NOTES_STATES:
                return False
            for item in items:
                conn.execute(
                    "INSERT INTO meeting_notes(id, meeting_id, kind, text, evidence, state, edited, "
                    "model, created_at) VALUES (?,?,?,?,?,?,0,?,?)",
                    (
                        uuid.uuid4().hex,
                        meeting_id,
                        item["kind"],
                        item["text"],
                        json.dumps(item["evidence"], separators=(",", ":")),
                        OPEN_STATES[item["kind"]],
                        model,
                        _stamp(now),
                    ),
                )
            self._run(conn, meeting_id, "done", code, len(items), dropped, now)
            if not self._keeps(conn, row):
                conn.execute("DELETE FROM meeting_segments WHERE meeting_id=?", (meeting_id,))
            return True

    def awaiting_notes(self, now: float) -> list[str]:
        """Finished meetings with segments whose notes were never made: retried a few times,
        spaced, within the day the segments may live."""
        marks = ",".join("?" * len(KEPT_ENDS))
        with self._tx(write=False) as conn:
            if conn is None:
                return []
            rows = conn.execute(
                f"SELECT m.id, r.state AS run, r.attempts, r.at FROM meetings m "
                "LEFT JOIN meeting_note_runs r ON r.meeting_id=m.id "
                f"WHERE m.state IN ({marks}) AND m.ended_at > ? AND m.id IN "
                "(SELECT meeting_id FROM meeting_segments) ORDER BY m.ended_at",
                (*KEPT_ENDS, _stamp(now - SWEEP_S)),
            ).fetchall()
        return [
            r["id"]
            for r in rows
            if r["run"] in (None, "unavailable", "failed")
            and (r["attempts"] or 0) < TRIES
            and (r["at"] is None or r["at"] <= _stamp(now - RETRY_S))
        ]

    # --- reading and reviewing notes ------------------------------------------------------

    @staticmethod
    def _owned(conn, meeting_id, user_id):
        row = (
            conn.execute("SELECT * FROM meetings WHERE id=?", (meeting_id,)).fetchone()
            if conn
            else None
        )
        if row is None or row["user_id"] != user_id:
            raise _store().Missing
        return row

    @staticmethod
    def _labels(conn, meeting_id) -> dict:
        return {
            r["ext_participant"]: r["label"]
            for r in conn.execute(
                "SELECT ext_participant, label FROM meeting_participants WHERE meeting_id=?",
                (meeting_id,),
            )
        }

    def notes_for(self, meeting_id, user_id) -> dict:
        """The inviter's notes with how they were made and whether a transcript is kept. Speaker
        labels are the provider's display names (untrusted text) while the participants are held."""
        with self._tx(write=False) as conn:
            row = self._owned(conn, meeting_id, user_id)
            labels = self._labels(conn, meeting_id)
            kept = self._keeps(conn, row)
            rows = conn.execute(
                "SELECT * FROM meeting_notes WHERE meeting_id=? ORDER BY created_at, rowid",
                (meeting_id,),
            ).fetchall()
            run = conn.execute(
                "SELECT state, code, attempts, notes, dropped, at FROM meeting_note_runs "
                "WHERE meeting_id=?",
                (meeting_id,),
            ).fetchone()
            segments = (
                {
                    r["seq"]: r["text"]
                    for r in conn.execute(
                        "SELECT seq, text FROM meeting_segments WHERE meeting_id=?", (meeting_id,)
                    )
                }
                if kept
                else {}
            )
        return {
            "notes": [self.note_view(r, labels, segments) for r in rows],
            "generation": dict(run) if run else None,
            "transcript_kept": bool(kept and segments),
        }

    @staticmethod
    def note_view(row, labels=None, segments=None) -> dict:
        evidence = json.loads(row["evidence"])
        ranges = []
        for part in evidence.get("ranges", []):
            entry = dict(part)
            if segments:  # the words themselves, only with a kept transcript
                entry["quote"] = [
                    segments[i] for i in range(part["from"], part["to"] + 1) if i in segments
                ]
            ranges.append(entry)
        speakers = [
            (labels or {}).get(ext, "a participant") for ext in evidence.get("speakers", [])
        ]
        return {
            "id": row["id"],
            "kind": row["kind"],
            "text": row["text"],
            "state": row["state"],
            "origin": "edited" if row["edited"] else "generated",
            "model": row["model"],
            "evidence": {"ranges": ranges, "speakers": speakers},
            "filed_ref": row["filed_ref"],
            "decided_at": row["decided_at"],
            "created_at": row["created_at"],
        }

    def note(self, meeting_id, note_id, user_id) -> dict:
        with self._tx(write=False) as conn:
            self._owned(conn, meeting_id, user_id)
            row = conn.execute(
                "SELECT * FROM meeting_notes WHERE id=? AND meeting_id=?", (note_id, meeting_id)
            ).fetchone()
        if row is None:
            raise _store().Missing
        return dict(row)

    def update_note(self, meeting_id, note_id, user_id, *, text=None, dismiss=False, now) -> dict:
        """Edit (the text is then the person's own) or dismiss a note nobody filed yet."""
        with self._tx() as conn:
            self._owned(conn, meeting_id, user_id)
            open_note = "AND state IN ('draft','proposed') AND filed_request_id IS NULL"
            if dismiss:
                changed = conn.execute(
                    "UPDATE meeting_notes SET decided_by=?, decided_at=?, state='dismissed' "
                    f"WHERE id=? AND meeting_id=? {open_note}",
                    (user_id, _stamp(now), note_id, meeting_id),
                ).rowcount
            else:
                changed = conn.execute(
                    "UPDATE meeting_notes SET text=?, edited=1 "
                    f"WHERE id=? AND meeting_id=? {open_note}",
                    (text, note_id, meeting_id),
                ).rowcount
            row = conn.execute(
                "SELECT * FROM meeting_notes WHERE id=? AND meeting_id=?", (note_id, meeting_id)
            ).fetchone()
            if row is None:
                raise _store().Missing
            if not changed:
                raise _refused("not_open", "That note was already filed or dismissed")
            return self.note_view(row, self._labels(conn, meeting_id))

    # --- filing: one claim per note, so a retried tap writes once -------------------------

    def claim_filing(self, meeting_id, note_id, user_id, request_id) -> tuple[str, dict]:
        """``claimed`` (this tap writes), ``resume`` (the same tap, retried after a crash),
        ``done`` (already filed: return it), or a refusal for a dismissed note or another tap that
        is mid-write."""
        with self._tx() as conn:
            self._owned(conn, meeting_id, user_id)
            row = conn.execute(
                "SELECT * FROM meeting_notes WHERE id=? AND meeting_id=?", (note_id, meeting_id)
            ).fetchone()
            if row is None:
                raise _store().Missing
            if row["filed_ref"]:
                return "done", dict(row)
            if row["state"] not in ("draft", "proposed"):
                raise _refused("not_open", "That note was dismissed")
            if row["filed_request_id"] is None:
                conn.execute(
                    "UPDATE meeting_notes SET filed_request_id=? WHERE id=? "
                    "AND filed_request_id IS NULL",
                    (request_id, note_id),
                )
                return "claimed", dict(row)
            if row["filed_request_id"] == request_id:
                return "resume", dict(row)
            raise _refused("filing_in_progress", "That note is being filed")

    def release_filing(self, note_id, request_id) -> None:
        """The write was refused for good: the note is open again."""
        with self._tx() as conn:
            conn.execute(
                "UPDATE meeting_notes SET filed_request_id=NULL WHERE id=? AND filed_request_id=? "
                "AND filed_ref IS NULL",
                (note_id, request_id),
            )

    def complete_filing(self, note_id, request_id, ref, user_id, now) -> dict:
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM meeting_notes WHERE id=?", (note_id,)).fetchone()
            if row is None:
                raise _store().Missing
            if not row["filed_ref"]:
                conn.execute(
                    "UPDATE meeting_notes SET filed_ref=?, state=?, decided_by=?, decided_at=? "
                    "WHERE id=? AND filed_request_id=? AND filed_ref IS NULL",
                    (ref, FILED_STATES[row["kind"]], user_id, _stamp(now), note_id, request_id),
                )
            row = conn.execute("SELECT * FROM meeting_notes WHERE id=?", (note_id,)).fetchone()
            return self.note_view(row, self._labels(conn, row["meeting_id"]))

    # --- deleting a meeting ---------------------------------------------------------------

    def delete_meeting(self, meeting_id, user_id) -> list[dict]:
        """Delete a finished meeting and everything it owns. What the inviter filed into memory
        or cards stays theirs and is returned, so they can delete it too."""
        with self._tx() as conn:
            row = self._owned(conn, meeting_id, user_id)
            if lifecycle.is_active(row["state"]):
                raise _refused("meeting_active", "The meeting is still running")
            filed = [
                {"note": r["id"], "kind": r["kind"], "ref": r["filed_ref"]}
                for r in conn.execute(
                    "SELECT id, kind, filed_ref FROM meeting_notes WHERE meeting_id=? "
                    "AND filed_ref IS NOT NULL ORDER BY created_at, rowid",
                    (meeting_id,),
                )
            ]
            conn.execute("DELETE FROM meeting_segments WHERE meeting_id=?", (meeting_id,))
            conn.execute("DELETE FROM meeting_participants WHERE meeting_id=?", (meeting_id,))
            conn.execute("DELETE FROM meeting_notes WHERE meeting_id=?", (meeting_id,))
            conn.execute("DELETE FROM meeting_note_runs WHERE meeting_id=?", (meeting_id,))
            conn.execute("DELETE FROM meeting_chat WHERE meeting_id=?", (meeting_id,))
            conn.execute("DELETE FROM meeting_outbox WHERE meeting_id=?", (meeting_id,))
            conn.execute("DELETE FROM meeting_events WHERE meeting_id=?", (meeting_id,))
            conn.execute("DELETE FROM meetings WHERE id=?", (meeting_id,))
            return filed
