"""Reject placeholder git author and committer identities.

The pre-push hook and CI both call this module, so ``git push --no-verify``
still fails on the pull request.

    python -m commonplace.guards.identity --range origin/develop..HEAD
    python -m commonplace.guards.identity --pre-push origin <url>   # hook; reads stdin
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import TextIO

from commonplace.guards import push_range

PLACEHOLDER_DOMAINS = frozenset({"example.invalid", "example.com", "localhost"})
GENERIC_NAMES = frozenset(
    {
        "project contributor",
        "your name",
        "your name here",
        "contributor",
        "anonymous",
        "placeholder",
        "unknown",
        "nobody",
    }
)

FIX_MESSAGE = """\
Fix: set git user.name and git user.email to the real identity, then amend or rebase.
  git config user.name "Your Name"
  git config user.email "you@your-domain"
  git commit --amend --reset-author --no-edit
If more than one commit is affected, rebase them onto the base:
  git rebase <base> --exec 'git commit --amend --reset-author --no-edit'
Rejected: emails at example.invalid, example.com, or localhost; empty names; generic names such as "Project contributor".
"""


class IdentityError(RuntimeError):
    """The commit range could not be read."""


def identity_problem(name: str, email: str) -> str | None:
    """A short reason the identity is unusable, or None when it is acceptable."""
    collapsed = " ".join((name or "").split())
    address = (email or "").strip()
    if not collapsed:
        return "empty name"
    if "@" not in address:
        return "empty email"
    domain = address.rsplit("@", 1)[1].casefold()
    if domain in PLACEHOLDER_DOMAINS:
        return f"placeholder email domain {domain}"
    if collapsed.casefold() in GENERIC_NAMES:
        return f"generic name {collapsed!r}"
    return None


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _merge_base(repo: Path, one: str, other: str) -> str | None:
    proc = _git(repo, "merge-base", one, other)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def revision_for(
    repo: Path, update: push_range.PushUpdate, remote: str, default_ref: str | None
) -> str | None:
    """The commits an update introduces, or None for a delete or a tag."""
    if update.is_delete or update.is_tag:
        return None
    if not set(update.remote_sha) == {"0"}:
        seen = _git(repo, "cat-file", "-e", f"{update.remote_sha}^{{commit}}")
        if seen.returncode == 0:
            return f"{update.remote_sha}..{update.local_sha}"
    ref = default_ref or push_range.default_branch(repo, remote)
    if ref:
        base = _merge_base(repo, update.local_sha, ref)
        if base and base != update.local_sha:
            return f"{base}..{update.local_sha}"
    return update.local_sha


def problems_in_revision(repo: Path, revision: str) -> list[str]:
    """One line per placeholder author or committer in ``revision``."""
    proc = _git(repo, "log", "-z", "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce", revision)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"exit status {proc.returncode}"
        raise IdentityError(f"git log {revision} failed: {detail}")
    found: list[str] = []
    for record in proc.stdout.split("\0"):
        if not record:
            continue
        row = record.split("\x1f")
        if len(row) != 5:
            continue
        sha, author, author_email, committer, committer_email = row
        short = sha[:12]
        for role, person, address in (
            ("author", author, author_email),
            ("committer", committer, committer_email),
        ):
            reason = identity_problem(person, address)
            if reason:
                found.append(f"{short} {role} {person!r} <{address}>: {reason}")
    return found


def format_failure(found: list[str]) -> str:
    body = "\n".join(f"  {line}" for line in found)
    return f"Refusing placeholder git identity:\n{body}\n\n{FIX_MESSAGE}"


def check_revisions(repo: Path, revisions: list[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for revision in revisions:
        for line in problems_in_revision(repo, revision):
            if line not in seen:
                seen.add(line)
                found.append(line)
    return found


def pre_push_revisions(repo: Path, text: str, remote: str, default_ref: str | None) -> list[str]:
    revisions: list[str] = []
    for update in push_range.parse_pre_push_lines(text):
        revision = revision_for(repo, update, remote, default_ref)
        if revision and revision not in revisions:
            revisions.append(revision)
    return revisions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m commonplace.guards.identity",
        description="Fail when a commit author or committer is a placeholder.",
    )
    parser.add_argument(
        "--range", dest="revision", help="git revision to check, such as base..HEAD"
    )
    parser.add_argument(
        "--pre-push",
        action="store_true",
        help="read the commits being pushed from stdin (the pre-push hook)",
    )
    parser.add_argument("--repo", default=".", help="repository directory (default .)")
    parser.add_argument("remote", nargs="?", default="origin", help="remote name (git passes it)")
    parser.add_argument("url", nargs="?", help="remote url (git passes it)")
    return parser


def main(argv: list[str] | None = None, stdin: TextIO | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Path(args.repo)
    source = sys.stdin if stdin is None else stdin
    try:
        if args.pre_push:
            default = push_range.default_branch(repo, args.remote)
            revisions = pre_push_revisions(repo, source.read(), args.remote, default)
        elif args.revision:
            revisions = [args.revision]
        else:
            print("pass --range REV or --pre-push", file=sys.stderr)
            return 2
        found = check_revisions(repo, revisions)
    except (IdentityError, push_range.PushRangeError) as exc:
        print(f"Refusing placeholder git identity: {exc}", file=sys.stderr)
        print(FIX_MESSAGE, file=sys.stderr)
        return 1
    if not found:
        return 0
    print(format_failure(found), file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
