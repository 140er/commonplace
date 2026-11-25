"""The review-comment feeder: read a pull request's comments on GitHub, post them to the hub.

It runs outside the hub (on the production host or on a runner machine) with two credentials of
its own, each read from a file the workspace owner created there:

- a read-only GitHub token (fine-grained, pull requests and contents: read) for the watched
  repositories. Until that file exists the feeder reads nothing and says what it waits for;
- the hub's feeder token (the single scope ``pr_review:write``), which reaches only
  ``GET /api/pr-review/watch`` and ``POST /api/pr-review/comments``.

Each pass asks the hub which PRs to watch, reads each PR, its inline comments, its reviews and
its conversation comments (conditional requests: an unchanged list costs no rate limit), the
changed files of a new head, and posts what it read. A comment that was seen before and is gone
is posted as removed. GitHub's rate limit is respected: below a floor the pass stops until the
reset time. The feeder never writes to GitHub. Logs carry repositories, PR numbers and counts,
never a comment's words or a token.
"""

from __future__ import annotations

import argparse
import copy
import http.client
import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
FAST_S = 120  # a PR with comment activity in the last hour is read every 2 minutes
SLOW_S = 600  # otherwise every 10 minutes
IDLE_AFTER_S = 3600
RATE_FLOOR = 50  # stop a pass when GitHub has fewer requests left than this
MAX_PAGES = 10  # 100 items a page
MAX_FILES_PAGES = 3  # the changed files of a head, up to 300 (GitHub's own page size cap)
MAX_BATCH = 100
MAX_BATCH_BYTES = 900_000
MAX_RESPONSE = 8 * 1024 * 1024
TIMEOUT_S = 20
WAITING_EXIT = 3
_ENDPOINTS = {
    "inline": "pulls/{n}/comments",
    "review": "pulls/{n}/reviews",
    "conversation": "issues/{n}/comments",
}


class _PassSummary(TypedDict):
    prs: int
    requests: int
    comments: int
    skipped: int
    rate_limited: bool
    errors: list[str]


class FeederError(Exception):
    """A pass that cannot go on, with a fixed ``code`` (never a response body or a token)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class RateLimited(FeederError):
    def __init__(self, reset: float):
        super().__init__("feed_rate_limited")
        self.reset = reset


def _json(raw: bytes, code: str):
    """A JSON answer, or ``FeederError(code)``: a malformed answer never stops the feeder."""
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise FeederError(code) from None


def _items(raw: bytes) -> list:
    """A GitHub list of objects; anything else is a bad answer for this PR."""
    items = _json(raw, "github_bad_answer")
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        raise FeederError("github_bad_answer")
    return items


def read_token(path) -> str | None:
    """The one-line secret in ``path``, or ``None`` when the file is missing or empty."""
    try:
        text = Path(path).expanduser().read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    if not text:
        return None
    if "\n" in text or any(ord(ch) < 33 for ch in text):
        raise FeederError("token_file_unreadable")
    return text


def _iso(moment: float) -> str:
    return datetime.fromtimestamp(moment, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Transport:
    """Bounded HTTP for both sides. ``(status, headers, raw)``; never raises on a status."""

    def __init__(self):
        self._opener = build_opener(ProxyHandler({}))

    def request(self, method, url, *, token, body=None, headers=None):
        sent = {"User-Agent": "Commonplace review-comment feeder", **(headers or {})}
        sent["Authorization"] = "Bearer " + token
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            sent["Content-Type"] = "application/json"
        try:
            response = self._opener.open(
                Request(url, data=data, headers=sent, method=method), timeout=TIMEOUT_S
            )
        except HTTPError as exc:
            response = exc
        except (URLError, OSError, http.client.HTTPException):
            raise FeederError("unreachable") from None
        with response:
            status = response.code if isinstance(response, HTTPError) else response.status
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise FeederError("response_too_large")
            return status, {k.lower(): v for k, v in response.headers.items()}, raw


def _author(item) -> dict:
    user = item.get("user") or {}
    return {
        "login": str(user.get("login") or "ghost"),
        "type": user.get("type") if user.get("type") in ("User", "Bot", "Organization") else "User",
        "association": item.get("author_association") or None,
    }


def comment_from(kind: str, item: dict) -> dict | None:
    """One GitHub item as an intake comment, or ``None`` for one that carries no words yet
    (a pending review, a review without a body)."""
    body = item.get("body") or ""
    if kind == "review":
        if item.get("state") == "PENDING" or not body.strip() or not item.get("submitted_at"):
            return None
        created = updated = item["submitted_at"]
    else:
        created, updated = item.get("created_at"), item.get("updated_at") or item.get("created_at")
    out = {
        "kind": kind,
        "id": str(item["id"]),
        "author": _author(item),
        "body": body,  # verbatim; GitHub itself caps a comment at 65,536 characters
        "created_at": created,
        "updated_at": updated,
    }
    if kind == "inline":
        reply_to = item.get("in_reply_to_id")
        out.update(
            thread=str(reply_to or item["id"]),
            in_reply_to=str(reply_to) if reply_to else None,
            path=item.get("path"),
            line=item.get("line") or item.get("original_line"),
            commit=item.get("commit_id"),
        )
    if kind == "review":
        out["review_state"] = item.get("state")
    return out


def _removed(kind: str, ident: str, seen: dict) -> dict:
    """A comment seen before and gone now: posted once as removed, with its stored author."""
    return {
        "kind": kind,
        "id": ident,
        "author": seen["author"],
        "body": "",
        "created_at": seen["created_at"],
        "updated_at": seen["created_at"],
        "removed": True,
    }


def batches(base: dict, comments: list, files: list | None) -> list[dict]:
    """The requests for one read: at most 100 comments and about 900 kB each; the first one
    carries the changed files of the head. A read with no comments is one request."""
    chunk: list[dict]
    out, chunk, size = [], [], 0
    for comment in comments:
        cost = len(json.dumps(comment))
        if chunk and (len(chunk) == MAX_BATCH or size + cost > MAX_BATCH_BYTES):
            out.append(chunk)
            chunk, size = [], 0
        chunk.append(comment)
        size += cost
    out.append(chunk)
    requests = []
    for number, part in enumerate(out):
        request = {**base, "comments": part}
        if number == 0 and files:
            request["changed_files"] = files
        requests.append(request)
    return requests


class Feeder:
    """One feeder: a hub, its token, a GitHub token, and the state file of what it has seen."""

    def __init__(
        self,
        hub: str,
        hub_token: str,
        github_token: str,
        state_path,
        *,
        api: str = GITHUB_API,
        transport=None,
        clock=time.time,
    ):
        self.hub = hub.rstrip("/")
        self.api = api.rstrip("/")
        self._hub_token = hub_token
        self._github_token = github_token
        self.state_path = Path(state_path).expanduser()
        self.transport = transport or Transport()
        self.clock = clock
        self.state = self._load()

    # -- state -------------------------------------------------------------------------

    def _load(self) -> dict:
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            state = {}
        state.setdefault("prs", {})
        return state

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(
            prefix=self.state_path.name + ".", suffix=".tmp", dir=self.state_path.parent
        )
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.state, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.state_path)
        finally:
            temporary.unlink(missing_ok=True)

    # -- the two sides -----------------------------------------------------------------

    def watch(self) -> list[dict]:
        status, _, raw = self.transport.request(
            "GET", self.hub + "/api/pr-review/watch", token=self._hub_token
        )
        if status in (401, 403):
            raise FeederError("hub_refused")
        if status != 200:
            raise FeederError("hub_unavailable")
        answer = _json(raw, "hub_bad_answer")
        prs = answer.get("prs") if isinstance(answer, dict) else None
        if not isinstance(prs, list):
            raise FeederError("hub_bad_answer")
        # An entry that is not a repository and a whole PR number is left out, never fatal.
        return [
            p
            for p in prs
            if isinstance(p, dict)
            and isinstance(p.get("repo"), str)
            and isinstance(p.get("pr"), int)
            and not isinstance(p.get("pr"), bool)
            and p["pr"] > 0
        ]

    def post(self, request: dict) -> dict:
        status, _, raw = self.transport.request(
            "POST", self.hub + "/api/pr-review/comments", token=self._hub_token, body=request
        )
        if status in (401, 403):
            raise FeederError("hub_refused")
        if status != 200:
            # The hub's answer names a field, never a comment's words; only the status is logged.
            raise FeederError(f"hub_status_{status}")
        return _json(raw, "hub_bad_answer")

    def _github(self, path: str, etag: str | None = None):
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if etag:
            headers["If-None-Match"] = etag
        url = path if path.startswith(("http://", "https://")) else f"{self.api}/{path}"
        status, response_headers, raw = self.transport.request(
            "GET", url, token=self._github_token, headers=headers
        )
        self._rate(response_headers)
        if status == 401:
            raise FeederError("github_token_refused")
        return status, response_headers, raw

    def _rate(self, headers) -> None:
        remaining, reset = headers.get("x-ratelimit-remaining"), headers.get("x-ratelimit-reset")
        if remaining is not None and remaining.isdigit() and int(remaining) < RATE_FLOOR:
            when = float(reset) if reset and reset.isdigit() else self.clock() + 60
            self.state["rate_reset"] = when
            raise RateLimited(when)

    def _next(self, headers) -> str | None:
        """The next page of a list, from the ``Link`` header, on the same host only."""
        for part in (headers.get("link") or "").split(","):
            if 'rel="next"' in part and "<" in part and ">" in part:
                url = part[part.index("<") + 1 : part.index(">")]
                if urlsplit(url).netloc == urlsplit(self.api).netloc:
                    return url
        return None

    def _list(self, path: str, etag: str | None, pages: int):
        """``(changed, items, etag, complete)`` of a list: ``changed`` is False on a 304."""
        status, headers, raw = self._github(path + "?per_page=100", etag)
        if status == 304:
            return False, [], etag, True
        if status != 200:
            raise FeederError("github_not_found" if status == 404 else f"github_status_{status}")
        first_etag, items, count = headers.get("etag"), _items(raw), 1
        url = self._next(headers)
        while url and count < pages:
            status, headers, raw = self._github(url)
            if status != 200:
                raise FeederError(f"github_status_{status}")
            items += _items(raw)
            url, count = self._next(headers), count + 1
        return True, items, first_etag, url is None

    # -- one PR ------------------------------------------------------------------------

    def read_pr(self, repo: str, number: int) -> tuple[list[dict], dict]:
        """``(requests, state)`` for one PR as it is now. The new state of the PR is only kept
        by the caller once every request was posted, so a failed post is read again next time."""
        key = f"{repo}#{number}"
        mine = copy.deepcopy(self.state["prs"].get(key) or {"etags": {}, "seen": {}})
        base = f"repos/{quote(repo, safe='/')}/"
        status, headers, raw = self._github(base + f"pulls/{number}", mine["etags"].get("pr"))
        if status == 200:
            pull = _json(raw, "github_bad_answer")
            if not isinstance(pull, dict):
                raise FeederError("github_bad_answer")
            head, repository = pull.get("head") or {}, (pull.get("base") or {}).get("repo") or {}
            mine["pr"] = {
                "head": head.get("sha"),
                "branch": head.get("ref"),
                "head_repo": (head.get("repo") or {}).get("full_name"),
                "pr_state": "merged" if pull.get("merged") else pull.get("state", "open"),
                "public": not repository.get("private", True),
            }
            mine["etags"]["pr"] = headers.get("etag")
        elif status != 304 or "pr" not in mine:
            raise FeederError("github_not_found" if status == 404 else f"github_status_{status}")
        info = mine["pr"]
        comments, latest = [], mine.get("activity")
        for kind, endpoint in _ENDPOINTS.items():
            changed, items, etag, complete = self._list(
                base + endpoint.format(n=number), mine["etags"].get(kind), MAX_PAGES
            )
            if not changed:
                continue
            mine["etags"][kind] = etag
            seen = mine["seen"].setdefault(kind, {})
            now_ids = set()
            for item in items:
                comment = comment_from(kind, item)
                if comment is None:
                    continue
                comments.append(comment)
                now_ids.add(comment["id"])
                seen[comment["id"]] = {
                    "author": comment["author"],
                    "created_at": comment["created_at"],
                }
                latest = max(filter(None, (latest, comment["updated_at"])), default=None)
            if complete:  # only a whole list can tell that a comment is gone
                for gone in sorted(set(seen) - now_ids):
                    comments.append(_removed(kind, gone, seen.pop(gone)))
        mine["activity"] = latest
        files = None
        if info["head"] and info["head"] != mine.get("files_head"):
            changed, items, _, complete = self._list(
                base + f"pulls/{number}/files", None, MAX_FILES_PAGES
            )
            if changed and complete:
                files = [str(i.get("filename")) for i in items if i.get("filename")]
                mine["files_head"] = info["head"]
        request = {
            "repo": repo,
            "pr": number,
            "head": info["head"],
            "pr_state": info["pr_state"]
            if info["pr_state"] in ("open", "closed", "merged")
            else "",
            "fetched_at": _iso(self.clock()),
            "public": bool(info["public"]),
        }
        for field in ("branch", "head_repo"):
            if info.get(field):
                request[field] = info[field]
        return batches(request, comments, files), mine

    # -- passes ------------------------------------------------------------------------

    def _due(self, key: str) -> bool:
        return self.state["prs"].get(key, {}).get("next_due", 0) <= self.clock()

    def _schedule(self, key: str) -> None:
        mine = self.state["prs"].get(key)
        if mine is None:
            return
        active = mine.get("activity")
        recent = False
        if active:
            try:
                moment = datetime.fromisoformat(active.replace("Z", "+00:00")).timestamp()
                recent = self.clock() - moment < IDLE_AFTER_S
            except ValueError:
                recent = False
        mine["next_due"] = self.clock() + (FAST_S if recent else SLOW_S)

    def once(self, *, respect_schedule: bool = False) -> _PassSummary:
        """One pass. ``{prs, requests, comments, skipped, rate_limited, errors}``; counts only."""
        summary: _PassSummary = {
            "prs": 0,
            "requests": 0,
            "comments": 0,
            "skipped": 0,
            "rate_limited": False,
            "errors": [],
        }
        if self.state.get("rate_reset", 0) > self.clock():
            summary["rate_limited"] = True
            return summary
        try:
            watched = self.watch()
            for entry in watched:
                key = f"{entry['repo']}#{entry['pr']}"
                if respect_schedule and not self._due(key):
                    summary["skipped"] += 1
                    continue
                try:
                    requests, mine = self.read_pr(entry["repo"], int(entry["pr"]))
                    for request in requests:
                        self.post(request)
                        summary["requests"] += 1
                        summary["comments"] += len(request["comments"])
                    self.state["prs"][key] = mine
                    summary["prs"] += 1
                    self._schedule(key)
                    logger.info(
                        "review feeder: %s#%s posted %d request(s)",
                        entry["repo"],
                        entry["pr"],
                        len(requests),
                    )
                except RateLimited:
                    # Before the generic handlers below: a near rate limit ends the whole pass
                    # (the outer handler marks it), it is never one PR's error.
                    raise
                except FeederError as exc:
                    if exc.code in ("hub_refused", "github_token_refused"):
                        raise
                    summary["errors"].append(exc.code)
                    logger.warning(
                        "review feeder: %s#%s skipped (%s)", entry["repo"], entry["pr"], exc.code
                    )
                except Exception as exc:  # one odd PR never stops the others; type only
                    summary["errors"].append("unexpected")
                    logger.warning(
                        "review feeder: %s#%s skipped (unexpected %s)",
                        entry["repo"],
                        entry["pr"],
                        type(exc).__name__,
                    )
        except RateLimited:
            summary["rate_limited"] = True
            logger.warning("review feeder: GitHub's rate limit is near; paused until its reset")
        finally:
            self._save()
        return summary

    def run(self, stop: threading.Event, *, interval_s: float = 30) -> None:
        """Pass after pass until ``stop`` is set; each PR is read on its own cadence."""
        while not stop.is_set():
            try:
                self.once(respect_schedule=True)
            except FeederError as exc:
                logger.warning("review feeder: pass stopped (%s)", exc.code)
            except Exception as exc:  # the loop outlives any one bad pass; type only
                logger.warning("review feeder: pass stopped (unexpected %s)", type(exc).__name__)
            stop.wait(interval_s)


WAITING_TEXT = (
    "Waiting for the GitHub read token. Create a fine-grained, read-only token (pull requests "
    "and contents: read) for the watched repositories and save it in {path}. Nothing is read "
    "from GitHub until it is there."
)
HUB_TOKEN_TEXT = (
    "The hub's feeder token file {path} is missing. As the workspace owner, mint it with "
    "`commonplace token add review-feed --workspace <workspace> --client feeder "
    "--scopes pr_review:write --by <owner email>` and save its one line there."
)


def command(args) -> int:
    """``commonplace pr-review poll``. Never prints a token or a comment's words."""
    import sys

    try:
        hub_token = read_token(args.token_file)
        if hub_token is None:
            print(HUB_TOKEN_TEXT.format(path=args.token_file), file=sys.stderr)
            return 2
        stop = threading.Event()
        while True:
            github_token = read_token(args.github_token_file)
            if github_token is not None:
                break
            print(WAITING_TEXT.format(path=args.github_token_file), file=sys.stderr)
            if args.once or stop.wait(60):
                return WAITING_EXIT
        feeder = Feeder(args.hub, hub_token, github_token, args.state_file, api=args.github_api)
        if args.once:
            summary = feeder.once()
            print(json.dumps(summary))
            return 0 if not summary["errors"] else 1
        feeder.run(stop)
        return 0
    except FeederError as exc:
        print(f"commonplace pr-review: stopped ({exc.code})", file=sys.stderr)
        return 2


def add_parser(sub) -> None:
    review = sub.add_parser(
        "pr-review", help="the review-comment feeder (runs beside GitHub access, not on the hub)"
    ).add_subparsers(dest="action", required=True)
    poll = review.add_parser("poll", help="read watched PRs on GitHub and post their comments")
    poll.add_argument("--hub", required=True, help="the hub's address, e.g. https://hub.example")
    poll.add_argument("--token-file", required=True, help="file holding the hub's feeder token")
    poll.add_argument(
        "--github-token-file", required=True, help="file holding the read-only GitHub token"
    )
    poll.add_argument(
        "--state-file",
        default="~/.commonplace-pr-review/state.json",
        help="what the feeder has seen (ETags, comment ids); kept beside the feeder",
    )
    poll.add_argument("--github-api", default=GITHUB_API, help=argparse.SUPPRESS)
    poll.add_argument("--once", action="store_true", help="one pass, then exit")
    poll.set_defaults(func=command)
