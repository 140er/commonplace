"""Check a batch of review comments from the feeder before anything is stored.

A request is validated whole: one bad comment stores nothing. Comment text is kept exactly as
sent (verbatim, up to GitHub's own 65,536-character cap); it is not refused for looking like a
secret, because the reviewer's words are the reviewer's. Every outward projection of it is
redacted elsewhere. Times follow the pipeline's rules (ISO 8601, no offset means UTC).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from commonplace.pipeline.compute import MAX_FUTURE, parse_at, stamp

MAX_COMMENTS = 100
MAX_REQUEST_BYTES = 1024 * 1024
MAX_BODY = 65_536
MAX_FILES = 300
OLDEST = datetime(2020, 1, 1, tzinfo=timezone.utc)
KINDS = ("inline", "review", "conversation")
REVIEW_STATES = ("COMMENTED", "CHANGES_REQUESTED", "APPROVED", "DISMISSED", "PENDING")
PR_STATES = ("open", "closed", "merged")
_REPO = re.compile(r"[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}")
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_LOGIN = re.compile(r"[A-Za-z0-9_.\[\]-]{1,100}")
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_ASSOC = re.compile(r"[A-Z_]{1,30}")
_BODY_KEYS = {"repo", "pr", "head", "pr_state", "branch", "head_repo", "fetched_at", "changed_files", "comments"}
_COMMENT_KEYS = {
    "kind",
    "id",
    "thread",
    "in_reply_to",
    "path",
    "line",
    "commit",
    "author",
    "review_state",
    "body",
    "created_at",
    "updated_at",
    "removed",
}
_AUTHOR_KEYS = {"login", "type", "association"}
_VERDICT = re.compile(
    r"\s*Reviewed head: [0-9a-f]{40}(?:[0-9a-f]{24})?\s*\n\s*Verdict: (?:PASS|CHANGES)\s*"
)
# The hidden marker a Coworker's own reply ends with (see the design note).
REPLY_MARKER = "<!-- cmmn:reply"


class Invalid(ValueError):
    """The request is not an acceptable batch of comments (a plain 400); nothing was stored."""


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_verdict(body: str) -> bool:
    """A review body that is only the public form of a gate verdict."""
    return _VERDICT.fullmatch(body) is not None


def _time(value, where, now, *, required=True):
    if value is None and not required:
        return None
    try:
        at = parse_at(value)
    except ValueError as exc:
        raise Invalid(f"{where}: {exc}") from None
    if at > now + MAX_FUTURE:
        raise Invalid(f"{where}: the time is more than 5 minutes in the future")
    if at < OLDEST:
        raise Invalid(f"{where}: the time is before 2020")
    return stamp(at)


def _id(value, where, *, required=True):
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not _ID.fullmatch(str(value)):
        raise Invalid(f"{where} must be 1 to 64 plain characters")
    return str(value)


def _sha(value, where, *, required=False):
    if value is None and not required:
        return None
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise Invalid(f"{where} must be a full lowercase commit SHA")
    return value


def _plain(value, where, limit, *, lines=False):
    if not isinstance(value, str):
        raise Invalid(f"{where} must be text")
    if len(value) > limit:
        raise Invalid(f"{where} is longer than {limit} characters")
    allowed = "\t\n\r" if lines else "\t"
    if any((ord(ch) < 32 and ch not in allowed) or ord(ch) == 127 for ch in value):
        raise Invalid(f"{where} must be plain text")
    return value


def _comment(raw, position, now):
    where = f"comment {position}"
    if not isinstance(raw, dict) or raw.keys() - _COMMENT_KEYS:
        raise Invalid(f"{where} holds only {', '.join(sorted(_COMMENT_KEYS))}")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise Invalid(f"{where}: kind must be one of {', '.join(KINDS)}")
    author = raw.get("author")
    if not isinstance(author, dict) or author.keys() - _AUTHOR_KEYS:
        raise Invalid(f"{where}: author holds login, type and association only")
    login = author.get("login")
    if not isinstance(login, str) or not _LOGIN.fullmatch(login):
        raise Invalid(f"{where}: author login is not valid")
    kind_of = author.get("type", "User")
    if kind_of not in ("User", "Bot", "Organization", "Mannequin"):
        raise Invalid(f"{where}: author type is not known")
    assoc = author.get("association")
    if assoc is not None and (not isinstance(assoc, str) or not _ASSOC.fullmatch(assoc)):
        raise Invalid(f"{where}: author association is not valid")
    state = raw.get("review_state")
    if state is not None and state not in REVIEW_STATES:
        raise Invalid(f"{where}: review_state is not known")
    line = raw.get("line")
    if line is not None and (isinstance(line, bool) or not isinstance(line, int) or not 0 <= line <= 10**7):
        raise Invalid(f"{where}: line must be a whole number")
    path = raw.get("path")
    body = _plain(raw.get("body", ""), f"{where}: body", MAX_BODY, lines=True)
    created = _time(raw.get("created_at"), f"{where}: created_at", now)
    removed = raw.get("removed", False)
    if not isinstance(removed, bool):
        raise Invalid(f"{where}: removed must be true or false")
    return {
        "kind": kind,
        "id": _id(raw.get("id"), f"{where}: id"),
        "thread": _id(raw.get("thread"), f"{where}: thread", required=False),
        "in_reply_to": _id(raw.get("in_reply_to"), f"{where}: in_reply_to", required=False),
        "path": None if path is None else _plain(path, f"{where}: path", 500),
        "line": line,
        "commit": _sha(raw.get("commit"), f"{where}: commit"),
        "author": login,
        "author_type": kind_of,
        "association": assoc,
        "review_state": state,
        "body": body,
        "digest": digest(body),
        "created_at": created,
        "edited_at": _time(raw.get("updated_at"), f"{where}: updated_at", now, required=False)
        or created,
        "removed": removed,
    }


def validate(body, now: datetime) -> dict:
    """The request as checked fields, or ``Invalid``."""
    if not isinstance(body, dict) or body.keys() - _BODY_KEYS:
        raise Invalid("the body holds only " + ", ".join(sorted(_BODY_KEYS)))
    if len(json.dumps(body)) > MAX_REQUEST_BYTES:
        raise Invalid("the request is larger than 1 MiB")
    repo = body.get("repo")
    if not isinstance(repo, str) or not _REPO.fullmatch(repo):
        raise Invalid("repo must look like owner/name")
    pr = body.get("pr")
    if isinstance(pr, bool) or not isinstance(pr, int) or not 1 <= pr <= 10**9:
        raise Invalid("pr must be a whole number from 1")
    state = body.get("pr_state", "")
    if state not in ("",) + PR_STATES:
        raise Invalid("pr_state must be open, closed or merged")
    head_repo = body.get("head_repo")
    if head_repo is not None and (not isinstance(head_repo, str) or not _REPO.fullmatch(head_repo)):
        raise Invalid("head_repo must look like owner/name")
    branch = body.get("branch")
    if branch is not None:
        branch = _plain(branch, "branch", 255)
    files = body.get("changed_files", [])
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise Invalid(f"changed_files must be a list of at most {MAX_FILES} paths")
    files = [_plain(f, "changed_files path", 500) for f in files]
    comments = body.get("comments")
    if not isinstance(comments, list) or len(comments) > MAX_COMMENTS:
        raise Invalid(f"comments must be a list of at most {MAX_COMMENTS} comments")
    out = [_comment(raw, n, now) for n, raw in enumerate(comments, 1)]
    keys = [(c["kind"], c["id"]) for c in out]
    if len(set(keys)) != len(keys):
        raise Invalid("a comment appears twice in the request")
    return {
        "repo": repo,
        "pr": pr,
        "head": _sha(body.get("head"), "head", required=True),
        "pr_state": state,
        "branch": branch,
        "head_repo": head_repo,
        "fetched_at": _time(body.get("fetched_at"), "fetched_at", now),
        "changed_files": files,
        "comments": out,
    }
