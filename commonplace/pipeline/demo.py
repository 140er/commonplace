"""A fixed example of three pull requests, so the Pipeline view can be seen without live data.

``events(now)`` is deterministic: every time is an offset from ``now``, so the same ``now``
always gives the same events and the same computed numbers. The three PRs cover a finished
change, a change whose first gate run failed and was retried (still going), and a change that
has just started. Nothing here is stored; ``GET /api/pipeline/prs?demo=1`` serves it.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .compute import compute_pr, parse_at, stamp

REPO = "example/app"


class _Story:
    """Write one PR's events as a walk through time: ``at(seconds)`` moves, ``add`` records."""

    def __init__(self, pr: int, title: str, start: datetime):
        self.pr, self.title, self.clock, self.items = pr, title, start, []

    def wait(self, seconds: int):
        self.clock += timedelta(seconds=seconds)

    def add(self, step: str, state: str, ref: str | None = None, detail: str | None = None):
        n = len(self.items) + 1
        self.items.append(
            {
                "id": f"demo-{self.pr}-{n}",
                "step": step,
                "state": state,
                "at": stamp(self.clock),
                "ref": ref,
                "detail": detail,
            }
        )

    def run(
        self, step: str, seconds: int, end: str = "pass", queued: int = 0, ref=None, detail=None
    ):
        """A step that waits ``queued`` seconds in line, runs ``seconds`` and ends in ``end``."""
        if queued:
            self.add(step, "queued")
            self.wait(queued)
        self.add(step, "running")
        self.wait(seconds)
        self.add(step, end, ref=ref, detail=detail)


def _finished(now: datetime) -> _Story:
    s = _Story(101, "Example: a finished change", now - timedelta(hours=6))
    s.run("card", 60)
    s.wait(30)
    s.run("builder", 1500, queued=40)
    s.wait(25)
    s.add("pr_opened", "pass", ref=f"https://github.com/{REPO}/pull/101")
    s.wait(35)
    s.run("gate", 192, queued=40, ref="gate-verdict-101", detail="Verdict: PASS")
    s.wait(20)
    s.run("check", 365, queued=75, ref=f"https://github.com/{REPO}/pull/101/checks")
    s.wait(720)
    s.run("merge", 8)
    s.wait(150)
    s.run("deploy_snapshot", 40)
    s.run("deploy_rollback_image", 25)
    s.wait(5)
    s.run("deploy_smoke", 70)
    s.wait(4)
    s.run("deploy_health", 30)
    s.wait(120)
    s.run("backup_proof", 55, detail="Restore proof recorded")
    return s


def _retried(now: datetime) -> _Story:
    s = _Story(102, "Example: first gate failed, then passed", now - timedelta(minutes=40))
    s.run("card", 45)
    s.wait(20)
    s.run("builder", 1260, queued=30)
    s.wait(15)
    s.add("pr_opened", "pass", ref=f"https://github.com/{REPO}/pull/102")
    s.wait(30)
    s.run("gate", 160, end="fail", queued=35, detail="One finding to fix")
    s.wait(240)
    s.run("gate", 185, queued=20, ref="gate-verdict-102", detail="Verdict: PASS")
    s.wait(25)
    s.add("check", "queued")
    s.wait(55)
    s.add("check", "running")
    return s


def _started(now: datetime) -> _Story:
    s = _Story(103, "Example: a change that just started", now - timedelta(minutes=13))
    s.run("card", 50)
    s.wait(25)
    s.add("builder", "running")
    return s


def events(now: datetime) -> list[dict]:
    """Every example event, each with its PR, in one list."""
    now = parse_at(now)
    out = []
    for story in (_finished(now), _retried(now), _started(now)):
        out += [{"repo": REPO, "pr": story.pr, "title": story.title, **e} for e in story.items]
    return out


def prs(now: datetime) -> list[dict]:
    """The example PRs as ``compute_pr`` answers them, the most recent activity first."""
    now = parse_at(now)
    grouped: dict[int, list[dict]] = {}
    for event in events(now):
        grouped.setdefault(event["pr"], []).append(event)
    rows = [
        compute_pr(items, now, repo=REPO, pr=number, title=items[0]["title"])
        for number, items in grouped.items()
    ]
    return sorted(rows, key=lambda row: row["updated_at"], reverse=True)
