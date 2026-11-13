#!/usr/bin/env python3
"""Subprocess-only launch bridge double; log names, never environment values."""

import argparse
import json
import os
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fake-config", required=True)
    parser.add_argument("command", choices=["acp-mcp-call"])
    parser.add_argument("tool", choices=["t3_thread_launch"])
    parser.add_argument("launch")
    args = parser.parse_args()
    with open(args.fake_config, encoding="utf-8") as stream:
        config = json.load(stream)
    token = config.get("machine_token", "")
    entry = {
        "argv": sys.argv[1:],
        "launch": json.loads(args.launch),
        "env_names": sorted(os.environ),
        "token_seen": bool(token) and any(token in value for value in os.environ.values()),
    }
    fd = os.open(config["log"], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry) + "\n")
    mode = config["mode"]
    if mode == "sleep":
        time.sleep(config.get("sleep_seconds", 65))
    if mode == "nonzero":
        print("fake launch failed", file=sys.stderr)
        return 1
    if mode == "bad-json":
        print("not JSON")
        return 0
    if mode == "leaky":
        print(
            "refused: " + os.environ.get("T3_ACP_MCP_AUTHORIZATION", "") + " " + "x" * 60,
            file=sys.stderr,
        )
        return 1
    if mode == "diagnostic-flood":
        for stream, label in ((sys.stdout, "stdout"), (sys.stderr, "stderr")):
            print("progress first\n" + "progress\n" * 2500, file=stream)
            print("api_key=" + "sk-" + "S" * 24, file=stream)
            print(label + " tail: runtime_mode_escalation_denied", file=stream)
        return 1
    if mode == "is-error":
        print("runtime_mode_escalation_denied", file=sys.stderr)
    result = {
        "isError": mode == "is-error",
        "structuredContent": {"threadId": "demo-thread", "runId": "demo-run", "status": "running"},
    }
    if mode == "is-error":
        result["error"] = {"code": "runtime_mode_escalation_denied"}
    if mode == "no-thread-id":
        del result["structuredContent"]["threadId"]
    if mode == "no-run-id":
        del result["structuredContent"]["runId"]
    if mode == "status-failed":
        result["structuredContent"]["status"] = "failed"
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
