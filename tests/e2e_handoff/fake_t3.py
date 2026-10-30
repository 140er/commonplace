"""Executable fake T3 and a native-session fixture. Never contacts a model."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


def source_fixture(root, monkeypatch, *, provider="claudeAgent", worktree=False, transcript=None):
    from commonplace.handoff.t3 import T3

    root.mkdir(parents=True, exist_ok=True)
    state = root / "t3" / "userdata"
    state.mkdir(parents=True)
    project = root / "project"
    project.mkdir()
    cwd = root / "existing-worktree" if worktree else project
    cwd.mkdir(exist_ok=True)
    native_id = "12345678-1234-1234-1234-123456789abc"
    provider_home = root / "provider"
    if provider == "codex":
        path = provider_home / "sessions/2026/10/30" / f"rollout-date-{native_id}.jsonl"
        content = '{"type":"session_meta","payload":{"id":"native"}}\n'
    else:
        path = provider_home / "projects/project" / f"{native_id}.jsonl"
        content = '{"type":"user","message":{"role":"user","content":"Keep this verbatim."}}\n'
    path.parent.mkdir(parents=True)
    path.write_bytes(transcript.read_bytes() if transcript else content.encode())
    view = {
        "thread": {
            "threadId": "mcp:source",
            "projectId": "project",
            "worktreePath": str(cwd) if worktree else None,
            "providerInstanceId": provider,
            "model": "old-model",
            "branch": "feat/source",
        }
    }
    native = {
        "driver": provider,
        "providerInstanceId": provider,
        "ownerNodeId": None,
        "nativeThreadRef": {"nativeId": native_id},
    }
    with sqlite3.connect(state / "statev2.sqlite") as db:
        db.execute("CREATE TABLE projection_projects (project_id TEXT, workspace_root TEXT)")
        db.execute("INSERT INTO projection_projects VALUES (?, ?)", ("project", str(project)))
        db.execute(
            "CREATE TABLE orchestration_v2_projection_provider_threads "
            "(thread_id TEXT, payload_json TEXT, first_run_ordinal INTEGER, updated_at TEXT)"
        )
        db.execute(
            "INSERT INTO orchestration_v2_projection_provider_threads VALUES (?, ?, 1, 'now')",
            ("mcp:source", json.dumps(native)),
        )
    (state / "settings.json").write_text(
        json.dumps({"providerInstances": {provider: {"config": {"homePath": str(provider_home)}}}})
    )
    (state / "server-runtime.json").write_text(json.dumps({"origin": "http://127.0.0.1:1"}))
    config = root / "fake.json"
    config.write_text(json.dumps({"view": view, "log": str(root / "calls.jsonl")}))
    monkeypatch.setenv("HANDOFF_FAKE_T3", str(config))
    adapter = T3(root / "t3", executable=(sys.executable, str(Path(__file__).resolve())))
    return SimpleNamespace(
        adapter=adapter,
        config=config,
        state=state,
        cwd=cwd,
        project=project,
        path=path,
        native=native,
        view=view,
        log=root / "calls.jsonl",
    )


def main():
    config = json.loads(Path(os.environ["HANDOFF_FAKE_T3"]).read_text())
    arguments = sys.argv[1:]
    with Path(config["log"]).open("a") as stream:
        stream.write(json.dumps({"argv": arguments, "cwd": os.getcwd()}) + "\n")
    if arguments[:2] == ["thread", "show"]:
        print(json.dumps(config["view"]))
        return 0
    if arguments[:3] == ["auth", "session", "issue"]:
        print(json.dumps({"sessionId": "fake-session", "token": "fake-token"}))
        return 0
    if arguments[:3] == ["auth", "session", "revoke"]:
        return 1 if config.get("revoke_fails") else 0
    if arguments[:2] == ["thread", "settle"]:
        if config.get("settle_fails"):
            return 1
        print(json.dumps({"settlesWhenTurnEnds": True}))
        return 0
    bridge = arguments[:2] == ["acp-mcp-call", "t3_thread_launch"]
    if bridge:
        assert os.environ["T3_ACP_MCP_AUTHORIZATION"] == "Bearer fake-token"
        options = json.loads(arguments[2])
        assert options["workspaceStrategy"] == {
            "type": "existing_worktree",
            "worktreePath": os.getcwd(),
        }
        prompt = options["message"]
        provider = options["modelSelection"]["instanceId"]
    else:
        assert arguments[:2] == ["thread", "start"], arguments
        assert arguments[2] == os.getcwd() and arguments[3] == "-"
        prompt = sys.stdin.read()
        provider = arguments[arguments.index("--provider") + 1]
    if config.get("start_fails"):
        return 1
    if config.get("invalid_reply"):
        print("unconfirmed start")
        return 0
    prompt_path = Path(config["log"]).with_suffix(".prompt")
    prompt_path.write_text(prompt, newline="")
    if config.get("successor"):
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in ("COMMONPLACE_HANDOFF_ID", "COMMONPLACE_THREAD_ID")
            and not key.startswith("T3_ACP_MCP_")
        }
        environment["HANDOFF_FAKE_PROVIDER"] = provider
        subprocess.run(
            [sys.executable, config["successor"], str(prompt_path)],
            env=environment,
            check=True,
            timeout=15,
        )
    result = {
        "threadId": config.get("successor_id", "mcp:successor"),
        "runId": config.get("run_id", "run:new"),
        "status": config.get("run_status", "starting"),
    }
    print(json.dumps({"structuredContent": result} if bridge else result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
