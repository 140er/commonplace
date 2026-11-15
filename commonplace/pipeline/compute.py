"""How one pull request moved through the build and release flow: one pure function.

``compute_pr`` turns the recorded events of one PR into the shape the Pipeline view draws:
per step its state, start, end, duration and the wait before it, and per PR the total lead
time, the slowest step, the longest wait and the current step. It reads no clock, store or
file: the caller passes ``now`` and the events, so every edge case is a plain unit test.

Rules, in short (the full list is in ``docs/system/pr-pipeline-view.md``):

* A step's *run* ends at its first pass, fail or skipped event; an event after that starts a
  new run (a retry). Only the last run is shown, ``attempts`` counts the runs.
* ``start`` is the first running event of the run, else its first event; ``end`` is the
  run's pass, fail or skipped event. ``duration`` is end minus start, or the time so far for
  a running step.
* ``wait`` is the time from the end of the nearest earlier step that has events to the start
  of this step's *first* run (a retry's own gap is not a wait; ``span`` covers it). A step that
  started before that one ended has ``overlap`` and a wait of zero. The step after the last finished one carries an ongoing wait while the PR is open.
* The lead time runs from the card's start (else the first event) to the healthy deploy.
  A PR whose last step failed stops there: later steps stay pending and nothing is guessed.
* An event id seen twice counts once (the first, in recorded order).
* The card step comes from the board, not from the events. When the caller passes ``card``
  (the linked card's number, first claim time and, if known, the time its PR was linked) the
  step is built from it: running at the claim, passed when the PR opened (else when the PR
  was linked). ``card=None`` means no card is linked: the step is skipped, "No card", at the
  first event. Without the argument, recorded card events are used as they are. A card step
  from the board waits for the next step from its claim, and is not the slowest step (it
  spans the builder's work).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

STEPS = (
    "card",
    "builder",
    "pr_opened",
    "gate",
    "check",
    "merge",
    "deploy_snapshot",
    "deploy_rollback_image",
    "deploy_smoke",
    "deploy_health",
    "backup_proof",
)
LABELS = {
    "card": "Card",
    "builder": "Builder started",
    "pr_opened": "PR opened",
    "gate": "Gate",
    "check": "Check",
    "merge": "Merge into develop",
    "deploy_snapshot": "Deploy: snapshot",
    "deploy_rollback_image": "Deploy: rollback image",
    "deploy_smoke": "Deploy: smoke",
    "deploy_health": "Deploy: health",
    "backup_proof": "Backup proof",
}
STATES = ("pending", "queued", "running", "pass", "fail", "skipped")
TERMINAL = frozenset({"pass", "fail", "skipped"})
LEAD_END = "deploy_health"
MAX_FUTURE = timedelta(minutes=5)


def parse_at(value: Any) -> datetime:
    """An ISO 8601 time as an aware UTC datetime. A time without an offset counts as UTC."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and 10 <= len(value) <= 40:
        text = value.strip()
        if text[-1:] in "zZ":
            text = text[:-1] + "+00:00"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError("a time must be ISO 8601, such as 2026-11-15T10:30:00Z") from None
    else:
        raise ValueError("a time must be ISO 8601, such as 2026-11-15T10:30:00Z")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def stamp(moment: datetime | None) -> str | None:
    """The canonical text of a time: UTC with milliseconds, which sorts as text."""
    if moment is None:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + (
        f"{moment.microsecond // 1000:03d}Z"
    )


def _seconds(delta: timedelta) -> int:
    return max(0, int(delta.total_seconds()))


def _runs(events: list[dict]) -> list[list[dict]]:
    """Split one step's time-ordered events: every pass, fail or skipped ends a run."""
    runs: list[list[dict]] = []
    open_run = False
    for event in events:
        if not open_run:
            runs.append([])
            open_run = True
        runs[-1].append(event)
        if event["state"] in TERMINAL:
            open_run = False
    return runs


UNSET = object()
NO_CARD = "No card"


def _card_events(card, events: list[dict]) -> list[dict]:
    """The card step as events: from the card's claim to the PR opening, or 'No card'."""
    if card is None:
        first = events[0]["_at"] if events else None
        if first is None:
            return []
        return [
            {"id": "card:none", "step": "card", "state": "skipped", "at": first, "detail": NO_CARD}
        ]
    opened = [e for e in events if e["step"] == "pr_opened"]
    end = next((e["_at"] for e in opened if e["state"] == "pass"), None) or (
        opened[0]["_at"] if opened else None
    )
    if end is None and card.get("linked_at"):
        end = parse_at(card["linked_at"])
    start, detail = parse_at(card["claimed_at"]), None
    if end is not None and start > end:
        start, detail = end, "Card linked after the PR opened"
    ref = f"#/card/{card['number']}"
    out = [
        {
            "id": f"card:{card['number']}:running",
            "step": "card",
            "state": "running",
            "at": start,
            "ref": ref,
            "detail": detail,
        }
    ]
    if end is not None:
        out.append(
            {
                "id": f"card:{card['number']}:pass",
                "step": "card",
                "state": "pass",
                "at": end,
                "ref": ref,
                "detail": detail,
            }
        )
    return out


def _run_start(run: list[dict]) -> datetime:
    """The first running event of a run, else its first event."""
    return next((e for e in run if e["state"] == "running"), run[0])["_at"]


def _step(name: str, events: list[dict], now: datetime) -> dict:
    row: dict[str, Any] = {
        "step": name,
        "label": LABELS[name],
        "present": bool(events),
        "state": "pending",
        "attempts": 0,
        "events": len(events),
        "queued_at": None,
        "start": None,
        "end": None,
        "last_at": None,
        "duration": None,
        "first_start": None,
        "span": None,
        "running": False,
        "wait": None,
        "ref": None,
        "detail": None,
        "_start": None,
        "_end": None,
    }
    if not events:
        return row
    runs = _runs(events)
    run = runs[-1]
    last = run[-1]
    start = _run_start(run)
    first_start = _run_start(runs[0])
    queued = next((e for e in runs[0] if e["state"] == "queued"), None)
    end = last["_at"] if last["state"] in TERMINAL else None
    row.update(
        state=last["state"],
        attempts=len(runs),
        queued_at=stamp(queued["_at"]) if queued else None,
        first_start=stamp(first_start) if len(runs) > 1 else None,
        start=stamp(start),
        end=stamp(end),
        last_at=stamp(last["_at"]),
        ref=next((e["ref"] for e in reversed(run) if e.get("ref")), None),
        detail=next((e["detail"] for e in reversed(run) if e.get("detail")), None),
        _start=first_start,
        _end=end,
        _queued=queued["_at"] if queued else None,
    )
    if end is not None:
        row["duration"] = _seconds(end - start)
        row["span"] = _seconds(end - first_start) if len(runs) > 1 else None
    elif last["state"] == "running":
        row["duration"] = _seconds(now - start)
        row["running"] = True
    return row


def _clean(events: Iterable[Mapping], now: datetime) -> list[dict]:
    """Known steps and states only, each id once, in time order (recorded order breaks ties)."""
    seen: set[str] = set()
    kept = []
    for index, event in enumerate(events):
        if event.get("step") not in LABELS or event.get("state") not in STATES:
            continue
        key = str(event.get("id"))
        if key in seen:
            continue
        seen.add(key)
        try:
            at = parse_at(event.get("at"))
        except ValueError:
            continue
        kept.append(
            {
                **event,
                "_at": at,
                "_order": (event["seq"] if isinstance(event.get("seq"), int) else index),
            }
        )
    kept.sort(key=lambda e: (e["_at"], e["_order"]))
    return kept


def _wait_end(row: dict):
    """Where waiting for the step after this one starts: its end (a board card: its claim)."""
    return row.get("_wait_end") or row["_end"]


def _waits(rows: list[dict], now: datetime, open_pipeline: bool) -> None:
    """Fill each step's ``wait``: from the nearest earlier present step's end to its start."""
    previous = None
    last_present = max((i for i, row in enumerate(rows) if row["present"]), default=-1)
    for index, row in enumerate(rows):
        if row["present"]:
            if previous is not None and _wait_end(previous) is not None:
                gap = row["_start"] - _wait_end(previous)
                wait = {
                    "from_step": previous["step"],
                    "seconds": _seconds(gap),
                    "from": stamp(_wait_end(previous)),
                    "to": stamp(row["_start"]),
                    "queued_seconds": None,
                    "ongoing": False,
                    "overlap": gap < timedelta(0),
                }
                queued = row.get("_queued")
                if queued is not None and queued <= row["_start"]:
                    wait["queued_seconds"] = _seconds(row["_start"] - queued)
                if row["state"] in ("pending", "queued") and open_pipeline:
                    # Not started yet: the wait is still growing.
                    wait.update(
                        seconds=_seconds(now - _wait_end(previous)), to=stamp(now), ongoing=True
                    )
                row["wait"] = wait
            previous = row
        elif (
            index > last_present
            and previous is not None
            and _wait_end(previous) is not None
            and open_pipeline
        ):
            # The step after the last finished one: waiting for it to appear.
            row["wait"] = {
                "from_step": previous["step"],
                "seconds": _seconds(now - _wait_end(previous)),
                "from": stamp(_wait_end(previous)),
                "to": stamp(now),
                "queued_seconds": None,
                "ongoing": True,
                "overlap": False,
            }
            open_pipeline = False  # only the next step waits, not every step after it


def compute_pr(
    events: Iterable[Mapping],
    now: datetime,
    *,
    repo: str = "",
    pr: int = 0,
    title: str = "",
    card=UNSET,
) -> dict:
    """The pipeline of one PR from its events. ``now`` is the caller's clock (aware)."""
    now = parse_at(now)
    if card is UNSET:
        clean = _clean(events, now)
    else:
        base = _clean([e for e in events if e.get("step") != "card"], now)
        clean = _clean([*base, *_card_events(card, base)], now)
    by_step: dict[str, list[dict]] = {name: [] for name in STEPS}
    for event in clean:
        by_step[event["step"]].append(event)
    rows = [_step(name, by_step[name], now) for name in STEPS]
    linked = isinstance(card, Mapping)
    if linked and rows[0]["present"]:
        rows[0]["_wait_end"] = rows[0]["_start"]
    present = [row for row in rows if row["present"]]
    furthest = present[-1] if present else None
    failed = furthest is not None and furthest["state"] == "fail"
    health = rows[STEPS.index(LEAD_END)]
    done = health["state"] == "pass"
    state = "failed" if failed else "done" if done else "running"

    _waits(rows, now, open_pipeline=state == "running")

    # Lead time: card start (else the first event) to the healthy deploy.
    started = rows[0]["_start"] if rows[0]["present"] else (clean[0]["_at"] if clean else None)
    no_card = card is None and rows[0]["detail"] == NO_CARD
    lead = {"seconds": None, "in_progress": False, "from": None, "to": None, "basis": None}
    if started is not None:
        lead["basis"] = "card" if rows[0]["present"] and not no_card else "first_event"
        lead["from"] = stamp(started)
        if done:
            lead.update(seconds=_seconds(health["_end"] - started), to=health["end"])
        elif failed:
            end = furthest["_end"] or furthest["_start"]
            lead.update(seconds=_seconds(end - started), to=stamp(end))
        else:
            lead.update(seconds=_seconds(now - started), in_progress=True)

    timed = [r for r in rows if r["duration"] and not (linked and r["step"] == "card")]
    slowest = max(timed, key=lambda r: r["duration"], default=None)  # first of equals wins
    waits = [r for r in rows if r["wait"] and r["wait"]["seconds"] > 0]
    longest = max(waits, key=lambda r: r["wait"]["seconds"], default=None)

    if failed:
        current = furthest["step"]
    elif furthest is None:
        current = STEPS[0]
    elif furthest["state"] in TERMINAL:
        index = STEPS.index(furthest["step"]) + 1
        current = STEPS[index] if index < len(STEPS) else None
    else:
        current = furthest["step"]

    steps = [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]
    return {
        "repo": repo,
        "pr": pr,
        "title": title,
        "state": state,
        "current_step": current,
        "updated_at": stamp(clean[-1]["_at"]) if clean else None,
        "lead": lead,
        "slowest": {"step": slowest["step"], "seconds": slowest["duration"]} if slowest else None,
        "longest_wait": (
            {
                "before": longest["step"],
                "after": longest["wait"]["from_step"],
                "seconds": longest["wait"]["seconds"],
                "ongoing": longest["wait"]["ongoing"],
            }
            if longest
            else None
        ),
        "steps": steps,
    }
