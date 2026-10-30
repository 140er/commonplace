"""Opt-in Claude CLI adapter; no alternate model or credential fallback."""

import json
import os
import pwd
import subprocess
import sys
from pathlib import Path

from successor import append_record


def main():
    prompt_file, handoff_id, executable = sys.argv[1:]
    destination = Path(os.environ["HANDOFF_E2E_RESULTS"])
    append_record(destination / "launches.jsonl", {"handoff_id": handoff_id, "pid": os.getpid()})
    prompt = Path(prompt_file).read_text(encoding="utf-8")
    prompt += (
        "\n\nFor this validation, recover the staging database port and its codename "
        "from the old tool results. Use thread_recall, then thread_get on the hit's exact range. "
        "Answer with the exact port and codename."
    )
    environment = dict(os.environ)
    environment.pop("CLAUDECODE", None)
    # The hook runs under a throwaway HOME; the agent needs the user's own login.
    environment["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
    try:
        process = subprocess.run(
            [
                executable,
                "-p",
                "--model",
                "claude-haiku-5-5",
                "--mcp-config",
                os.environ["HANDOFF_E2E_AGENT_TOOLS"],
                "--strict-mcp-config",
                "--allowedTools",
                "mcp__memory__thread_recall,mcp__memory__thread_get,mcp__memory__thread_chain",
                "--max-turns",
                "8",
                prompt,
            ],
            env=environment,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if process.returncode:
            append_record(
                destination / "errors.jsonl",
                {
                    "handoff_id": handoff_id,
                    "error": "ClaudeExit",
                    "code": process.returncode,
                    "stdout": process.stdout[-2000:],
                    "stderr": process.stderr[-2000:],
                },
            )
            return 1
        with (destination / f"{handoff_id}.json").open("x", encoding="utf-8") as stream:
            json.dump({"answer": process.stdout, "pid": os.getpid()}, stream)
        return 0
    except subprocess.TimeoutExpired:
        append_record(
            destination / "errors.jsonl", {"handoff_id": handoff_id, "error": "ClaudeTimeout"}
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
