"""Record a synthetic CLI invocation and emit a fixed answer."""

import argparse
import json
import os
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--exit", type=int, default=0)
    parser.add_argument("--sleep", type=float, default=0)
    parser.add_argument("--empty", action="store_true")
    # Print a claude-style JSON result (with fields the client must not forward).
    parser.add_argument("--json-result", action="store_true")
    parser.add_argument("--cost", type=float)
    parser.add_argument("--tokens", default="")  # input,cache_creation,cache_read,output
    args = parser.parse_args()
    record = {
        "argv": sys.argv[1:],
        "cwd": sorted(os.listdir()),
        "env": dict(os.environ),
        "stdin": sys.stdin.read(),
    }
    Path(args.record).write_text(json.dumps(record), encoding="utf-8")
    if args.sleep:
        time.sleep(args.sleep)
    if args.json_result:
        result = {
            "type": "result",
            "result": "Fake answer from the runner.",
            "session_id": "session-canary",
            "cwd": "/canary/path",
            "model": "canary-model",
        }
        if args.tokens:
            first, created, read, out = (int(n) for n in args.tokens.split(","))
            result["usage"] = {
                "input_tokens": first,
                "cache_creation_input_tokens": created,
                "cache_read_input_tokens": read,
                "output_tokens": out,
                "service_tier": "canary-tier",
            }
        if args.cost is not None:
            result["total_cost_usd"] = args.cost
        print(json.dumps(result))
    elif not args.empty:
        print("Fake answer from the runner.")
    return args.exit


if __name__ == "__main__":
    sys.exit(main())
