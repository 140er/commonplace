"""Local T3 adapter. Read public thread JSON, then its native session index.

T3's thread view deliberately has no transcript path. The read-only v2
provider-thread projection supplies native session IDs; the provider's own
JSONL is the lossless source, including tool results. No timeline export or
latest-N message view can replace it.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from .models import SuccessorRef


class T3Error(ValueError):
    pass


@dataclass(frozen=True)
class Target:
    provider: str
    model: str
    effort: str = ""

    @classmethod
    def parse(cls, value: str, effort: str = "") -> Target:
        provider, separator, model = value.partition("/")
        provider = {"claude": "claudeAgent", "claude-code": "claudeAgent"}.get(provider, provider)
        if not separator or not all(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", part) for part in (provider, model)
        ):
            raise T3Error("--to must be <T3 provider instance>/<model slug>")
        if effort and not re.fullmatch(r"[a-z][a-z0-9_-]*", effort):
            raise T3Error("invalid effort")
        return cls(provider, model, effort)


@dataclass(frozen=True)
class SourceThread:
    thread_id: str
    cwd: str
    provider: str
    model: str
    transcript_format: str
    transcript_paths: tuple[Path, ...]
    project_id: str
    worktree: bool = False
    branch: str = ""
    pr_url: str = ""
    workspace_root: str = ""

    def transcript(self) -> str:
        # newline="" retains CR/LF in raw records.
        texts = []
        for path in self.transcript_paths:
            with path.open(encoding="utf-8", newline="") as stream:
                texts.append(stream.read())
        if any(text and not text.endswith("\n") for text in texts[:-1]):
            raise T3Error("an earlier native transcript is incomplete; retry after it is flushed")
        return "".join(texts)


class T3:
    def __init__(self, base_dir=None, *, executable=("t3",)):
        self.base_dir = Path(base_dir or os.environ.get("T3CODE_HOME") or Path.home() / ".t3")
        self.state_dir = self.base_dir.expanduser() / "userdata"
        self.executable = executable

    def _run(self, arguments, *, cwd=None, input=None, environment=None):
        try:
            result = subprocess.run(
                [*self.executable, *arguments],
                cwd=cwd,
                input=input,
                env=environment,
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise T3Error("T3 command failed or timed out; source was not settled") from None
        if result.returncode:
            # CLI diagnostics can quote prompts or credentials.
            raise T3Error("T3 command failed; source was not settled")
        return result.stdout

    def _json(self, arguments, **kwargs):
        try:
            value = json.loads(self._run(arguments, **kwargs))
        except ValueError as error:
            if isinstance(error, T3Error):
                raise
            raise T3Error("T3 returned invalid JSON; source was not settled") from None
        if not isinstance(value, dict):
            raise T3Error("T3 returned invalid JSON; source was not settled")
        return value

    def resolve(self, thread_id: str) -> SourceThread:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,255}", thread_id):
            raise T3Error("invalid T3 thread id")
        view = self._json(["thread", "show", thread_id, "--json", "--messages", "0"])
        thread = view.get("thread", {})
        if not isinstance(thread, dict) or thread.get("threadId") != thread_id:
            raise T3Error("T3 did not return the requested thread")
        try:
            database = (self.state_dir / "statev2.sqlite").resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(database, uri=True)) as connection:
                project = connection.execute(
                    "SELECT workspace_root FROM projection_projects WHERE project_id = ?",
                    (thread["projectId"],),
                ).fetchone()
                rows = connection.execute(
                    "SELECT payload_json FROM orchestration_v2_projection_provider_threads "
                    "WHERE thread_id = ? ORDER BY first_run_ordinal, updated_at",
                    (thread_id,),
                ).fetchall()
            native = [json.loads(row[0]) for row in rows]
            native = [row for row in native if row.get("ownerNodeId") is None]
        except (OSError, sqlite3.Error, ValueError, KeyError):
            raise T3Error("cannot read T3's native session index in T3CODE_HOME/userdata") from None
        if not project or not native:
            raise T3Error("T3 has no native transcript for this thread yet")
        cwd = str(Path(thread.get("worktreePath") or project[0]).expanduser().resolve())
        if not Path(cwd).is_dir():
            raise T3Error("the source working directory is unavailable")
        formats = {self._format(row.get("driver")) for row in native}
        if len(formats) != 1:
            raise T3Error("this thread contains mixed native transcript formats")
        paths = tuple(dict.fromkeys(self._transcript_path(row) for row in native))
        pr = thread.get("linkedPullRequest") or {}
        return SourceThread(
            thread_id=thread_id,
            cwd=cwd,
            provider=thread["providerInstanceId"],
            model=thread["model"],
            transcript_format=formats.pop(),
            transcript_paths=paths,
            project_id=thread["projectId"],
            worktree=bool(thread.get("worktreePath")),
            branch=thread.get("branch") or "",
            pr_url=pr.get("url", ""),
            workspace_root=project[0],
        )

    @staticmethod
    def _format(driver):
        if driver in ("claudeAgent", "claude-code", "claude"):
            return "claude-code"
        if driver == "codex":
            return "codex"
        raise T3Error("manual handoff supports Claude Code and Codex source transcripts")

    def _transcript_path(self, row):
        native_id = (row.get("nativeThreadRef") or {}).get("nativeId", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", native_id):
            raise T3Error("T3 has not confirmed the native session id yet")
        fmt = self._format(row.get("driver"))
        settings_file = self.state_dir / "settings.json"
        settings = json.loads(settings_file.read_text()) if settings_file.exists() else {}
        instance = settings.get("providerInstances", {}).get(row.get("providerInstanceId"), {})
        configured = instance.get("config", {}).get("homePath")
        variable, default = (
            ("CLAUDE_CONFIG_DIR", ".claude") if fmt == "claude-code" else ("CODEX_HOME", ".codex")
        )
        home = Path(configured or os.environ.get(variable) or Path.home() / default).expanduser()
        if fmt == "claude-code":
            candidates = (home / "projects").glob(f"*/{native_id}.jsonl")
        else:
            candidates = (
                path
                for directory in ("sessions", "archived_sessions")
                for path in (home / directory).glob(f"**/*{native_id}.jsonl")
            )
        paths = {path.resolve() for path in candidates if path.is_file()}
        if len(paths) != 1:
            raise T3Error("cannot uniquely locate the provider's full native transcript")
        return paths.pop()

    def start(self, source: SourceThread, prompt: str, target: Target) -> SuccessorRef:
        if source.worktree:
            value = self._start_worktree(source, prompt, target)
        else:
            argv = [
                "thread",
                "start",
                source.cwd,
                "-",
                "--provider",
                target.provider,
                "--model",
                target.model,
                "--json",
            ]
            if target.effort:
                argv.extend(["--effort", target.effort])
            value = self._json(argv, cwd=source.cwd, input=prompt)
        identifier = value.get("threadId")
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,255}", identifier)
            or identifier == source.thread_id
            or not value.get("runId")
            or value.get("status") in ("failed", "cancelled", "interrupted")
        ):
            raise T3Error("T3 did not confirm a new successor thread")
        return SuccessorRef(agent_ref=f"t3:{identifier}", thread_id=identifier)

    def _start_worktree(self, source, prompt, target):
        # The installed CLI has no existing-worktree flag. Its MCP bridge is
        # the supported route to bind the checkout BEFORE the agent starts.
        issued = self._json(
            [
                "auth",
                "session",
                "issue",
                "--scope",
                "orchestration:read",
                "--scope",
                "orchestration:operate",
                "--ttl",
                "5m",
                "--label",
                "commonplace handoff",
                "--json",
            ]
        )
        if not all(
            isinstance(issued.get(key), str) and issued[key] for key in ("token", "sessionId")
        ):
            raise T3Error("T3 did not issue a bridge session")
        try:
            runtime = json.loads((self.state_dir / "server-runtime.json").read_text())
            if not isinstance(runtime, dict) or not isinstance(runtime.get("origin"), str):
                raise T3Error("T3's local server address is unavailable")
            environment = os.environ.copy()
            environment.update(
                T3_ACP_MCP_ENDPOINT=runtime["origin"].rstrip("/") + "/mcp",
                T3_ACP_MCP_AUTHORIZATION="Bearer " + issued["token"],
            )
            arguments = {
                "title": "Context handoff",
                "projectId": source.project_id,
                "message": prompt,
                "modelSelection": {"instanceId": target.provider, "model": target.model},
                "workspaceStrategy": {"type": "existing_worktree", "worktreePath": source.cwd},
            }
            if target.effort:
                arguments["effort"] = target.effort
            result = self._json(
                ["acp-mcp-call", "t3_thread_launch", json.dumps(arguments)],
                cwd=source.cwd,
                environment=environment,
            )
            if result.get("isError") or not isinstance(result.get("structuredContent"), dict):
                raise T3Error("T3 could not start the successor in the existing worktree")
            return result["structuredContent"]
        finally:
            # Tokens never reach a file, prompt, log, result, or successor env.
            try:
                self._run(["auth", "session", "revoke", issued["sessionId"]])
            except T3Error:
                pass  # The short TTL still applies; never hide a confirmed launch.

    def settle(self, thread_id):
        self._json(["thread", "settle", thread_id, "--json"])
