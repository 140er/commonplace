"""Email a person once per new Today item, in small batches.

Only people who turned it on get mail. What a person is told about is exactly what
their own Today list holds (``needs_me`` applies the same read rules as the app),
so nobody hears about an item they cannot read. The mail carries the card number,
its title and a link, never the card body.
"""

import logging
import threading
import time
from contextlib import contextmanager

from commonplace import events, mailer, sessions, urls
from commonplace.boards import service
from commonplace.extensions import RetryableToolError, ToolContext, ToolError

logger = logging.getLogger(__name__)

# At most this many mails per person in any hour; a burst is one mail listing the items.
MAX_PER_HOUR = 3
MAX_LISTED = 10
WINDOW_SECONDS = 3600
_BUCKETS = ("asks", "done_review", "backlog_from_agents", "plans", "approvals")
_SUBJECT_TITLE = 80
# How long closing the database waits for a pass that is mid-flight, like email sign-in.
SHUTDOWN_WAIT = 1.0


class _Halted(Exception):
    """The notifier was stopped: the pass ends without touching the database again."""


def _line(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class TodayEmail:
    def __init__(self, gateway, mail, *, clock=time.time, sweep_seconds=300, settle_seconds=5):
        self.gateway, self.db, self.mailer = gateway, gateway.db, mail
        self.clock, self.sweep_seconds, self.settle_seconds = clock, sweep_seconds, settle_seconds
        self._work = threading.RLock()
        self._stop, self._wake = threading.Event(), threading.Event()
        self.thread = threading.Thread(
            target=self._run, name="commonplace-today-email", daemon=True
        )

    @property
    def available(self) -> bool:
        return bool(self.mailer.configured)

    def start(self):
        events.add_listener(self.on_event)
        self._wake.set()
        self.thread.start()

    def stop(self):
        events.remove_listener(self.on_event)
        self._stop.set()
        self._wake.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=SHUTDOWN_WAIT)

    def on_event(self, event):
        if event["kind"].startswith("card."):
            self._wake.set()

    def _run(self):
        while not self._stop.is_set():
            self._wake.wait(timeout=self.sweep_seconds)
            if self._stop.is_set():
                break
            # Let a burst of card changes settle so it goes out as one mail.
            self._stop.wait(self.settle_seconds)
            if self._stop.is_set():
                break  # Stopped while settling: the database may be closing, so no pass.
            self._wake.clear()
            try:
                self.reconcile(halt=self._stop)
            except Exception as exc:
                logger.warning("today email pass failed: %s", type(exc).__name__)

    # -- per-person state ---------------------------------------------------------

    def today(self, principal) -> dict[int, str]:
        """The needs-you items this person can read, number to title.

        A refusal means this person has no Today list to read (empty). A busy or paused
        memory is not an empty Today: it raises, so nothing is forgotten or seeded from it."""
        try:
            report = service.BoardService(
                ToolContext(principal=principal, gateway=self.gateway)
            ).needs_me()
        except RetryableToolError:
            raise
        except ToolError:
            return {}
        items: dict[int, str] = {}
        for bucket in _BUCKETS:
            for card in report.get(bucket, ()):
                items.setdefault(card["number"], card.get("title") or "")
        return items

    def enabled(self, principal) -> bool:
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT enabled FROM today_email_prefs WHERE workspace_id=? AND user_id=?",
                (principal.workspace_id, principal.user_id),
            ).fetchone()
        return bool(row and row["enabled"])

    def set_enabled(self, principal, on: bool):
        # What is in Today now is already known to the person: only later items send.
        seed = self.today(principal) if on else {}
        with self.db._lock, self.db._tx() as conn:
            key = (principal.workspace_id, principal.user_id)
            conn.execute(
                "INSERT INTO today_email_prefs(workspace_id,user_id,enabled,updated_at) "
                "VALUES (?,?,?,?) ON CONFLICT(workspace_id,user_id) DO UPDATE SET "
                "enabled=excluded.enabled, updated_at=excluded.updated_at",
                (*key, int(on), events._now()),
            )
            conn.execute("DELETE FROM today_email_items WHERE workspace_id=? AND user_id=?", key)
            conn.executemany(
                "INSERT INTO today_email_items(workspace_id,user_id,number,noted_at) "
                "VALUES (?,?,?,?)",
                [(*key, number, events._now()) for number in seed],
            )

    # -- sending ------------------------------------------------------------------

    @contextmanager
    def _sql(self, halt):
        """The database lock, refused once ``halt`` is set.

        The check and the statements run under the lock that closing the database also
        takes, so a stopped pass cannot slip a query in after the connection is gone."""
        with self.db._lock:
            if halt is not None and halt.is_set():
                raise _Halted
            yield

    def reconcile(self, halt=None) -> int:
        """One pass over everyone who opted in; returns the mails sent.

        The background thread passes its stop event as ``halt``; a stopped pass ends at
        its next database step. Callers that drive a pass by hand pass nothing."""
        if not self.available:
            return 0
        sent = 0
        try:
            with self._work:
                with self._sql(halt):
                    people = self.db._conn.execute(
                        "SELECT workspace_id, user_id FROM today_email_prefs WHERE enabled=1"
                    ).fetchall()
                for person in people:
                    try:
                        sent += self._person(person["workspace_id"], person["user_id"], halt)
                    except _Halted:
                        raise
                    except Exception as exc:
                        logger.warning("today email failed for one person: %s", type(exc).__name__)
        except _Halted:
            pass
        return sent

    def _person(self, workspace_id, user_id, halt=None) -> int:
        with self._sql(halt):
            principal = sessions.person_principal(self.db, user_id, workspace_id)
            if principal is None:
                return 0
            today = self.today(principal)
        key = (workspace_id, user_id)
        now = self.clock()
        with self._sql(halt):
            noted = {
                row["number"]
                for row in self.db._conn.execute(
                    "SELECT number FROM today_email_items WHERE workspace_id=? AND user_id=?", key
                )
            }
            recent = self.db._conn.execute(
                "SELECT COUNT(*) FROM today_email_sends "
                "WHERE workspace_id=? AND user_id=? AND sent_at>?",
                (*key, now - WINDOW_SECONDS),
            ).fetchone()[0]
        fresh = [number for number in today if number not in noted]
        with self._sql(halt), self.db._tx() as conn:
            for number in noted - set(today):  # Left Today: a later return is new again.
                conn.execute(
                    "DELETE FROM today_email_items WHERE workspace_id=? AND user_id=? AND number=?",
                    (*key, number),
                )
            conn.execute("DELETE FROM today_email_sends WHERE sent_at<=?", (now - WINDOW_SECONDS,))
        if not fresh or recent >= MAX_PER_HOUR:
            return 0  # Over the cap: the items stay un-noted and go out in a later mail.
        with self._sql(halt):
            subject, text = self._message(workspace_id, sorted(fresh), today)
        # A failed send counts against the cap so a broken server is not hammered.
        with self._sql(halt), self.db._tx() as conn:
            conn.execute(
                "INSERT INTO today_email_sends(workspace_id,user_id,sent_at) VALUES (?,?,?)",
                (*key, now),
            )
        try:
            self.mailer.send(principal.user_email, subject, text)
        except mailer.MailError:
            logger.warning("today email not sent")
            return 0
        with self._sql(halt), self.db._tx() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO today_email_items(workspace_id,user_id,number,noted_at) "
                "VALUES (?,?,?,?)",
                [(*key, number, events._now()) for number in fresh],
            )
        return 1

    def _message(self, workspace_id, numbers, today):
        shown = numbers[:MAX_LISTED]
        # Stable links that open in this person's workspace, like every other shared link.
        card_paths = {number: f"/app/#/card/{number}" for number in shown}
        links = urls.share_links(
            self.db, workspace_id, [*card_paths.values(), "/app/#/", "/app/#/settings"]
        )
        if len(numbers) == 1:
            subject = f"New in Today: {_line(today[numbers[0]], _SUBJECT_TITLE)}"
        else:
            subject = f"{len(numbers)} new items in Today"
        lines = ["Waiting on you:", ""]
        for number in shown:
            lines += [
                f"- #{number} {_line(today[number], 200)}",
                f"  {links[card_paths[number]]}",
            ]
        if len(numbers) > len(shown):
            lines.append(f"- and {len(numbers) - len(shown)} more")
        lines += [
            "",
            f"Open Today: {links['/app/#/']}",
            "",
            "You turned these emails on. Turn them off in Settings, under Email for Today:",
            links["/app/#/settings"],
        ]
        return subject, "\n".join(lines) + "\n"
