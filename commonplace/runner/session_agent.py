"""One fenced session command per runner pass; no model call for thread inspection."""

import json
import subprocess
import sys
import time

from . import jobs


def _effect(config, command, env, secrets):
    action, thread = command["action"], command["thread_id"]
    if action == "launch":
        tool = "t3_thread_launch"
        args = {
            "title": f"Orchestrator {command['board_slug']} [commonplace-session:{command['job_id']}]",
            "projectId": config["projects"][command["project"]]["project_id"],
            "topic": command["topic"],
            "runtimeMode": command["runtime_mode"],
            "workspaceStrategy": {"type": "root"},
            "message": command["message"],
            "modelSelection": command["modelSelection"],
        }
    else:
        tool, args = {
            "continue": (
                "t3_thread_send",
                {"threadId": thread, "mode": "auto", "message": command["message"]},
            ),
            "inspect": (
                "t3_thread_read",
                {"threadId": thread, "limit": 1, "maxCharsPerItem": 1, "runLimit": 1},
            ),
            "stop": ("t3_thread_interrupt", {"threadId": thread}),
        }[action]
    if any(secret and secret in json.dumps(args) for secret in secrets):
        return {"outcome": "uncertain"}
    try:
        response = subprocess.run(
            [*config["t3_executable"], "acp-mcp-call", tool, json.dumps(args)],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=config["t3_timeout_seconds"],
            check=False,
        )
        data = json.loads(response.stdout)
        if response.returncode or data.get("isError", False):
            return {"outcome": "uncertain"}
        content = data["structuredContent"]
        if action == "inspect":
            observed = content["thread"]
            if observed["threadId"] != thread:
                return {"outcome": "uncertain"}
            status = observed["status"]
            if status in ("idle", "completed", "interrupted", "failed", "cancelled", "rolled_back"):
                return {"outcome": "finished"}
            if status not in ("preparing", "queued", "starting", "running", "waiting"):
                return {"outcome": "uncertain"}
            return {"outcome": "running"}
        if action == "stop":
            # Interrupt acknowledgement is not proof that the model turn ended.
            observed = _effect(config, {**command, "action": "inspect"}, env, secrets)
            return {"outcome": "stopped"} if observed["outcome"] == "finished" else observed
        bound = content["threadId"]
        if (
            not isinstance(bound, str)
            or jobs.THREAD_ID.fullmatch(bound) is None
            or (thread and bound != thread)
        ):
            return {"outcome": "uncertain"}
        if not content.get("runId") or content.get("status") in (
            "failed",
            "cancelled",
            "interrupted",
            "rolled_back",
        ):
            return {"outcome": "uncertain"}
        return {"outcome": "running", "thread_id": bound}
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError, AttributeError):
        return {"outcome": "uncertain"}


def _deliver(hub, path, entry):
    from .agent import _HubError

    if entry["outcome"] == "spawning":
        entry["outcome"] = "uncertain"
    try:
        hub.post("sessions/result", entry)
    except _HubError as exc:
        if exc.kind != "fenced":
            raise
    path.unlink()


def tick(hub, config, journal, env, secrets):
    from .agent import _local_grant_code, _save

    # Session journals are separate from per-card start journals.
    path = journal / "session-command.json"
    if path.exists():
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(entry, dict)
                or entry.keys() - {"lease_token", "outcome", "thread_id", "code"}
                or not isinstance(entry.get("lease_token"), str)
                or not 1 <= len(entry["lease_token"]) <= 256
                or entry.get("outcome")
                not in ("spawning", "running", "finished", "stopped", "uncertain", "refused")
                or (entry["outcome"] == "refused" and entry.get("code") not in jobs.PRESPAWN_CODES)
                or ("code" in entry and entry["outcome"] != "refused")
                or (
                    "thread_id" in entry
                    and (
                        not isinstance(entry["thread_id"], str)
                        or jobs.THREAD_ID.fullmatch(entry["thread_id"]) is None
                    )
                )
            ):
                raise ValueError("invalid session journal")
        except (ValueError, UnicodeError, RecursionError, TypeError):
            path.rename(path.with_name(path.name + f".quarantine-{time.time_ns()}"))
            print("runner: invalid session journal quarantined", file=sys.stderr)
        else:
            _deliver(hub, path, entry)
    command = hub.post("sessions/lease", {})
    if command is None:
        return
    token = command["lease_token"]
    invalid = (
        command.get("action") not in ("launch", "continue", "inspect", "stop")
        or not isinstance(command.get("message"), str)
        or len(command["message"]) > 120_000
        or not isinstance(command.get("modelSelection"), dict)
        or (
            command["action"] != "launch"
            and (
                not isinstance(command.get("thread_id"), str)
                or jobs.THREAD_ID.fullmatch(command["thread_id"]) is None
            )
        )
    )
    code = "job_invalid" if invalid else _local_grant_code(config, command)
    if code:
        entry = {"lease_token": token, "outcome": "refused", "code": code}
        _save(path, entry)
        _deliver(hub, path, entry)
        return
    if not hub.post("sessions/starting", {"lease_token": token})["proceed"]:
        return
    entry = {"lease_token": token, "outcome": "spawning"}
    _save(path, entry)
    entry.update(_effect(config, command, env, secrets))
    _save(path, entry)
    _deliver(hub, path, entry)
