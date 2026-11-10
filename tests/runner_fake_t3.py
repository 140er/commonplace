#!/usr/bin/env python3
"""Subprocess-only launch bridge double; log names, never environment values."""

import argparse
import json
import os
import sys
import time


def host_call(config, operation, body):
    from urllib.request import ProxyHandler, Request, build_opener

    request = Request(
        f"http://127.0.0.1:{config['host_port']}/{operation}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with build_opener(ProxyHandler({})).open(request, timeout=2) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fake-config", required=True)
    parser.add_argument("command", choices=["acp-mcp-call"])
    parser.add_argument("tool", choices=["t3_thread_launch", "t3_run_stop"])
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
    if args.tool == "t3_run_stop":
        request = json.loads(args.launch)
        if mode == "disconnect":
            host_call(config, "disconnect", request)
            return 1
        content = {**request, "contractVersion": 1, "status": "running"}
        if mode == "unsupported":
            print(json.dumps({"isError": True, "structuredContent": {"code": "unsupported"}}))
            return 0
        if "host_port" in config and mode != "false-confirm":
            print(json.dumps(host_call(config, "stop", request)))
            return 0
        if mode == "false-confirm":
            content.update(status="stopped")  # no terminal evidence
        print(json.dumps({"isError": False, "structuredContent": content}))
        return 0
    if mode == "sleep":
        time.sleep(config.get("sleep_seconds", 65))
    if mode == "nonzero":
        print("fake launch failed", file=sys.stderr)
        return 1
    if mode == "bad-json":
        print("not JSON")
        return 0
    result = {
        "isError": mode == "is-error",
        "structuredContent": {"threadId": "demo-thread", "runId": "demo-run", "status": "running"},
    }
    if mode == "no-thread-id":
        del result["structuredContent"]["threadId"]
    if mode == "no-run-id":
        del result["structuredContent"]["runId"]
    if mode == "status-failed":
        result["structuredContent"]["status"] = "failed"
    if "host_port" in config:
        result = host_call(config, "launch", {"ignore": mode == "ignored"})
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
