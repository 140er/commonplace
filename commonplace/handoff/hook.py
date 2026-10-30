"""Fail-open client hook for Claude Code and Codex. Owner: part T15.

Run ``python -m commonplace.handoff.hook --client claude-code`` with hook
JSON on stdin. See ``COMMONPLACE_URL``, ``COMMONPLACE_TOKEN``, the local
``COMMONPLACE_HANDOFF=off`` override, and ``~/.commonplace/handoff-client.json``
(``{"successor_argv": ["claude", "-p", "{prompt}"]}``). Settings are cached
for 60 seconds; a warm below-threshold call does no HTTP or full file read.

The repository's Codex adapter (``mempalace.hooks_cli._parse_harness_input``)
uses the same ``session_id`` / ``transcript_path`` fields as Claude Code.
Codex's ``thread_id`` / ``rollout_path`` aliases are also accepted. Its
documented repository integration establishes ``systemMessage`` output,
but no hook output that terminates the old agent. Codex therefore gets a
notification only: stopping the old run still needs harness support.

A private launch journal prevents repeated local launches, including after
registration fails. A crash between spawning and recording the PID leaves
an uncertain launch claim: fall back and require explicit server relaunch,
never risk starting a duplicate successor. Locks use POSIX ``fcntl``;
unavailable locking falls back to normal compaction.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from . import trigger
from .models import DEFAULT_THRESHOLD, HandoffRequest, HandoffSettings, ThreadMeta

CACHE_TTL = 60
HTTP_TIMEOUT = 30


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(server: str, token: str, route: str, body=None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    request = Request(server + route, data=data, headers=headers)
    # Only contact the explicitly configured gateway, never a proxy or redirect.
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    with opener.open(request, timeout=HTTP_TIMEOUT) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise ValueError("Invalid response")
    return result


def _private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def _atomic_text(path: Path, text: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".handoff-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _write_json(path: Path, value: dict) -> None:
    _atomic_text(path, json.dumps(value))


def _read_json(path: Path):
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return None


def _context(server: str, token: str, client: str, agent: str, task: str) -> str:
    # Bind cached settings and launch records to the authenticated context,
    # without writing the token (or a reusable credential) to disk.
    return hashlib.sha256(json.dumps([server, token, client, agent, task]).encode()).hexdigest()


@contextmanager
def _session_lock(path: Path):
    import fcntl

    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
        else:
            yield True
    finally:
        os.close(descriptor)


def _parse_settings(value: dict) -> HandoffSettings:
    if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
        raise ValueError("Invalid setting")
    threshold = value.get("threshold", DEFAULT_THRESHOLD)
    window = value.get("context_window")
    if (
        type(threshold) not in (float, int)
        or not math.isfinite(threshold)
        or not 0.50 <= threshold <= 0.95
        or (window is not None and (type(window) is not int or window <= 0))
    ):
        raise ValueError("Invalid setting")
    return HandoffSettings(enabled=value["enabled"], threshold=threshold, context_window=window)


def _settings(path: Path, context: str, server: str, token: str, agent: str, task: str):
    cached = _read_json(path)
    now = time.time()
    if isinstance(cached, dict) and cached.get("context") == context:
        at = cached.get("fetched_at")
        if type(at) in (int, float) and 0 <= now - at < CACHE_TTL:
            try:
                return _parse_settings(cached.get("effective"))
            except ValueError:
                pass
    reply = _request(
        server, token, "/api/settings/handoff?" + urlencode({"agent": agent, "task": task})
    )
    settings = _parse_settings(reply.get("effective"))
    # Cache only the three fields used locally, never launcher configuration.
    _write_json(
        path,
        {
            "context": context,
            "fetched_at": now,
            "effective": {
                "enabled": settings.enabled,
                "threshold": settings.threshold,
                "context_window": settings.context_window,
            },
        },
    )
    return settings


_PARENT_MARKER = re.compile(rb"commonplace-handoff-id: (hof_[0-9a-f]{8,64})\b")


def _parent_from_transcript(path) -> str:
    """The handoff id a successor prompt names in its last line
    (``writer.HANDOFF_ID_MARKER``), from the start of this agent's own
    transcript: how a successor without COMMONPLACE_HANDOFF_ID in its
    environment (a T3 thread) links into the chain. The server checks it."""
    try:
        with open(path, "rb") as stream:
            head = stream.read(262_144)
    except OSError:
        return ""
    found = _PARENT_MARKER.search(head)
    return found.group(1).decode("ascii") if found else ""


def _branch(cwd: str) -> str:
    if not cwd:
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
            encoding="utf-8",
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


def _output(client: str, successor: str) -> dict:
    reason = f"Context handed off. Successor: {successor}"
    if client == "codex":
        return {"systemMessage": reason}
    return {"continue": False, "stopReason": reason}


def _register(server: str, token: str, record: dict, journal: Path) -> None:
    _request(
        server,
        token,
        "/api/handoffs/" + quote(record["handoff_id"], safe="") + "/successor",
        {"agent_ref": record["agent_ref"]},
    )
    record["registered"] = True
    _write_json(journal, record)


def _report_launch_failure(server: str, token: str, handoff_id: str, error: str) -> None:
    """Tell the hub the local successor didn't start; the agent then compacts."""
    try:
        _request(
            server,
            token,
            "/api/handoffs/" + quote(handoff_id, safe="") + "/launch-failed",
            {"error": f"successor command failed: {error}"},
        )
    except Exception:
        pass


def _resume(record: dict, journal: Path, client: str, server: str, token: str):
    if record.get("state") == "manual" and isinstance(record.get("prompt_file"), str):
        return _output(client, record["prompt_file"])
    if record.get("state") != "launched":
        return None
    if not record.get("registered"):
        _register(server, token, record, journal)
    return _output(client, record["agent_ref"])


def _launch(
    reply: dict, journal: Path, context: str, cwd: str, client: str, server: str, token: str
):
    handoff = reply["handoff"]
    prompt = reply.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        raise ValueError("Missing prompt")
    config_path = Path.home() / ".commonplace" / "handoff-client.json"
    if config_path.exists():
        with config_path.open(encoding="utf-8") as stream:
            config = json.load(stream)
        if not isinstance(config, dict):
            raise ValueError("Invalid client configuration")
        argv = config.get("successor_argv", [])
    else:
        argv = []
    if not isinstance(argv, list) or any(not isinstance(arg, str) or not arg for arg in argv):
        raise ValueError("Invalid successor command")

    prompt_dir = journal.parent.parent / "handoff-prompts"
    _private_dir(prompt_dir)
    key = hashlib.sha256((context + handoff["id"]).encode()).hexdigest()
    prompt_file = prompt_dir / f"{key}.md"
    _atomic_text(prompt_file, prompt)
    record = {
        "context": context,
        "handoff_id": handoff["id"],
        "prompt_file": str(prompt_file),
        "state": "starting" if argv else "manual",
    }
    # Persist intent before spawn, then the PID before registration. A retry
    # must never start a second process after a partial success or crash.
    _write_json(journal, record)
    if not argv:
        return _output(client, str(prompt_file))
    replacements = {
        "prompt": prompt,
        "prompt_file": str(prompt_file),
        "handoff_id": handoff["id"],
        "thread_id": handoff["stored_thread_id"],
        "cwd": cwd,
    }
    argv = [
        re.sub(
            r"\{(prompt|prompt_file|handoff_id|thread_id|cwd)\}",
            lambda match: replacements[match[1]],
            arg,
        )
        for arg in argv
    ]
    environment = dict(os.environ)
    environment.update(
        COMMONPLACE_HANDOFF_ID=handoff["id"], COMMONPLACE_THREAD_ID=handoff["stored_thread_id"]
    )
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd if cwd and Path(cwd).is_dir() else prompt_dir,
            env=environment,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        try:
            if process.wait(timeout=0.05) != 0:
                raise RuntimeError("Successor exited")
        except subprocess.TimeoutExpired:
            pass
    except (OSError, RuntimeError) as exc:
        _report_launch_failure(server, token, handoff["id"], type(exc).__name__)
        raise
    record.update(state="launched", agent_ref=f"pid:{process.pid}", registered=False)
    _write_json(journal, record)
    _register(server, token, record, journal)
    return _output(client, record["agent_ref"])


def _handoff(reply: dict, journal: Path, context: str, meta: ThreadMeta, server: str, token: str):
    if reply.get("action") != "handoff":
        return None
    handoff = reply.get("handoff")
    if (
        not isinstance(handoff, dict)
        or any(
            not isinstance(handoff.get(key), str) or not handoff[key]
            for key in ("id", "stored_thread_id")
        )
        or type(reply.get("launch")) is not bool
        or handoff.get("source_thread_id", meta.thread_id) != meta.thread_id
    ):
        raise ValueError("Invalid handoff")
    if reply["launch"]:
        return _launch(reply, journal, context, meta.cwd, meta.client, server, token)
    successor = handoff.get("successor")
    if (
        isinstance(successor, dict)
        and isinstance(successor.get("agent_ref"), str)
        and successor["agent_ref"]
    ):
        return _output(meta.client, successor["agent_ref"])
    # A claimed launch without a known successor is not proof one is running.
    return None


def _run(argv, server: str, token: str):
    args = list(sys.argv[1:] if argv is None else argv)
    client = "claude-code"
    if args:
        if len(args) != 2 or args[0] != "--client" or args[1] not in ("claude-code", "codex"):
            raise ValueError("Invalid client")
        client = args[1]
    address = urlsplit(server)
    if (
        address.scheme not in ("http", "https")
        or not address.hostname
        or address.username is not None
        or address.password is not None
        or address.query
        or address.fragment
    ):
        raise ValueError("Invalid gateway URL")
    data = json.load(sys.stdin)
    if not isinstance(data, dict):
        raise ValueError("Invalid hook input")
    session = data.get("session_id")
    transcript = data.get("transcript_path")
    if client == "codex":
        session = session or data.get("thread_id")
        transcript = transcript or data.get("rollout_path")
    cwd = data.get("cwd", "")
    event = data.get("hook_event_name", "")
    if (
        not isinstance(session, str)
        or not session.strip()
        or not isinstance(transcript, str)
        or not transcript
        or not isinstance(cwd, str)
    ):
        raise ValueError("Missing hook metadata")
    if event not in ("", "PostToolUse", "Stop", "PreCompact"):
        return None
    transcript_path = Path(transcript).expanduser()
    if not transcript_path.is_file():
        raise ValueError("Missing transcript")
    agent = os.environ.get("COMMONPLACE_AGENT", "")
    task = os.environ.get("COMMONPLACE_TASK", "")
    context = _context(server, token, client, agent, task)
    cache_dir = Path.home() / ".commonplace" / "handoff-cache"
    key = hashlib.sha256(session.encode()).hexdigest()
    cache = cache_dir / f"{key}.json"
    journal = cache_dir / f"{key}.launch.json"
    # Disabled hooks only touch the settings cache. In particular, never
    # create a launch lock or inspect a journal before the enabled gate.
    try:
        _private_dir(cache_dir.parent)
        _private_dir(cache_dir)
        settings = _settings(cache, context, server, token, agent, task)
    except Exception:
        # Unknown settings must behave like off, including silent fallback.
        return None
    if not settings.enabled:
        return None
    with _session_lock(cache_dir / f"{key}.lock") as acquired:
        if not acquired:
            return None
        # A malformed journal must not erase evidence of a previous launch.
        if journal.exists():
            record = _read_json(journal)
            if not isinstance(record, dict):
                raise ValueError("Invalid launch record")
            if record.get("context") == context:
                return _resume(record, journal, client, server, token)
        usage = trigger.usage_from_tail(
            str(transcript_path),
            fmt=client,
            override_window=settings.context_window,
            configured=trigger.configured_model(cwd) if client == "claude-code" else "",
        )
        if event != "PreCompact" and not trigger.should_handoff(usage, settings):
            return None
        with transcript_path.open(encoding="utf-8", newline="") as stream:
            transcript_text = stream.read()
        meta = ThreadMeta(
            thread_id=session,
            agent=agent,
            client=client,
            model=usage.model if usage else "",
            cwd=cwd,
            branch=_branch(cwd),
            task_ref=task,
            parent_handoff_id=os.environ.get("COMMONPLACE_HANDOFF_ID", "")
            or _parent_from_transcript(transcript_path),
        )
        request = HandoffRequest(
            meta=meta, transcript=transcript_text, transcript_format=client, usage=usage
        )
        reply = _request(server, token, "/api/handoff", asdict(request))
        return _handoff(reply, journal, context, meta, server, token)


def main(argv=None) -> int:
    """Read one hook event. Every operational failure returns zero, silently."""
    server = os.environ.get("COMMONPLACE_URL", "").strip().rstrip("/")
    token = os.environ.get("COMMONPLACE_TOKEN", "").strip()
    if (
        os.environ.get("COMMONPLACE_HANDOFF", "").strip().lower() == "off"
        or not server
        or not token
    ):
        return 0
    try:
        output = _run(argv, server, token)
        if output is not None:
            print(json.dumps(output))
    except (Exception, SystemExit, KeyboardInterrupt) as error:
        try:
            # Exception messages can contain tokens, URLs or transcript text.
            print(
                f"Commonplace handoff: {type(error).__name__}; continuing normally.",
                file=sys.stderr,
            )
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
