"""Merge a reported pull request on the caller's own GitHub grant.

Every refusal here is a plain code; GitHub's own answers (branch protection,
required checks and reviews) are passed through as generic outcomes, never as
response bodies.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from ..social.providers import SignInError
from . import client

SCOPE = "public_repo"
API = "https://api.github.com/repos/"
URL = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([1-9][0-9]*)")
# Preferred first; a repository's own settings decide which are allowed.
METHODS = (
    ("squash", "allow_squash_merge"),
    ("merge", "allow_merge_commit"),
    ("rebase", "allow_rebase_merge"),
)
REFUSALS = {
    401: "github_not_connected",
    405: "github_not_mergeable",
    409: "github_head_moved",
    403: "github_merge_refused",
    404: "github_merge_refused",
    422: "github_merge_refused",
}


def pull_request(url):
    """``(owner, repo, number)`` of a plain github.com PR URL, else None."""
    match = URL.fullmatch(url) if isinstance(url, str) else None
    if match is None or any(set(part) == {"."} for part in match.groups()[:2]):
        return None
    owner, repo, number = match.groups()
    return owner, repo, int(number)


def merge(token, owner, repo, number, head):
    """Merge only a clean, open, public-repository PR still at ``head``."""
    base = API + quote(owner, safe="") + "/" + quote(repo, safe="")
    status, _, repository = client.request("GET", base, token=token)
    if status == 401:
        raise SignInError("github_not_connected")
    if status == 404 or (
        status == 200 and isinstance(repository, dict) and repository.get("private") is not False
    ):
        raise SignInError("github_private_repo")
    if status != 200 or not isinstance(repository, dict):
        raise SignInError("github_merge_failed")
    method = next((name for name, flag in METHODS if repository.get(flag) is True), None)
    if method is None:
        raise SignInError("github_merge_refused")
    status, _, pull = client.request("GET", f"{base}/pulls/{number}", token=token)
    if status == 401:
        raise SignInError("github_not_connected")
    if status != 200 or not isinstance(pull, dict):
        raise SignInError("github_merge_failed")
    seen = pull.get("head")
    if not isinstance(seen, dict) or seen.get("sha") != head:
        raise SignInError("github_head_moved")
    if (
        pull.get("state") != "open"
        or pull.get("merged") is not False
        or pull.get("draft") is not False
        or pull.get("mergeable_state") != "clean"
    ):
        raise SignInError("github_not_mergeable")
    status, _, result = client.request(
        "PUT",
        f"{base}/pulls/{number}/merge",
        token=token,
        json_body={"sha": head, "merge_method": method},
    )
    if status != 200:
        raise SignInError(REFUSALS.get(status, "github_merge_failed"))
    if not isinstance(result, dict) or result.get("merged") is not True:
        raise SignInError("github_merge_failed")
    return {"merged": True, "sha": head}
