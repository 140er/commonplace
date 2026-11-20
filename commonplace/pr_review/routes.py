"""``/api/pr-review``: comments in, the loop's state out.

    POST /api/pr-review/comments                     a batch of review comments of one PR (feeder)
    GET  /api/pr-review/watch                        the PRs a feeder should read (feeder)
    GET  /api/pr-review/cards/<n>                    the comments, counts and history of a card's PR
    GET  /api/pr-review/cards/<n>/comments/<id>/original   one comment's stored words
    POST /api/pr-review/cards/<n>/decision           {"choice": "again|mine|dismiss"} (owner)
    GET  /api/pr-review/prs                          every PR with its counts and card (people)
    GET  /api/pr-review/boards/<slug>                the loop's setting for a board (people)
    PUT  /api/pr-review/boards/<slug>                {"enabled": bool, "rounds": 1-3} (owner)
    GET  /api/pr-review/people                       trusted reviewers and the Coworker's own logins
    PUT  /api/pr-review/people                       {"login": "...", "role": "trusted|own|null"} (owner)

Writing comments is for the feeder's own token (scope ``pr_review:write``, which the gateway
allows on the first two routes only) or the signed-in workspace owner. Every other route is for
signed-in people; agent tokens use the Coworker tools and get 403 here. A card the caller
cannot see, a card that does not exist and another workspace's card answer the same 404.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from commonplace import http_util
from commonplace.policy import PR_REVIEW_SCOPE

from . import intake, mapping, status as st, trigger
from .needs_me import CHOICES
from .store import LIMITS, Invalid, Limit, Refused, open_review

logger = logging.getLogger(__name__)

PREFIX = "/api/pr-review"
NOT_FOUND = {"error": "Card not found", "reason": "not_found"}


def _now() -> datetime:
    """The server clock; tests replace this one function."""
    return datetime.now(timezone.utc)


def _feeder(principal) -> bool:
    if principal.person:
        return principal.role == "owner"
    return principal.policy.has_scope(PR_REVIEW_SCOPE)


def _card_ref(parts):
    return int(parts[1]) if len(parts) >= 2 and parts[0] == "cards" and parts[1].isdigit() else None


def _board_view(store, slug):
    setting = store.board(slug)
    return {"board": slug, "enabled": setting["enabled"], "rounds": setting["rounds"], "limits": LIMITS}


def _card_view(gateway, principal, store, facts):
    repo = mapping.repo_of(facts)
    pr = facts.get("pr_number")
    base = {"card": facts["number"], "repo": repo, "pr": pr}
    setting = store.board(facts["board"])
    base["loop"] = {"enabled": setting["enabled"], "rounds": setting["rounds"]}
    if not repo or not pr:
        return {**base, "counts": {s: 0 for s in st.STATUSES}, "comments": [], "history": [], "pr_info": None}
    row = store.pr(repo, pr)
    return {
        **base,
        "pr_info": None
        if row is None
        else {
            "head": row["head"],
            "state": row["pr_state"],
            "fetched_at": row["fetched_at"],
            "rounds_used": row["rounds_used"],
            "rounds_allowed": min(setting["rounds"], row["rounds_allowed"]),
            "loop_stopped": row["loop_stopped"],
        },
        "counts": store.counts(repo, pr),
        "comments": store.comments(repo, pr),
        "history": store.history(repo, pr),
    }


def install(gateway) -> None:
    def handle(h):
        principal = http_util.require_principal(gateway, h)
        if principal is None:
            return

        def send(status, body):
            http_util.send_json(h, status, body)

        method = h.command
        parts = http_util.path_parts(h, PREFIX + "/")
        store = open_review(gateway, principal.workspace_id)
        workspace = principal.workspace_id
        try:
            if parts == ["comments"] and method == "POST":
                if not _feeder(principal):
                    return send(403, {"error": "the review-comment feeder or the workspace owner can post comments"})
                try:
                    body = http_util.read_json(h)
                except ValueError:
                    return
                now = _now()
                request = intake.validate(body, now)
                result = store.record(request, now)
                found = mapping.match(gateway, workspace, request["repo"], request["pr"])
                result["unmapped"] = 0 if found["card"] else len(request["comments"])
                _wake(gateway, workspace, now)
                return send(200, {"repo": request["repo"], "pr": request["pr"], **result})
            if parts == ["watch"] and method == "GET":
                if not _feeder(principal):
                    return send(403, {"error": "the review-comment feeder or the workspace owner can read this"})
                boards = store.enabled_boards()
                return send(200, {"prs": mapping.watched(gateway, workspace, boards)})
            if not principal.person:
                return send(
                    403,
                    {
                        "error": "Sign in as a person to use this page; Coworkers use the review tools.",
                        "reason": "person_only",
                    },
                )
            owner = principal.role == "owner"
            if parts == ["prs"] and method == "GET":
                return send(200, {"prs": _pr_list(gateway, principal, store)})
            if len(parts) == 2 and parts[0] == "boards":
                return _board(gateway, principal, store, parts[1], method, owner, h, send)
            if parts == ["people"]:
                return _people(principal, store, method, owner, h, send)
            number = _card_ref(parts)
            facts = (
                None
                if number is None
                else mapping.card_facts(gateway, workspace, number, principal=principal)
            )
            if facts is None:
                return send(404, NOT_FOUND)
            if len(parts) == 2 and method == "GET":
                return send(200, _card_view(gateway, principal, store, facts))
            if len(parts) == 5 and parts[2] == "comments" and parts[4] == "original" and method == "GET":
                repo, pr = mapping.repo_of(facts), facts.get("pr_number")
                original = store.original(int(parts[3])) if parts[3].isdigit() else None
                if original is None or (original["repo"], original["pr"]) != (repo, pr):
                    return send(404, NOT_FOUND)
                return send(200, {"id": int(parts[3]), "text": original["body"]})
            if parts[2:] == ["decision"] and method == "POST":
                if not owner:
                    return send(403, {"error": "Only a workspace owner can decide this.", "reason": "forbidden"})
                try:
                    body = http_util.read_json(h)
                except ValueError:
                    return
                choice = body.get("choice") if isinstance(body, dict) else None
                choice = CHOICES.get(choice, choice)
                repo, pr = mapping.repo_of(facts), facts.get("pr_number")
                if not repo or not pr:
                    return send(404, NOT_FOUND)
                shown = store.decide(repo, pr, choice, f"user:{principal.user_id}", _now())
                if choice == "again":
                    _wake(gateway, workspace, _now())
                return send(200, shown)
            return send(404, {"error": "Not found", "reason": "not_found"})
        except (Invalid, intake.Invalid) as exc:
            send(400, {"error": str(exc), "reason": "invalid"})
        except Limit:
            send(429, {"error": st.REASONS["intake_limit"][0], **st.reason_fields("intake_limit")})
        except Refused as exc:
            send(404 if exc.code == "not_found" else 409, {"error": str(exc), "reason": exc.code})
        except Exception as exc:
            http_util.log_failure(logger, "review comment request failed", exc)
            send(500, {"error": "review comment request failed"})

    gateway.add_route("POST", PREFIX + "/comments", handle)
    gateway.add_route("GET", PREFIX + "/watch", handle)
    gateway.add_route("GET", PREFIX + "/prs", handle)
    for method in ("GET", "PUT"):
        gateway.add_route(method, PREFIX + "/people", handle)
    for method in ("GET", "POST", "PUT"):
        gateway.add_prefix_route(method, PREFIX + "/", handle)


def _wake(gateway, workspace, now):
    """Run the trigger right after intake or a decision; its own failures never fail the request."""
    try:
        trigger.run(gateway, workspace, now)
    except Exception as exc:
        http_util.log_failure(logger, "review comment trigger failed", exc)


def _pr_list(gateway, principal, store):
    out = []
    for row in store.prs():
        found = mapping.match(gateway, principal.workspace_id, row["repo"], row["pr"], principal=principal)
        counts = store.counts(row["repo"], row["pr"])
        out.append(
            {
                "repo": row["repo"],
                "pr": row["pr"],
                "card": found["card"],
                "card_reason": found["reason"],
                "candidates": found["candidates"],
                "counts": counts,
                "fetched_at": row["fetched_at"],
            }
        )
    return out


def _board(gateway, principal, store, slug, method, owner, h, send):
    if method == "GET":
        return send(200, _board_view(store, slug))
    if method != "PUT" or not owner:
        return send(403, {"error": "Only a workspace owner can change this.", "reason": "forbidden"})
    try:
        body = http_util.read_json(h)
    except ValueError:
        return
    if not isinstance(body, dict) or body.keys() - {"enabled", "rounds"} or not isinstance(body.get("enabled"), bool):
        raise Invalid("send enabled (true or false) and optionally rounds")
    setting = store.set_board(slug, body["enabled"], principal.user_id, body.get("rounds"), _now())
    send(200, {k: setting[k] for k in ("board", "enabled", "rounds")})


def _people(principal, store, method, owner, h, send):
    if method == "GET":
        return send(200, {"people": store.people()})
    if method != "PUT" or not owner:
        return send(403, {"error": "Only a workspace owner can change this.", "reason": "forbidden"})
    try:
        body = http_util.read_json(h)
    except ValueError:
        return
    if not isinstance(body, dict) or body.keys() - {"login", "role"} or "login" not in body:
        raise Invalid("send login and role (trusted, own or null)")
    store.set_person(body["login"], body.get("role"), f"user:{principal.user_id}", _now())
    send(200, {"people": store.people()})
