"""Small, real projection database with adversarial source text."""

import json
import sqlite3
from pathlib import Path

from commonplace.compute.activity_math import iso

CANARY = "PROMPT_FILE_APIKEY_CANARY_641_do_not_transport"
END = 1795089600  # A fixed historical observation, not a current-time assertion.
START = END - 86400
AT = START + 60


def source(path):
    db = sqlite3.connect(path)
    tables = {
        "runs": "run_id TEXT PRIMARY KEY,thread_id TEXT,ordinal INT,status TEXT,requested_at TEXT,completed_at TEXT,payload_json TEXT",
        "threads": "thread_id TEXT PRIMARY KEY,project_id TEXT,updated_at TEXT,payload_json TEXT",
        "turn_items": "turn_item_id TEXT PRIMARY KEY,run_id TEXT,thread_id TEXT,ordinal INT,type TEXT,status TEXT,payload_json TEXT",
        "subagents": "subagent_id TEXT PRIMARY KEY,thread_id TEXT,child_thread_id TEXT,status TEXT,started_at TEXT,completed_at TEXT,run_id TEXT,payload_json TEXT",
        "runtime_requests": "thread_id TEXT,kind TEXT,status TEXT,created_at TEXT,resolved_at TEXT,payload_json TEXT",
        "run_attempts": "attempt_id TEXT PRIMARY KEY,run_id TEXT,status TEXT,payload_json TEXT",
        "provider_turns": "provider_turn_id TEXT PRIMARY KEY,thread_id TEXT,ordinal INT,started_at TEXT,completed_at TEXT,run_attempt_id TEXT,payload_json TEXT",
        "context_transfers": "source_thread_id TEXT,target_thread_id TEXT,target_run_id TEXT,type TEXT,updated_at TEXT,status TEXT,payload_json TEXT",
    }
    for name, columns in tables.items():
        db.execute(f"CREATE TABLE orchestration_v2_projection_{name} ({columns})")
    db.execute(
        "CREATE INDEX item_run_ordinal ON orchestration_v2_projection_turn_items(run_id,ordinal)"
    )
    db.execute(
        "CREATE INDEX turn_thread_ordinal ON orchestration_v2_projection_provider_turns(thread_id,ordinal)"
    )
    db.execute(
        "CREATE TABLE projection_thread_pull_requests(thread_id TEXT,host TEXT,repository TEXT,number INT,snapshot_json TEXT)"
    )

    def insert(table, **fields):
        keys = list(fields)
        values = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in fields.values()]
        db.execute(
            f"INSERT INTO orchestration_v2_projection_{table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",
            values,
        )

    for tid, state, synced in (
        ("main", "open", AT + 50),
        ("child", "merged", AT + 170),
        ("queued", None, AT),
    ):
        links = (
            []
            if state is None
            else [
                {
                    "host": "github.example.test",
                    "repository": "sample/project",
                    "number": 17,
                    "snapshot": {
                        "state": state,
                        "syncedAt": iso(synced),
                        "title": CANARY,
                        "headBranch": CANARY,
                    },
                    "url": CANARY,
                }
            ]
        )
        insert(
            "threads",
            thread_id=tid,
            project_id=CANARY,
            updated_at=iso(AT + 180),
            payload_json={
                "title": CANARY,
                "workspaceRoot": CANARY,
                "createdBy": "user" if tid == "main" else "agent",
                "pullRequests": links,
            },
        )
    for rid, tid, ordinal, start, end in (
        ("r1", "main", 1, 0, 120),
        ("r2", "main", 2, 60, 180),
        ("r3", "child", 1, 40, 140),
    ):
        insert(
            "runs",
            run_id=rid,
            thread_id=tid,
            ordinal=ordinal,
            status="completed",
            requested_at=iso(AT + start),
            completed_at=iso(AT + end),
            payload_json={
                "startedAt": iso(AT + start),
                "modelSelection": {"model": "gpt-6.1-sol"},
                "prompt": CANARY,
            },
        )
        insert(
            "run_attempts",
            attempt_id="a" + rid,
            run_id=rid,
            status="completed",
            payload_json={
                "reason": "initial",
                "startedAt": iso(AT + start),
                "completedAt": iso(AT + end),
                "input": CANARY,
            },
        )
    insert(
        "runs",
        run_id="q",
        thread_id="queued",
        ordinal=1,
        status="queued",
        requested_at=iso(AT),
        completed_at=None,
        payload_json={"modelSelection": {"model": CANARY}, "prompt": CANARY},
    )
    for sid, child, name in (
        ("duplicate", "child", "gpt-6.1-sol"),
        ("native", None, "gpt-6.1-terra"),
    ):
        insert(
            "subagents",
            subagent_id=sid,
            thread_id="main",
            child_thread_id=child,
            run_id="r1",
            status="completed",
            started_at=iso(AT + 80),
            completed_at=iso(AT + 100),
            payload_json={"modelSelection": {"model": name}, "prompt": CANARY, "result": CANARY},
        )
    for iid, kind, start, end, extra in (
        (
            "tool",
            "dynamic_tool",
            10,
            90,
            {"toolName": CANARY, "input": {"prompt": CANARY}, "output": CANARY},
        ),
        (
            "cmd",
            "command_execution",
            40,
            100,
            {"input": "pytest " + CANARY, "exitCode": 7, "output": CANARY},
        ),
        ("compact", "compaction", 70, 110, {"driver": CANARY, "summary": CANARY}),
        (
            "edit",
            "file_change",
            20,
            21,
            {
                "diffStr": "--- before\n+++ after\n-" + CANARY + "\n+" + CANARY,
                "changes": [{"path": CANARY}],
            },
        ),
    ):
        insert(
            "turn_items",
            turn_item_id=iid,
            run_id="r1",
            thread_id="main",
            ordinal={"tool": 1, "cmd": 2, "compact": 3, "edit": 4}[iid],
            type=kind,
            status="completed",
            payload_json={"startedAt": iso(AT + start), "completedAt": iso(AT + end), **extra},
        )
    insert(
        "runtime_requests",
        thread_id="main",
        kind="user_input",
        status="resolved",
        created_at=iso(AT + 80),
        resolved_at=iso(AT + 120),
        payload_json={"answers": {"answer": [CANARY]}, "question": CANARY},
    )
    insert(
        "runtime_requests",
        thread_id="main",
        kind="user_input",
        status="resolved",
        created_at=iso(AT + 85),
        resolved_at=iso(AT + 95),
        payload_json={"decision": "cancel", "answers": {"answer": [CANARY]}},
    )
    insert(
        "provider_turns",
        provider_turn_id="turn1",
        thread_id="main",
        ordinal=1,
        started_at=iso(AT + 1),
        completed_at=iso(AT + 20),
        run_attempt_id="ar1",
        payload_json={
            "turnTokenUsage": {
                "usageScope": "main_agent",
                "usageStatus": "complete",
                "hasSubagents": True,
                "inputTokens": 100,
                "cachedInputTokens": 80,
                "outputTokens": 20,
            },
            "tokenUsage": {"usedTokens": 95, "maxTokens": 100},
            "reasoning": CANARY,
        },
    )
    insert(
        "provider_turns",
        provider_turn_id="turn2",
        thread_id="main",
        ordinal=2,
        started_at=iso(AT + 30),
        completed_at=iso(AT + 40),
        run_attempt_id="ar1",
        payload_json={
            "tokenUsage": {"inputTokens": 999999, "usedTokens": 20, "maxTokens": 100},
            "message": CANARY,
        },
    )
    db.commit()
    db.close()
    return Path(path)


def logs(path):
    path = Path(path)
    data = {
        "name": CANARY,
        "durationMs": 1000,
        "endTimeUnixNano": str((AT + 90) * 10**9),
        "traceId": "same",
        "spanId": "span",
        "attributes": {CANARY: CANARY},
        "events": [{"name": CANARY}],
        "exit": {"_tag": "Failure", "cause": CANARY},
    }
    path.write_text(json.dumps(data) + "\ninvalid\n")
    return path
