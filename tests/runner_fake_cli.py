"""Record a synthetic CLI invocation and emit a fixed answer."""

import argparse
import json
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    record = {
        "argv": sys.argv[1:],
        "cwd": sorted(os.listdir()),
        "env": dict(os.environ),
        "stdin": sys.stdin.read(),
    }
    Path(args.record).write_text(json.dumps(record), encoding="utf-8")
    print("Fake answer from the runner.")


if __name__ == "__main__":
    main()
