"""A fresh process that discovers the answer exclusively through Memory tools.

Invoked by the real client hook as successor.py {prompt_file} {handoff_id}.
The environment supplies transport/result paths, never the planted answer.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from wire import ThreadClient


def append_record(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        raw = (json.dumps(value) + "\n").encode("utf-8")
        assert os.write(descriptor, raw) == len(raw), "Incomplete diagnostic record"
    finally:
        os.close(descriptor)


def main():
    prompt_file, handoff_id = sys.argv[1:]
    destination = Path(os.environ["HANDOFF_E2E_RESULTS"])
    append_record(destination / "launches.jsonl", {"handoff_id": handoff_id, "pid": os.getpid()})
    try:
        prompt = Path(prompt_file).read_text(encoding="utf-8")
        assert not re.search(r"staging (?:DB|database) port is \d+", prompt)
        assert not re.search(r"codename [A-Z][A-Z0-9-]+", prompt)
        pointer = re.search(r"^Thread: (thr_[0-9a-f]+)$", prompt, re.MULTILINE)
        assert pointer, "Prompt has no stored thread pointer"
        thread_id = pointer[1]
        assert os.environ["COMMONPLACE_HANDOFF_ID"] == handoff_id
        assert os.environ["COMMONPLACE_THREAD_ID"] == thread_id
        client = ThreadClient(os.environ["COMMONPLACE_URL"], os.environ["COMMONPLACE_TOKEN"])
        client.initialize()
        tools = client.rpc("tools/list")["tools"]
        assert {"thread_recall", "thread_get", "thread_chain"} <= {tool["name"] for tool in tools}
        hits = client.tool(
            "thread_recall", thread_id=thread_id, query="staging database port", follow_chain=True
        )["hits"]
        answer = None
        for hit in hits:
            # Fetch exact ranges; the answer is never extracted from recall's snippet.
            messages = client.tool(
                "thread_get",
                thread_id=hit["thread_id"],
                start=hit["seq_start"],
                end=hit["seq_end"],
            )["messages"]
            for message in messages:
                found = re.search(
                    r"staging (?:DB|database) port is (\d+), codename ([A-Z][A-Z0-9-]+)",
                    message["text"],
                )
                if found:
                    assert message["kind"] == "tool_result"
                    assert found[1] not in prompt and found[2] not in prompt
                    answer = {"port": found[1], "codename": found[2]}
                    break
            if answer:
                break
        assert answer, "No answer in fetched Memory ranges"
        chain = client.tool("thread_chain", thread_id=thread_id)["threads"]

        # The hook registers this PID. Wait for registration before handing off
        # again, so generation two really originates in the registered successor.
        deadline = time.monotonic() + 15
        while True:
            record = client.json("GET", f"/api/handoffs/{handoff_id}")
            if record["state"] == "done":
                assert record["successor"]["agent_ref"] == f"pid:{os.getpid()}"
                break
            assert time.monotonic() < deadline, "Successor registration timed out"
            time.sleep(0.025)

        child_hook = None
        if len(chain) == 1 and os.environ.get("HANDOFF_E2E_CHAIN_PAYLOAD"):
            payload = json.loads(Path(os.environ["HANDOFF_E2E_CHAIN_PAYLOAD"]).read_text())
            payload["session_id"] = record["successor"]["agent_ref"]
            child = subprocess.run(
                [sys.executable, "-m", "commonplace.handoff.hook", "--client", "claude-code"],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=45,
                check=True,
            )
            child_hook = {"stdout": child.stdout, "stderr": child.stderr}
            assert json.loads(child.stdout)["continue"] is False, child_hook
        result = {
            **answer,
            "handoff_id": handoff_id,
            "thread_id": thread_id,
            "pid": os.getpid(),
            "chain": chain,
            "calls": client.calls,
            "child_hook": child_hook,
        }
        # Exclusive creation plus the append-only launch log exposes duplicate launches.
        with (destination / f"{handoff_id}.json").open("x", encoding="utf-8") as stream:
            json.dump(result, stream)
        return 0
    except Exception as error:
        append_record(
            destination / "errors.jsonl",
            {"handoff_id": handoff_id, "error": type(error).__name__, "detail": str(error)[:1000]},
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
