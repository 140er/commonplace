"""Pinned, resumable subscription replays, executed only by the local owner CLI.

The hub may read reports and accept/undo a promotion; it never calls ``run``.
Manifests are owner-supplied historical tasks, each at an immutable local commit,
with explicit source files, original test files, a test command and a known outcome.
"""

from __future__ import annotations

import json
import os
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from threading import Event

from . import schema
from .benchmark import TASK, Benchmarks, TaskResult, _protected
from .store import LIVE, OrchError, _channel, _checked_config, require_owner

MAX_TASKS = 50
MAX_CONTEXT = 100_000
COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _path(name):
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("replay paths must be repository-relative files")
    path = PurePosixPath(name)
    if path.is_absolute() or any(p in ("..", ".git") or p.startswith(".") for p in path.parts):
        raise ValueError("replay paths must be repository-relative files")
    return name


def specification(data):
    """Validate at the owner boundary; no arbitrary CLI args or evaluator changes."""
    from commonplace.runner.replay_cli import SubscriptionCLI

    try:
        if not isinstance(data, dict) or data.keys() - {"cli", "model", "role", "tasks"}:
            raise ValueError("unexpected replay fields")
        cli = data.get("cli")
        if not isinstance(cli, str):
            raise ValueError("replay needs a subscription CLI")
        SubscriptionCLI(cli, model=data.get("model"))
        if not isinstance(data.get("role"), str) or not schema.NAME.fullmatch(data["role"]):
            raise ValueError("replay needs a config role")
        tasks = data.get("tasks")
        if not isinstance(tasks, list) or not 5 <= len(tasks) <= MAX_TASKS:
            raise ValueError("replay needs 5 to 50 historical tasks")
        seen = set()
        for task in tasks:
            if not isinstance(task, dict) or set(task) != {
                "id",
                "revision",
                "request",
                "files",
                "test_files",
                "test_argv",
                "known_passed",
            }:
                raise ValueError(
                    "each replay needs a revision, request, files, tests and known_passed"
                )
            if (
                not isinstance(task["id"], str)
                or not TASK.fullmatch(task["id"])
                or task["id"] in seen
            ):
                raise ValueError("task ids must be unique short ids")
            seen.add(task["id"])
            if not isinstance(task["revision"], str) or not COMMIT.fullmatch(task["revision"]):
                raise ValueError("task revision must be a full immutable commit")
            if not isinstance(task["request"], str) or not 1 <= len(task["request"]) <= 20_000:
                raise ValueError("task request must be 1 to 20000 characters")
            if type(task["known_passed"]) is not bool:
                raise ValueError("known_passed must be a bool")
            for field in ("files", "test_files"):
                paths = task[field]
                if not isinstance(paths, list) or not 1 <= len(paths) <= 20:
                    raise ValueError("tasks need explicit source and test files")
                for name in paths:
                    _path(name)
            if set(task["files"]) & set(task["test_files"]):
                raise ValueError("tests cannot be editable files")
            for name in task["files"]:
                parts = PurePosixPath(name).parts
                if any(
                    re.search(r"test|eval|guard|safety|approval", p, re.I) for p in parts
                ) or name in (
                    "AGENTS.md",
                    "CLAUDE.md",
                    "REVIEW.md",
                    "pyproject.toml",
                    "package.json",
                ):
                    raise ValueError("tests, approval gates and evaluation rules cannot be edited")
            argv = task["test_argv"]
            if (
                not isinstance(argv, list)
                or not 2 <= len(argv) <= 40
                or not all(isinstance(arg, str) and arg and len(arg) <= 500 for arg in argv)
            ):
                raise ValueError("test_argv needs an executable and explicit narrow tests")
            if not any(
                arg == name or arg.startswith(name + "::")
                for arg in argv
                for name in task["test_files"]
            ):
                raise ValueError("test_argv must name an original test file explicitly")
        if len(_json(data).encode()) > 512_000:
            raise ValueError("replay manifest is too large")
    except (ValueError, TypeError) as exc:
        raise OrchError("invalid_replay", str(exc)) from None
    return json.loads(_json(data))


class Replays:
    def __init__(self, store):
        self.store = store
        self.benchmarks = Benchmarks(store)

    def create(self, principal, board, channel, manifest):
        require_owner(principal)
        _channel(channel)
        if channel == LIVE:
            raise OrchError("invalid_channel", "a replay needs an exp:<name> channel")
        spec = specification(manifest)
        with self.store.transaction() as conn:
            live, candidate = self.benchmarks._current(conn, board, channel)
            before, after = _checked_config(live), _checked_config(candidate)
            _protected(before, after)
            role = spec["role"]
            if role not in before.roles or role not in after.roles:
                raise OrchError("invalid_replay", "replay role is missing from a config")
            # This slice evaluates prompts and workflow text. Refuse a config change
            # whose effect (model slots, installed skills, routing) it cannot replay.
            paths = {f"roles.{role}.prompt", f"roles.{role}.workflow"}
            for config in (before, after):
                if config.roles[role].workflow:
                    paths.add(f"workflows.{config.roles[role].workflow}.steps")
            if any(change.path not in paths for change in schema.diff(before, after)):
                raise OrchError(
                    "unsupported_replay_change",
                    "this replay evaluates the selected role's prompt and workflow only",
                )
            row = conn.execute(
                "INSERT INTO orch_replays(board,channel,live_version,candidate_version,specification,state,created_at) "
                "VALUES (?,?,?,?,?,'ready',?)",
                (board, channel, live.version, candidate.version, _json(spec), self.store._now()),
            )
            return row.lastrowid

    def _row(self, conn, board, replay_id):
        row = conn.execute(
            "SELECT * FROM orch_replays WHERE id=? AND board=?", (replay_id, board)
        ).fetchone()
        if row is None:
            raise OrchError("not_found", "no such replay")
        return dict(row)

    def get(self, board, replay_id):
        with self.store.connection() as conn:
            row = self._row(conn, board, replay_id)
            attempts = [
                dict(r)
                for r in conn.execute(
                    "SELECT task,side,state,reason,passed,tokens,tokens_reason,latency_ms,test_exit "
                    "FROM orch_replay_attempts WHERE replay_id=? ORDER BY id",
                    (replay_id,),
                )
            ]
        report = {
            k: row[k]
            for k in (
                "id",
                "board",
                "channel",
                "state",
                "reason",
                "live_version",
                "candidate_version",
                "benchmark_id",
            )
        }
        report["attempts"] = attempts
        report["cost_basis"] = "tokens and elapsed time; no currency charge is measured"
        if row["benchmark_id"] is not None:
            entry = self.benchmarks.get(row["benchmark_id"])
            report.update(
                candidate_sha256=entry.candidate_sha256,
                promote=entry.promote,
                reasons=list(entry.reasons),
            )
            report["summary"] = {"tasks": entry.summary["tasks"]}
            for side in ("live", "candidate"):
                report["summary"][side] = {
                    k: entry.summary[side][k] for k in ("passed", "tokens", "latency_ms")
                }
            report["regressions"] = [
                r.removeprefix("regression:") for r in entry.reasons if r.startswith("regression:")
            ]
        return report

    def _state(self, replay_id, state, reason=None):
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE orch_replays SET state=?,reason=? WHERE id=?", (state, reason, replay_id)
            )

    def run(self, principal, board, replay_id, repository, *, concurrency=1, resume=False):
        """Synchronous owner-machine operation; no worker survives this call."""
        require_owner(principal)
        if type(concurrency) is not int or not 1 <= concurrency <= 3:
            raise OrchError("invalid_replay", "concurrency must be between 1 and 3")
        repository = Path(repository).resolve()
        if not repository.is_dir():
            raise OrchError("invalid_replay", "repository must be a local checkout")
        with self.store.transaction() as conn:
            row = self._row(conn, board, replay_id)
            if row["state"] == "complete":
                return self.get(board, replay_id)
            expected = "paused: plan limit" if resume else "ready"
            if row["state"] != expected:
                raise OrchError(
                    "replay_not_ready", "start a ready replay or explicitly resume a plan pause"
                )
            live, candidate = self.benchmarks._current(conn, board, row["channel"])
            if (live.version, candidate.version) != (row["live_version"], row["candidate_version"]):
                raise OrchError("stale_benchmark", "live or the candidate changed during replay")
            _protected(_checked_config(live), _checked_config(candidate))
            conn.execute(
                "UPDATE orch_replays SET state='running',reason=NULL WHERE id=?", (replay_id,)
            )
            done = {
                (r["task"], r["side"])
                for r in conn.execute(
                    "SELECT task,side FROM orch_replay_attempts WHERE replay_id=? AND state='complete'",
                    (replay_id,),
                )
            }
        spec = json.loads(row["specification"])
        configs = {"live": _checked_config(live), "candidate": _checked_config(candidate)}
        pending = iter(
            (task, side)
            for task in spec["tasks"]
            for side in configs
            if (task["id"], side) not in done
        )
        stop = Event()
        failed = None
        try:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                active = {}

                def submit():
                    if not stop.is_set():
                        item = next(pending, None)
                        if item is not None:
                            task, side = item
                            active[
                                pool.submit(_run_one, repository, task, configs[side], spec, stop)
                            ] = (task, side)

                for _ in range(concurrency):
                    submit()
                while active:
                    completed, _ = wait(active, return_when=FIRST_COMPLETED)
                    for future in completed:
                        task, side = active.pop(future)
                        result = future.result()
                        if result["state"] == "skipped":
                            continue
                        with self.store.transaction() as conn:
                            conn.execute(
                                "INSERT INTO orch_replay_attempts(replay_id,task,side,state,reason,passed,tokens,tokens_reason,latency_ms,test_exit) "
                                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (
                                    replay_id,
                                    task["id"],
                                    side,
                                    *(
                                        result[k]
                                        for k in (
                                            "state",
                                            "reason",
                                            "passed",
                                            "tokens",
                                            "tokens_reason",
                                            "latency_ms",
                                            "test_exit",
                                        )
                                    ),
                                ),
                            )
                        if result["state"] != "complete":
                            stop.set()
                            if result["reason"] == "plan_limit" or failed is None:
                                failed = result["reason"]
                    for _ in completed:
                        submit()
            if failed:
                self._state(
                    replay_id, "paused: plan limit" if failed == "plan_limit" else "refused", failed
                )
            else:
                report = self.get(board, replay_id)
                sides: dict[str, list[TaskResult]] = {side: [] for side in configs}
                for task in spec["tasks"]:
                    for side in configs:
                        attempts = [
                            r
                            for r in report["attempts"]
                            if r["task"] == task["id"] and r["side"] == side
                        ]
                        completed_attempt = next(r for r in attempts if r["state"] == "complete")
                        tokens = (
                            sum(r["tokens"] for r in attempts)
                            if all(r["tokens"] is not None for r in attempts)
                            else None
                        )
                        sides[side].append(
                            TaskResult(
                                task["id"],
                                bool(completed_attempt["passed"]),
                                None,
                                tokens,
                                sum(r["latency_ms"] for r in attempts),
                            )
                        )
                entry = self.benchmarks.record(
                    principal,
                    board,
                    row["channel"],
                    live_results=sides["live"],
                    candidate_results=sides["candidate"],
                    expected_versions=(live.version, candidate.version),
                )
                with self.store.transaction() as conn:
                    conn.execute(
                        "UPDATE orch_replays SET state='complete',benchmark_id=? WHERE id=?",
                        (entry.id, replay_id),
                    )
        except OrchError as exc:
            self._state(replay_id, "refused", exc.code)
            raise
        except BaseException:
            self._state(replay_id, "refused", "replay_interrupted")
            raise
        return self.get(board, replay_id)

    def promote(self, principal, board, replay_id, *, sha256, connections):
        require_owner(principal)
        report = self.get(board, replay_id)
        if report["state"] != "complete":
            raise OrchError("replay_incomplete", "only a completed replay can be accepted")
        return self.benchmarks.promote(
            principal, board, report["benchmark_id"], sha256=sha256, connections=connections
        )


def _file(root, name):
    path = root / _path(name)
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("replay files must be regular files inside the checkout")
    return path


def _run_one(repository, task, config, spec, stop):
    from commonplace.runner.agent import _available_memory_mb
    from commonplace.runner.replay_cli import SubscriptionCLI, capture, child_environment

    started = time.monotonic()
    result = {
        "state": "refused",
        "reason": None,
        "passed": False,
        "tokens": None,
        "tokens_reason": "model was not started",
        "latency_ms": 0,
        "test_exit": None,
    }
    try:
        available = _available_memory_mb()
        if available is not None and available < 8192:
            result["reason"] = "resource_limit_free_memory_below_8192mb"
            return result
        with TemporaryDirectory(prefix="commonplace-replay-") as directory:
            root = Path(directory) / "checkout"
            env = child_environment()
            env.update(
                GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0"
            )
            cloned = capture(
                [
                    "git",
                    "clone",
                    "--local",
                    "--no-hardlinks",
                    "--no-checkout",
                    "--quiet",
                    str(repository),
                    str(root),
                ],
                cwd=directory,
                env=env,
            )
            checked = (
                capture(
                    [
                        "git",
                        "-c",
                        "core.hooksPath=" + os.devnull,
                        "checkout",
                        "--detach",
                        task["revision"],
                    ],
                    cwd=root,
                    env=env,
                )
                if not cloned.code and not cloned.reason
                else cloned
            )
            if checked.code or checked.reason:
                result["reason"] = checked.reason or "checkout_failed"
                return result
            paths = task["files"] + task["test_files"]
            contents = {name: _file(root, name).read_text(encoding="utf-8") for name in paths}
            if sum(len(s.encode()) for s in contents.values()) > MAX_CONTEXT:
                raise ValueError("replay context too large")
            test_env = {
                k: v for k, v in child_environment().items() if k not in ("HOME", "USERPROFILE")
            }
            test_env["PYTHONDONTWRITEBYTECODE"] = "1"
            known = capture(task["test_argv"], cwd=root, env=test_env)
            if known.reason or (known.code == 0) != task["known_passed"]:
                result["reason"] = known.reason or "known_outcome_mismatch"
                return result
            if stop.is_set():
                result["state"] = "skipped"
                return result
            role = config.roles[spec["role"]]
            workflow = config.workflows.get(role.workflow)
            prompt = _json(
                {
                    "instructions": role.prompt or "",
                    "workflow": list(workflow.steps) if workflow else [],
                    "task": task["request"],
                    "files": contents,
                    "editable_files": task["files"],
                    "response_format": 'Return only a JSON object {"files": {"relative/source/file": "complete new content"}}. Do not edit tests or evaluation rules. No tools.',
                }
            )
            reply = SubscriptionCLI(spec["cli"], model=spec.get("model")).run(prompt)
            result.update(tokens=reply.tokens, tokens_reason=reply.tokens_reason)
            if reply.state != "complete":
                result.update(state=reply.state, reason=reply.reason)
                stop.set()
                return result
            try:
                answer = reply.answer.strip()
                if answer.startswith("```json\n") and answer.endswith("```"):
                    answer = answer[8:-3]
                patch = json.loads(answer)
                files = patch["files"]
                if not isinstance(files, dict) or not files or set(files) - set(task["files"]):
                    raise ValueError
                if not all(isinstance(s, str) for s in files.values()):
                    raise ValueError
                for name, text in files.items():
                    _file(root, name).write_text(text, encoding="utf-8")
            except (ValueError, TypeError, KeyError, RecursionError):
                result.update(state="refused", reason="invalid_model_patch")
                return result
            tested = capture(task["test_argv"], cwd=root, env=test_env)
            result.update(
                state="complete" if tested.reason is None else "refused",
                reason=tested.reason or ("tests_failed" if tested.code else None),
                passed=tested.code == 0 and tested.reason is None,
                test_exit=tested.code,
            )
            return result
    except (OSError, ValueError, UnicodeError):
        result["reason"] = "sandbox_or_input_failed"
        return result
    finally:
        result["latency_ms"] = round((time.monotonic() - started) * 1000)
        if result["state"] not in ("complete", "skipped"):
            stop.set()
