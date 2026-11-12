#!/usr/bin/env python3
"""Memory of N embedding processes: each with its own model vs one shared service.

Starts N worker processes that each embed one query and one batch of
documents, the way a search and a small mine do, and reads every process's
memory from ``/proc/<pid>/smaps_rollup`` while all of them are still alive.
Runs it twice: with the model in each process (the default), and with the
workers pointed at one ``mempalace.embedding_service``.

    uv run python benchmarks/embedding_service_memory.py --workers 4

Linux only. Uses the installed MiniLM model and never downloads one.
Informational: it prints numbers and asserts nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile

WORKER = """
import json, sys
import mempalace.embedding as embedding

def memory():
    fields = {}
    with open("/proc/self/smaps_rollup") as handle:
        for line in handle:
            key = line.split(":")[0]
            if key in ("Rss", "Pss", "Pss_Anon"):
                fields[key] = int(line.split()[1]) // 1024
    return fields

batch = int(sys.argv[1])
ef = embedding.get_embedding_function(model="minilm")
ef(input=["where did we decide on the auth flow?"])
after_query = memory()
ef(input=["a drawer of verbatim text about the project " * 20] * batch)
print(json.dumps({"after_query": after_query, "after_batch": memory()}), flush=True)
sys.stdin.read()  # stay alive until every process has been measured
"""


def run_workers(count: int, batch: int, switch: str) -> list:
    env = {
        **os.environ,
        "MEMPALACE_EMBEDDING_DEVICE": "cpu",
        "MEMPALACE_EMBEDDING_SERVICE": switch,
    }
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", WORKER, str(batch)],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        for _ in range(count)
    ]
    try:
        return [json.loads(process.stdout.readline()) for process in processes]
    finally:
        for process in processes:
            process.communicate("")


def total(results: list, stage: str, field: str = "Pss") -> int:
    return sum(result[stage][field] for result in results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--batch", type=int, default=64, help="documents in the batch embed")
    args = parser.parse_args()

    from mempalace.embedding_service import EmbeddingServiceClient

    in_process = run_workers(args.workers, args.batch, "off")

    directory = tempfile.mkdtemp(prefix="mp-emb-bench-")
    socket_path = os.path.join(directory, "embedding.sock")
    service = subprocess.Popen(
        [sys.executable, "-m", "mempalace.embedding_service", "--socket", socket_path],
        env={
            **os.environ,
            "MEMPALACE_EMBEDDING_DEVICE": "cpu",
            "MEMPALACE_EMBEDDING_MODEL": "minilm",
        },
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for line in service.stderr:
            if "Embedding service ready" in line:
                break
        else:
            print("the embedding service did not start", file=sys.stderr)
            return 1
        shared = run_workers(args.workers, args.batch, socket_path)
        status = EmbeddingServiceClient(socket_path, "minilm").ping()
    finally:
        service.terminate()
        service.wait(timeout=30)

    print(f"{args.workers} processes, one query then a {args.batch}-document batch each (MiB)")
    print(f"{'':34}{'after query':>12}{'after batch':>12}")
    for label, results in (("model in every process", in_process), ("shared service", shared)):
        per_process = sorted(result["after_batch"]["Pss"] for result in results)
        print(
            f"  {label + ': workers, Pss sum':32}{total(results, 'after_query'):>12}"
            f"{total(results, 'after_batch'):>12}   per process {per_process}"
        )
    print(f"  {'shared service: service Rss':32}{'':>12}{status['rss_mib']:>12}")
    print(
        f"  {'shared service: total':32}{'':>12}"
        f"{total(shared, 'after_batch') + status['rss_mib']:>12}"
    )
    print(f"  model loads: {args.workers} in-process, {status['stats']['model_loads']} shared")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
