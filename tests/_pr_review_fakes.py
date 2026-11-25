"""A fake GitHub for the review-comment feeder, and a local git repository behind it.

The fake answers the few read endpoints the feeder uses (a pull request, its inline comments,
reviews, conversation comments and changed files) from memory and from a local bare repository,
with ETags, pagination and rate-limit headers like GitHub's. It records every request, so a test
can show that nothing but reads ever reached it. No test talks to the real GitHub.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def ago(minutes: int) -> str:
    """A GitHub-style time ``minutes`` before now (the hub refuses times in the future)."""
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def git(cwd, *args) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=Reviewer", "-c", "user.email=r@example.test"]
        + ["-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return result.stdout.strip()


def make_repo(root: Path, branch="feat/thing") -> tuple[Path, Path]:
    """A bare remote with ``main`` and a PR branch, and a working clone of it."""
    bare, work = root / "remote.git", root / "work"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    (work / "app.py").write_text("x = 1\nprint(x)\n")
    git(work, "add", "app.py")
    git(work, "commit", "-q", "-m", "start")
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "-q", "origin", "main")
    git(work, "checkout", "-q", "-b", branch)
    (work / "app.py").write_text("x = 2\nprint(x)\n")
    git(work, "commit", "-qam", "the change under review")
    git(work, "push", "-q", "origin", branch)
    return bare, work


class FakeGitHub:
    def __init__(
        self, bare: Path, repo="acme/widgets", number=7, branch="feat/thing", *, private=False
    ):
        self.bare, self.repo, self.number, self.branch = bare, repo, number, branch
        self.private = private
        self.state, self.merged = "open", False
        self.head_repo = repo
        self.lists = {"inline": [], "review": [], "conversation": []}
        self.requests: list[tuple[str, str, str]] = []
        self.rate_remaining = 5000
        self.page_size = 100
        self.refuse_token = False
        self._next_id = 1000
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet test output
                pass

            def _record(self):
                fake.requests.append(
                    (self.command, self.path, self.headers.get("Authorization", ""))
                )

            def do_GET(self):
                self._record()
                fake.answer(self)

            def _refuse(self):
                self._record()
                self.send_response(405)
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_POST = do_PUT = do_PATCH = do_DELETE = _refuse

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # -- what reviewers do --------------------------------------------------------------

    def add(
        self,
        kind,
        body,
        *,
        login="reviewer1",
        association="MEMBER",
        when=None,
        **extra,
    ):
        self._next_id += 1
        when = when or ago(30)
        item = {
            "id": self._next_id,
            "user": {"login": login, "type": "User"},
            "author_association": association,
            "body": body,
            "created_at": when,
            "updated_at": when,
        }
        if kind == "inline":
            item.update(path="app.py", line=1, commit_id=self.head(), in_reply_to_id=None)
        if kind == "review":
            item.update(state=extra.pop("state", "COMMENTED"), submitted_at=when)
            item.pop("created_at")
            item.pop("updated_at")
        item.update(extra)
        self.lists[kind].append(item)
        return item

    def edit(self, item, body, when=None):
        item["body"] = body
        item["updated_at"] = when or ago(5)

    def remove(self, kind, item):
        self.lists[kind].remove(item)

    # -- what GitHub answers ------------------------------------------------------------

    def head(self) -> str:
        return git(self.bare, "rev-parse", f"refs/heads/{self.branch}")

    def files(self) -> list[str]:
        return git(self.bare, "diff", "--name-only", "main", self.branch).splitlines()

    def _pull(self):
        return {
            "number": self.number,
            "state": "closed" if self.merged else self.state,
            "merged": self.merged,
            "head": {"sha": self.head(), "ref": self.branch, "repo": {"full_name": self.head_repo}},
            "base": {"ref": "main", "repo": {"full_name": self.repo, "private": self.private}},
        }

    def answer(self, h):
        if self.refuse_token:
            return self._send(h, 401, {"message": "Bad credentials"})
        parts = urlsplit(h.path)
        query = parse_qs(parts.query)
        prefix = f"/repos/{self.repo}/"
        if not parts.path.startswith(prefix):
            return self._send(h, 404, {"message": "Not Found"})
        rest = parts.path[len(prefix) :]
        n = self.number
        routes = {
            f"pulls/{n}": lambda: self._pull(),
            f"pulls/{n}/comments": lambda: self.lists["inline"],
            f"pulls/{n}/reviews": lambda: self.lists["review"],
            f"issues/{n}/comments": lambda: self.lists["conversation"],
            f"pulls/{n}/files": lambda: [{"filename": f} for f in self.files()],
        }
        if rest not in routes:
            return self._send(h, 404, {"message": "Not Found"})
        body = routes[rest]()
        link = None
        if isinstance(body, list):
            page = int(query.get("page", ["1"])[0])
            size = self.page_size
            if len(body) > page * size:
                link = f'<{self.url}{parts.path}?per_page={size}&page={page + 1}>; rel="next"'
            body = body[(page - 1) * size : page * size]
        raw = json.dumps(body).encode()
        etag = '"' + hashlib.sha256(raw).hexdigest()[:20] + '"'
        if h.headers.get("If-None-Match") == etag:
            return self._send(h, 304, None, etag=etag)
        return self._send(h, 200, body, etag=etag, link=link, raw=raw)

    def _send(self, h, status, body, *, etag=None, link=None, raw=None):
        data = b"" if status == 304 else (raw if raw is not None else json.dumps(body).encode())
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        h.send_header("X-RateLimit-Remaining", str(self.rate_remaining))
        h.send_header("X-RateLimit-Reset", "4102444800")
        if etag:
            h.send_header("ETag", etag)
        if link:
            h.send_header("Link", link)
        h.end_headers()
        if data:
            h.wfile.write(data)

    def writes(self) -> list:
        return [r for r in self.requests if r[0] != "GET"]
