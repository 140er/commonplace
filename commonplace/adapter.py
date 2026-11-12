"""Reference agent adapter: how any agent takes part in a board.

The protocol is in ``docs/agent-adapter.md``. An agent needs a connection token and
four calls: take work, heartbeat while it holds the card, report progress, and report
the result (or give the card back). This module is that protocol over the canonical
MCP tools, with nothing specific to one agent type; ``tools`` is a
:class:`commonplace.client.Client` or anything with the same ``call_tool`` and
``write_tool`` methods.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from .client import ClientError, ToolRefused

# Heartbeat after this share of the claim has passed: 20 minutes of a 30-minute claim.
HEARTBEAT_FRACTION = 2 / 3
NOTHING_TO_TAKE = "nothing to take"

_TAKEN = re.compile(r"#([1-9][0-9]*) ")
_UNTIL = re.compile(r" until ([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z)")
_OK = re.compile(r"ok #([1-9][0-9]*) ([a-z]+)")


class AdapterError(ClientError):
    """The hub's reply did not follow the documented protocol."""


class ClaimLost(AdapterError):
    """The hub refused the holder's heartbeat: stop working on this card."""


@dataclass(frozen=True)
class Claim:
    """One held card. ``until`` is the claim deadline; ``text`` is the hub's full reply."""

    number: int
    title: str
    body: str
    until: datetime | None
    renewed_at: datetime
    text: str = ""

    def heartbeat_at(self) -> datetime | None:
        """When the next heartbeat is due, or None for a claim without a deadline."""
        if self.until is None:
            return None
        return self.renewed_at + (self.until - self.renewed_at) * HEARTBEAT_FRACTION

    def heartbeat_due(self, now: datetime) -> bool:
        due = self.heartbeat_at()
        return due is not None and now >= due

    def expired(self, now: datetime) -> bool:
        """Past the deadline: the card shows as expired until its holder renews it."""
        return self.until is not None and now >= self.until


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _deadline(line):
    found = _UNTIL.search(line)
    if found is None:
        return None
    return datetime.strptime(found[1], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _quoted(text, start):
    try:
        value, end = json.JSONDecoder().raw_decode(text, start)
    except (ValueError, RecursionError):
        raise AdapterError("Invalid card reply from Commonplace") from None
    if not isinstance(value, str):
        raise AdapterError("Invalid card reply from Commonplace")
    return value, end


def parse_claim(text: str, now: datetime) -> Claim | None:
    """The claim in a ``task_take_next`` reply; None when there was nothing to take."""
    if text == NOTHING_TO_TAKE:
        return None
    # Quoted values carry no raw line feed, so the reply splits on LF only.
    lines = text.split("\n")
    head = _TAKEN.match(lines[0])
    if head is None:
        raise AdapterError("Invalid card reply from Commonplace")
    title, end = _quoted(lines[0], head.end())
    body = [line for line in lines[1:] if line.startswith("body: ")]
    if not lines[0].startswith("  yours", end) or len(body) != 1:
        raise AdapterError("Invalid card reply from Commonplace")
    return Claim(
        number=int(head[1]),
        title=title,
        body=_quoted(body[0], len("body: "))[0],
        until=_deadline(lines[0][end:]),
        renewed_at=now,
        text=text,
    )


def _acknowledged(text, claim):
    lines = text.split("\n")
    found = _OK.match(lines[0])
    if found is None or int(found[1]) != claim.number:
        raise AdapterError("Invalid card reply from Commonplace")
    return found[2], lines[0]


class AgentAdapter:
    """Take, heartbeat, report and finish board work for one connection."""

    def __init__(self, tools, board: str | None = None, clock=_now):
        self._tools = tools
        self._board = board
        self._clock = clock

    def _scope(self, **arguments):
        if self._board is not None:
            arguments["board"] = self._board
        return arguments

    def connect(self) -> str:
        """Prove the credential and read the workspace rules and context."""
        return self._tools.call_tool("session_start", self._scope())

    def take(self, task: int | None = None) -> Claim | None:
        """Claim the next ready card, or one card by number; None when nothing is ready."""
        arguments = self._scope() if task is None else self._scope(task=task)
        return parse_claim(self._tools.write_tool("task_take_next", arguments), self._clock())

    def heartbeat(self, claim: Claim) -> Claim:
        """Renew the claim, also after it expired. :class:`ClaimLost` means stop."""
        try:
            text = self._tools.call_tool("task_heartbeat", {"task": claim.number})
        except ToolRefused:
            raise ClaimLost("Commonplace card is no longer held by this agent") from None
        return self._renewed(claim, text)

    def beat(self, claim: Claim) -> Claim:
        """Heartbeat only when one is due; call this from the agent's work loop."""
        return self.heartbeat(claim) if claim.heartbeat_due(self._clock()) else claim

    def progress(self, claim: Claim, status_line: str) -> Claim:
        """Report what the agent is doing now; a holder's write also renews the claim."""
        text = self._tools.write_tool(
            "task_update", {"task": claim.number, "status_line": status_line}
        )
        return self._renewed(claim, text)

    def done(self, claim: Claim, status_line: str | None = None) -> str:
        """Report the result. Returns the card's status: ``done``, or ``review`` on a
        board whose completions wait for a reviewer."""
        arguments = {"task": claim.number, "status": "done"}
        if status_line is not None:
            arguments["status_line"] = status_line
        return _acknowledged(self._tools.write_tool("task_update", arguments), claim)[0]

    def release(self, claim: Claim, note: str) -> str:
        """Give an unfinished card back to the queue for any agent; returns its status."""
        text = self._tools.write_tool(
            "task_handoff", {"task": claim.number, "to": "anyone", "note": note}
        )
        return _acknowledged(text, claim)[0]

    def work(self, run, task: int | None = None) -> tuple[Claim, str] | None:
        """Take one card, run ``run(claim, beat)`` and report it done.

        ``run`` calls ``beat()`` between its own steps; it returns a status line or
        None. An exception from ``run`` leaves the card held, so the agent can resume
        or release it. Returns the claim and the card's status, or None when nothing
        was ready."""
        claim = self.take(task)
        if claim is None:
            return None
        held = [claim]

        def beat():
            held[0] = self.beat(held[0])
            return held[0]

        status_line = run(claim, beat)
        return held[0], self.done(held[0], status_line)

    def _renewed(self, claim, text):
        _status, line = _acknowledged(text, claim)
        return replace(claim, until=_deadline(line), renewed_at=self._clock())
