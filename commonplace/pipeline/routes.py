"""``/api/pipeline``: record and read how pull requests move through the build and release flow.

    POST /api/pipeline/events          record events for one PR (idempotent by event id)
        {"repo": "owner/name", "pr": 12, "title": "...", "events": [
          {"id": "...", "step": "gate", "state": "pass", "at": "2026-11-15T10:30:00Z",
           "ref": "https://... or a short id", "detail": "short text"}]}
        -> 200 {"repo", "pr", "accepted": n, "duplicates": n}
    GET  /api/pipeline/prs?limit=20    {"prs": [...]} newest activity first (``?demo=1``: the example)
    GET  /api/pipeline/prs/<owner>/<name>/<pr>   one PR, 404 when nothing is recorded

Writing is for the deploy script's token (scope ``pipeline:write``, which the gateway allows
on this one route only) or the signed-in workspace owner. Reading is for any signed-in person
of the workspace, or an agent token with ``memory:read``. Everything is scoped to the caller's
workspace. The numbers come from ``compute.compute_pr``; the browser draws them. The ``card``
step is built from the board's card for the PR (``cards.py``), not from the events.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from commonplace import http_util
from commonplace.policy import PIPELINE_SCOPE

from . import cards, demo, store
from .compute import UNSET, compute_pr

logger = logging.getLogger(__name__)

PREFIX = "/api/pipeline"
MAX_LIMIT = 100


def _now() -> datetime:
    """The server clock; tests replace this one function."""
    return datetime.now(timezone.utc)


def _allowed(principal, write) -> bool:
    if principal.person:
        return principal.role == "owner" if write else True
    scope = PIPELINE_SCOPE if write else "memory:read"
    return principal.policy.has_scope(scope)


def _computed(gateway, principal, rows: list[dict], now: datetime) -> list[dict]:
    """Each stored PR as ``compute_pr`` answers it, with the card step from the board."""
    try:
        found = cards.cards_for(gateway, principal, [(row["repo"], row["pr"]) for row in rows])
    except Exception as exc:  # the board is not the pipeline's to break
        http_util.log_failure(logger, "pipeline card lookup failed", exc)
        found = None
    return [
        compute_pr(
            row["events"],
            now,
            repo=row["repo"],
            pr=row["pr"],
            title=row.get("title", ""),
            card=UNSET if found is None else found.get((row["repo"], row["pr"])),
        )
        for row in rows
    ]


def _limit(params: dict) -> int:
    raw = params.get("limit", "20")
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_LIMIT:
        raise store.Invalid(f"limit must be a whole number from 1 to {MAX_LIMIT}")
    return int(raw)


def install(gateway) -> None:
    def handle(h):
        principal = http_util.require_principal(gateway, h)
        if principal is None:
            return

        def send(status, body):
            http_util.send_json(h, status, body)

        write = h.command == "POST"
        if not _allowed(principal, write):
            send(
                403,
                {
                    "error": "the workspace owner or an agent connection with memory access can use this"
                },
            )
            return
        pipeline = store.open_pipeline(gateway, principal.workspace_id)
        now = _now()
        try:
            if write:
                try:
                    body = http_util.read_json(h)
                except ValueError:
                    return
                request = store.validate(body, now)
                result = pipeline.record(request, now)
                send(200, {"repo": request["repo"], "pr": request["pr"], **result})
                return
            parts = http_util.path_parts(h, PREFIX + "/prs")
            params = http_util.query(h)
            if not parts:
                limit = _limit(params)
                if params.get("demo") == "1":
                    rows = demo.prs(now)[:limit]
                    send(200, {"demo": True, "prs": rows})
                else:
                    rows = _computed(gateway, principal, pipeline.prs(limit), now)
                    send(200, {"demo": False, "prs": rows})
                return
            ref = _pr_ref(parts)
            if ref is None:
                send(404, {"error": "Not found"})
                return
            repo, number = ref
            if params.get("demo") == "1":
                row = next((r for r in demo.prs(now) if (r["repo"], r["pr"]) == ref), None)
            else:
                stored = pipeline.get(repo, number)
                row = _computed(gateway, principal, [stored], now)[0] if stored else None
            if row is None:
                send(404, {"error": "No events are recorded for that PR"})
            else:
                send(200, row)
        except store.Invalid as exc:
            send(400, {"error": str(exc)})
        except Exception as exc:
            http_util.log_failure(logger, "pipeline request failed", exc)
            send(500, {"error": "pipeline request failed"})

    gateway.add_route("POST", PREFIX + "/events", handle)
    gateway.add_route("GET", PREFIX + "/prs", handle)
    gateway.add_prefix_route("GET", PREFIX + "/prs/", handle)


def _pr_ref(parts):
    """``(repo, number)`` from ``owner/name/12`` or ``owner%2Fname/12``, else ``None``."""
    if len(parts) == 3:
        parts = ["/".join(parts[:2]), parts[2]]
    if len(parts) != 2 or not parts[1].isdigit() or "/" not in parts[0]:
        return None
    return parts[0], int(parts[1])
