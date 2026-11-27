"""Machine-side, subscription-only CLI calls for replay experiments.

No arbitrary argv, inherited provider settings, credential inspection, API fallback,
or model tools. A CLI returns proposed file contents; the replay runner applies only
the manifest's editable files and runs the original tests itself.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Queue
from tempfile import TemporaryDirectory
from threading import Thread

ENV_KEYS = ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "LANG", "LC_ALL", "TMPDIR", "TEMP", "TMP")
CLI_NAMES = ("claude", "codex", "grok")
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,100}")
PLAN_LIMIT = re.compile(
    r"usage.limit|rate.limit|plan.limit|hit your limit|limit (?:reached|exceeded)|"
    r"quota.{0,40}exceeded|too many requests|\b429\b",
    re.IGNORECASE,
)
MAX_OUTPUT = 2 * 1024 * 1024
STALL_SECONDS = 15 * 60


def child_environment(source=None) -> dict[str, str]:
    """Allowlist, rather than enumerating (or even accessing) credential values."""
    source = os.environ if source is None else source
    return {key: source[key] for key in ENV_KEYS if key in source}


@dataclass(frozen=True)
class Capture:
    code: int
    stdout: str
    stderr: str
    latency_ms: int
    reason: str | None = None


def capture(argv, *, cwd, env, prompt="") -> Capture:
    """Drain both pipes; stop only for an output limit or 15 minutes without output.

    Raw output stays in memory and is never logged. Each invocation owns its process
    group. All pipe workers finish before this returns, including on an exception.
    """
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError:
        return Capture(127, "", "", 0, "cli_unavailable")
    queue: Queue[tuple[int, bytes | None]] = Queue()
    stdin = proc.stdin
    assert stdin is not None

    def read(pipe, side):
        try:
            while chunk := os.read(pipe.fileno(), 8192):
                queue.put((side, chunk))
        finally:
            pipe.close()
            queue.put((side, None))

    def write():
        try:
            stdin.write(prompt.encode("utf-8"))
            stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                stdin.close()
            except (BrokenPipeError, OSError):
                pass

    workers = [
        Thread(target=read, args=(proc.stdout, 0)),
        Thread(target=read, args=(proc.stderr, 1)),
        Thread(target=write),
    ]
    for worker in workers:
        worker.start()
    chunks: list[list[bytes]] = [[], []]
    size, closed, reason = 0, 0, None
    last_output = time.monotonic()

    def stop():
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif proc.poll() is None:
            proc.kill()

    try:
        while closed < 2:
            try:
                side, chunk = queue.get(
                    timeout=max(0.001, STALL_SECONDS - (time.monotonic() - last_output))
                )
            except Empty:
                reason = "stalled_no_output"
                stop()
                break
            if chunk is None:
                closed += 1
                continue
            last_output = time.monotonic()
            size += len(chunk)
            if size > MAX_OUTPUT:
                reason = "output_limit_2mb"
                stop()
                break
            chunks[side].append(chunk)
        try:
            code = proc.wait(timeout=max(0.001, STALL_SECONDS - (time.monotonic() - last_output)))
        except subprocess.TimeoutExpired:
            reason = "stalled_no_output"
            stop()
            code = proc.wait()
    except BaseException:
        stop()
        proc.wait()
        raise
    finally:
        for worker in workers:
            worker.join()
    return Capture(
        code,
        b"".join(chunks[0]).decode("utf-8", errors="replace"),
        b"".join(chunks[1]).decode("utf-8", errors="replace"),
        round((time.monotonic() - started) * 1000),
        reason,
    )


@dataclass(frozen=True)
class Reply:
    state: str
    answer: str = ""
    tokens: int | None = None
    tokens_reason: str | None = "CLI did not report complete token usage"
    latency_ms: int = 0
    reason: str | None = None


def _count(value):
    return value if type(value) is int and 0 <= value <= 10**15 else None


def _tokens(usage, cli):
    if not isinstance(usage, dict):
        return None
    if cli == "grok" and _count(usage.get("total_tokens")) is not None:
        return usage["total_tokens"]
    fields: tuple[str, ...] = ("input_tokens", "output_tokens")
    if cli == "claude":
        fields += ("cache_creation_input_tokens", "cache_read_input_tokens")
    elif cli == "grok":
        if "inputTokens" in usage:
            fields = ("inputTokens", "outputTokens")
        else:
            fields += ("cache_creation_input_tokens", "cache_read_input_tokens")
    counts = [_count(usage.get(key)) for key in fields]
    return sum(counts) if None not in counts else None


def _decode(cli, output):
    """Only projected text and complete usage leave the CLI parser."""
    if cli != "codex":
        data = json.loads(output)
        if not isinstance(data, dict):
            raise ValueError
        answer = data.get("result" if cli == "claude" else "text", "")
        return answer, _tokens(data.get("usage"), cli), bool(data.get("is_error"))
    events = [json.loads(line) for line in output.splitlines() if line.strip()]
    turns = [e for e in events if isinstance(e, dict) and e.get("type") == "turn.completed"]
    usage = [_tokens(e.get("usage"), cli) for e in turns]
    answers = [
        e["item"].get("text", "")
        for e in events
        if isinstance(e, dict)
        and e.get("type") == "item.completed"
        and isinstance(e.get("item"), dict)
        and e["item"].get("type") == "agent_message"
    ]
    failed = any(isinstance(e, dict) and e.get("type") in ("error", "turn.failed") for e in events)
    return "\n".join(answers), sum(usage) if usage and None not in usage else None, failed


class SubscriptionCLI:
    def __init__(self, cli: str, *, model: str | None = None):
        if cli not in CLI_NAMES or (model is not None and not MODEL.fullmatch(model)):
            raise ValueError("subscription CLI or model is invalid; API flags are not supported")
        self.cli, self.model = cli, model

    def run(self, prompt: str) -> Reply:
        # A fresh cwd prevents project settings, hooks, .envrc and model aliases from
        # changing auth/routing. Only the CLI itself follows the auth-file link: we
        # never open, copy, parse or log credentials or an owner's settings.
        with TemporaryDirectory(prefix="commonplace-replay-cli-") as directory:
            root = Path(directory)
            env = child_environment()
            home = Path(env.get("HOME") or env.get("USERPROFILE") or Path.home())
            key, folder, auth = {
                "claude": ("CLAUDE_CONFIG_DIR", ".claude", ".credentials.json"),
                "codex": ("CODEX_HOME", ".codex", "auth.json"),
                "grok": ("GROK_HOME", ".grok", "auth.json"),
            }[self.cli]
            auth_home = Path(os.environ.get(key) or home / folder).expanduser()
            config = root / "config"
            config.mkdir(mode=0o700)
            (config / auth).symlink_to(auth_home / auth)
            env[key] = str(config)
            argv, status_argv = self._commands(config)
            status = capture(status_argv, cwd=root, env=env)
            if status.reason or status.code or not self._subscribed(status):
                reason = status.reason or (
                    "plan_limit"
                    if PLAN_LIMIT.search(status.stdout + status.stderr)
                    else "subscription_not_verified"
                )
                return Reply("paused" if reason == "plan_limit" else "refused", reason=reason)
            result = capture(argv, cwd=root, env=env, prompt=prompt)
            tokens, answer, failed = None, "", bool(result.code or result.reason)
            try:
                answer, tokens, error = _decode(self.cli, result.stdout)
                failed |= error
            except (ValueError, TypeError, KeyError, RecursionError):
                failed = True
            if failed:
                reason = result.reason or (
                    "plan_limit"
                    if PLAN_LIMIT.search(result.stdout + result.stderr)
                    else "cli_failed"
                )
                return Reply(
                    "paused" if reason == "plan_limit" else "refused",
                    tokens=tokens,
                    tokens_reason=None if tokens is not None else Reply.tokens_reason,
                    latency_ms=result.latency_ms,
                    reason=reason,
                )
            if not isinstance(answer, str) or not answer.strip():
                return Reply(
                    "refused", reason="cli_result_unrecognized", latency_ms=result.latency_ms
                )
            return Reply(
                "complete",
                answer,
                tokens,
                None if tokens is not None else Reply.tokens_reason,
                result.latency_ms,
            )

    def _commands(self, config):
        cli = self.cli
        model = ["--model", self.model] if self.model else []
        if cli == "claude":
            settings = [
                "--setting-sources",
                "",
                "--settings",
                '{"apiKeyHelper":"","disableAllHooks":true}',
            ]
            return (
                [
                    cli,
                    "-p",
                    "--output-format",
                    "json",
                    "--tools",
                    "",
                    "--strict-mcp-config",
                    "--safe-mode",
                    "--no-session-persistence",
                    *settings,
                    *model,
                ],
                [cli, *settings, "auth", "status"],
            )
        if cli == "codex":
            return (
                [
                    cli,
                    "exec",
                    "--json",
                    "--ephemeral",
                    "--ignore-user-config",
                    "--ignore-rules",
                    "--sandbox",
                    "read-only",
                    "--skip-git-repo-check",
                    "-c",
                    'forced_login_method="chatgpt"',
                    "-c",
                    'model_provider="openai"',
                    "-c",
                    "features.shell_tool=false",
                    "-c",
                    "features.apply_patch_freeform=false",
                    *model,
                    "-",
                ],
                [cli, "login", "status"],
            )
        (config / "config.toml").write_text(
            "[cli]\nauto_update=false\n[models]\nmax_retries=0\n"
            "[features]\ntelemetry=false\nmanaged_config=false\n[session]\nload_envrc=false\n",
            encoding="utf-8",
        )
        return (
            [
                cli,
                "-p",
                "--output-format",
                "json",
                "--tools",
                "",
                "--deny",
                "MCPTool",
                "--no-memory",
                "--no-subagents",
                "--disable-web-search",
                "--max-turns",
                "1",
                *model,
            ],
            [cli, "usage", "--json"],
        )

    def _subscribed(self, status):
        if self.cli == "codex":
            return (status.stdout + status.stderr).strip() == "Logged in using ChatGPT"
        try:
            data = json.loads(status.stdout)
        except ValueError:
            return False
        if not isinstance(data, dict):
            return False
        if self.cli == "claude":
            return (
                data.get("loggedIn") is True
                and data.get("authMethod") == "claude.ai"
                and data.get("apiProvider") == "firstParty"
                and data.get("subscriptionType") in ("pro", "max", "team", "enterprise")
            )
        # A successful usage query alone is not proof of subscription auth.
        # Only an explicit positive marker is accepted; unknown formats refuse.
        plan = data.get("plan")
        return (
            isinstance(plan, dict)
            and plan.get("subscription") is True
            and not data.get("error")
            and not data.get("is_error")
        )
