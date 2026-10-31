"""Synthetic RFC 008 B1a storage/latency benchmark; no external data.

Run from the repository root: uv run python benchmarks/thread_store_v2.py
The temporary stores are removed after measurement. JSON output is suitable
for a PR table. Latencies are median wall milliseconds, warm SQLite cache.
"""

from __future__ import annotations

import base64
import json
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commonplace.handoff.thread_store import ThreadStore, ThreadStoreV2
from commonplace.handoff.transcripts import parse


def chats(count=12, turns=300):
    rng = random.Random(808)
    media = [base64.b64encode(rng.randbytes(3072)).decode() for _ in range(8)]
    sources = []
    for chat in range(count):
        records = []
        for turn in range(turns):
            words = " ".join(f"word-{rng.randrange(5000)}" for _ in range(55))
            text = f"chat {chat} turn {turn} needle {words}"
            if turn % 6 == 0:
                text += "\nimage: " + media[turn % len(media)]
            records.append(
                {
                    "type": "user" if turn % 2 == 0 else "assistant",
                    "message": {
                        "role": "user" if turn % 2 == 0 else "assistant",
                        "content": [
                            {"type": "text", "text": text},
                            {"type": "text", "text": f"extra block {turn}"},
                        ],
                    },
                }
            )
        sources.append("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records))
    return sources


def latency(call, repeats=25):
    call()
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        call()
        times.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(times), 3)


def size(store, path):
    store._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return path.stat().st_size


def main():
    sources = chats()
    parsed = [parse(source) for source in sources]
    source_bytes = sum(len(source.encode()) for source in sources)
    result = {
        "source_bytes": source_bytes,
        "chats": len(sources),
        "messages_per_chat": len(parse(sources[0])),
    }
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for label, cls, filename in (
            ("v1", ThreadStore, "threads.sqlite3"),
            ("v2", ThreadStoreV2, "threads.v2.sqlite3"),
        ):
            path = root / label / filename
            store = cls(str(path))
            try:
                started = time.perf_counter()
                ids = [
                    store.store("workspace", str(i), items, actor="benchmark").id
                    for i, items in enumerate(parsed)
                ]
                store_ms = (time.perf_counter() - started) * 1000
                bytes_used = size(store, path)
                row = {
                    "store_ms": round(store_ms, 2),
                    "bytes": bytes_used,
                    "bytes_per_source_byte": round(bytes_used / source_bytes, 4),
                    "read_50_ms": latency(lambda: store.messages(ids[0], 250, 299)),
                    "search_top_8_ms": latency(lambda: store.search(ids, "needle")),
                }
                if label == "v2":
                    row["read_50_text_ms"] = latency(
                        lambda: store._messages(ids[0], 250, 299, raw=False)
                    )
                result[label] = row
            finally:
                store.close()
        # One message per append, identical final content to the batch control.
        items = parse(
            json.dumps(
                [
                    {"role": "user", "content": f"turn {turn} needle " + "sample words " * 20}
                    for turn in range(300)
                ]
            )
        )
        overhead = {}
        for mode in ("batch", "per_turn"):
            path = root / mode / "threads.v2.sqlite3"
            store = ThreadStoreV2(str(path))
            try:
                start = time.perf_counter()
                thread = store.store(
                    "workspace",
                    "source",
                    items if mode == "batch" else items[:1],
                    actor="benchmark",
                )
                if mode == "per_turn":
                    for end in range(2, len(items) + 1):
                        store.append(thread.id, items[:end])
                assert store.messages(thread.id) == items
                overhead[mode] = {
                    "bytes": size(store, path),
                    "blocks": store._conn.execute("SELECT count(*) FROM block").fetchone()[0],
                    "chunks": len(store.chunks(thread.id)),
                    "write_ms": round((time.perf_counter() - start) * 1000, 2),
                    "compressed_frame_bytes": store._conn.execute(
                        "SELECT sum(length(zraw)+length(ztext)) FROM block"
                    ).fetchone()[0],
                }
            finally:
                store.close()
        overhead["extra_bytes"] = overhead["per_turn"]["bytes"] - overhead["batch"]["bytes"]
        result["append_overhead"] = overhead
        large = {}
        for case, count, length in (
            ("one_4mb_message", 1, 4 * 1024 * 1024),
            ("eight_300kb_tool_outputs", 8, 300 * 1024),
        ):
            unit = "needle payload words \0 "
            text = (unit * (length // len(unit) + 1))[:length]
            items = parse(
                json.dumps(
                    [
                        {"role": "tool", "content": text, "name": "read", "tool_call_id": str(i)}
                        for i in range(count)
                    ]
                )
            )
            large[case] = {}
            for label, cls, filename in (
                ("v1", ThreadStore, "threads.sqlite3"),
                ("v2", ThreadStoreV2, "threads.v2.sqlite3"),
            ):
                store = cls(str(root / case / label / filename))
                try:
                    started = time.perf_counter()
                    thread = store.store("workspace", case, items, actor="benchmark")
                    store_ms = (time.perf_counter() - started) * 1000
                    large[case][label] = {
                        "store_ms": round(store_ms, 2),
                        "chunks_ms": latency(lambda: store.chunks(thread.id), 5),
                        "search_top_8_ms": latency(lambda: store.search([thread.id], "needle"), 5),
                        "nul_query_ms": latency(lambda: store.search([thread.id], "words \0"), 5),
                    }
                    assert store.messages(thread.id) == items
                finally:
                    store.close()
        result["large_messages"] = large
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
