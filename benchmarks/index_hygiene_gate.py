"""Synthetic recall gate with the real compact writer and a legacy SQLite seam.

Run ``uv run python -m benchmarks.index_hygiene_gate [--strategy union]``.
Use ``--embedding minilm`` to measure the same corpus with the local CPU model
(its public model weights may download on first use). Synthetic is the fast,
offline regression mode; it is not sufficient to authorize a production policy.
Exit 1 means the policy is blocked, including the expected vector-only recall
failure. ``default`` omits candidate_strategy at the real mempalace_search
handler, so a future change to its default is measured without changing here.
Use ``--backend compact`` to exercise real ``add_drawer`` writes and their
physical splits, index provenance and server-side keyword-only policy. The
default sqlite_exact mode retains the original benchmark-only policy seam.

The offline embedder is a deterministic bag of hashed words with a small,
query-independent synonym dictionary. This tests retrieval plumbing, not a
model's semantic quality. Paraphrases retain a topical keyword anchor: truly
zero-overlap tool-output paraphrases cannot be recovered by keyword indexing.
A passing synthetic gate is necessary, not evidence of recall on private data.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import inspect
import json
import math
import os
import random
import re
import sqlite3
import sys
import tempfile
from collections import Counter
from contextlib import closing, contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from commonplace.handoff.transcripts import parse
from mempalace.backends.embedding_wrapper import _embed_texts as _product_embed_texts
from mempalace.index_hygiene import find_base64_spans, index_text
from mempalace.lexical import tokenize


@dataclass(frozen=True)
class Message:
    id: str
    kind: str
    text: str
    raw: str
    # Independent fixture annotations, not detector output.
    payloads: tuple[str, ...] = ()


@dataclass(frozen=True)
class Needle:
    query: str
    target: str
    kind: str
    match: str


@dataclass(frozen=True)
class Corpus:
    transcripts: dict[str, str]
    messages: tuple[Message, ...]
    needles: tuple[Needle, ...]


def synthetic_corpus(*, seed: int = 38, noise: int = 24) -> Corpus:
    """Generate both JSONL shapes with native tool calls/results and images.

    Each message fits a single benchmark drawer; hygiene is applied to the
    whole parsed message before that drawer is built. Images deliberately
    repeat across messages and formats; source text and raw JSON are retained.
    """
    if noise < 0:
        raise ValueError("noise must be nonnegative")
    rng = random.Random(seed)
    image = base64.b64encode(b"\x89PNG\r\n\x1a\n" + rng.randbytes(1528)).decode()
    signature = base64.urlsafe_b64encode(rng.randbytes(300)).decode()
    path = "/workspace/generated-source/" + "long-component/sub-directory/" * 12 + "file.py"
    examples = [
        ("message", "user", "The observatory launch code is violet comet.", ""),
        ("message", "assistant", "The greenhouse watering schedule starts at sunrise.", ""),
        ("tool_call", "assistant", '{"command":"inspect archive backup status"}', ""),
        ("tool_result", "tool", "The archive backup password is in the copper vault.", ""),
        ("tool_result", "tool", "The compiler cache directory is amber-cache.", image),
        ("message", "user", f"Keep the source path {path} and id {'abcdef0123456789' * 20}.", ""),
        ("other", "assistant", signature, signature),
    ]
    # Shared vocabulary competes for both vector and lexical slots. These
    # passages discuss different facts, rather than restating the answers.
    for role, text in (
        ("tool", "Archive inventory check: twelve bundles passed integrity validation."),
        ("assistant", "The archive contains release notes; old bundles are retained for audits."),
        ("tool", "Backup transfer finished: copied two bundles; no integrity errors."),
        ("assistant", "The backup retention schedule keeps weekly bundles for a month."),
        ("tool", "Password rotation check: the next scheduled rotation is next month."),
        ("assistant", "Password rotation requires approval and a recorded maintenance window."),
        ("tool", "Cache eviction check: four expired entries were removed successfully."),
        ("assistant", "Cache eviction runs after a successful build to remove obsolete artifacts."),
    ):
        examples.append(("tool_result" if role == "tool" else "message", role, text, ""))
    for number in range(noise):
        examples.append(
            ("tool_result", "tool", f"Routine build screenshot number {number}.", image)
        )
    # Enough embedded conversation remains to make top-10 share meaningful.
    for number in range(12):
        examples.append(
            ("message", "assistant", f"Routine project discussion number {number}.", "")
        )

    transcripts = {}
    messages = []
    needles = []
    for fmt in ("claude-code", "codex"):
        records = []
        for kind, role, text, blob in examples:
            if fmt == "claude-code":
                if kind == "tool_call":
                    block = {
                        "type": "tool_use",
                        "id": "call",
                        "name": "shell",
                        "input": json.loads(text),
                    }
                elif kind == "tool_result":
                    content = [{"type": "text", "text": text}]
                    if blob:
                        content.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": blob,
                                },
                            }
                        )
                    block = {"type": "tool_result", "tool_use_id": "call", "content": content}
                elif kind == "other":
                    block = {"type": "opaque_signature", "signature": blob}
                else:
                    block = {"type": "text", "text": text}
                records.append(
                    {
                        "type": "user" if role in {"user", "tool"} else "assistant",
                        "message": {"role": "user" if role == "tool" else role, "content": [block]},
                    }
                )
            else:
                if kind == "tool_call":
                    payload = {
                        "type": "function_call",
                        "name": "shell",
                        "call_id": "call",
                        "arguments": text,
                    }
                elif kind == "tool_result":
                    output = (
                        text
                        if not blob
                        else [
                            {"type": "output_text", "text": text},
                            {"type": "input_image", "image_url": f"data:image/png;base64,{blob}"},
                        ]
                    )
                    payload = {"type": "function_call_output", "call_id": "call", "output": output}
                elif kind == "other":
                    payload = {"type": "reasoning", "encrypted_content": blob}
                else:
                    payload = {
                        "type": "message",
                        "role": role,
                        "content": [{"type": "output_text", "text": text}],
                    }
                records.append({"type": "response_item", "payload": payload})
        source = "".join(json.dumps(record) + "\n" for record in records)
        transcripts[fmt] = source
        parsed = parse(source, fmt)
        assert len(parsed) == len(examples)
        for number, message in enumerate(parsed):
            # Unknown/encrypted records have no readable text: retain their
            # exact raw source, as an index adapter must do for opaque data.
            body = message.text or message.raw
            blob = examples[number][3]
            messages.append(
                Message(f"{fmt}-{number}", message.kind, body, message.raw, (blob,) if blob else ())
            )
        needles.extend(
            [
                Needle("observatory launch code", f"{fmt}-0", "conversation", "keyword"),
                Needle(
                    "greenhouse irrigation timetable dawn", f"{fmt}-1", "conversation", "paraphrase"
                ),
                Needle("archive backup password", f"{fmt}-3", "tool_output", "keyword"),
                Needle("compiler cache directory", f"{fmt}-4", "tool_output", "keyword"),
                Needle(
                    "archive snapshot passphrase location", f"{fmt}-3", "tool_output", "paraphrase"
                ),
            ]
        )
    return Corpus(transcripts, tuple(messages), tuple(needles))


_SYNONYMS = {
    "irrigation": "watering",
    "timetable": "schedule",
    "dawn": "sunrise",
    "snapshot": "backup",
    "passphrase": "password",
    "location": "directory",
}


def synthetic_embedding(text: str) -> list[float]:
    """Stable, generic word hashing, shared by corpus and queries (no judgments)."""
    vector = [0.0] * 256
    for word in tokenize(text):
        word = _SYNONYMS.get(word, word)
        digest = hashlib.blake2b(word.encode(), digest_size=4).digest()
        vector[int.from_bytes(digest, "little") % len(vector)] += 1
    norm = math.sqrt(sum(value * value for value in vector)) or 1
    return [value / norm for value in vector]


class GateEmbedder:
    def __init__(self, mode="synthetic"):
        if mode not in {"synthetic", "minilm"}:
            raise ValueError("embedding must be synthetic or minilm")
        self.mode = mode
        self.function = None
        if mode == "minilm":
            from mempalace.embedding import get_embedding_function

            self.function = get_embedding_function(model="minilm", device="cpu")

    @property
    def label(self):
        return (
            "synthetic word hashing with fixed synonyms; not model-quality evidence"
            if self.mode == "synthetic"
            else "local minilm CPU"
        )

    def documents(self, texts):
        if self.function is None:
            return [synthetic_embedding(text) for text in texts]
        return self.function(input=texts)

    def query(self, texts):
        if self.function is not None and hasattr(self.function, "embed_query"):
            return self.function.embed_query(input=texts)
        return self.documents(texts)


class SyntheticCollection:
    """Only replace query embedding; SQLite and product ranking stay real."""

    def __init__(self, inner, embedder):
        self.inner = inner
        self.embedder = embedder

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def query(self, *, query_texts=None, **kwargs):
        if query_texts is not None:
            kwargs["query_embeddings"] = self.embedder.query(query_texts)
        return self.inner.query(**kwargs)


@contextmanager
def index_chat(corpus: Corpus, *, policy: bool, embedder=None):
    """Benchmark-only seam for today's indexing versus the proposed policy.

    Stored drawers stay verbatim. Only FTS text and embedding inputs change.
    SQLite's public writer requires vectors for all rows, so an empty vector
    blob simulates keyword-only rows after writing, before any query/cache.
    This deliberate fixture-only SQL is not a production ingestion feature.
    """
    from mempalace import paths
    from mempalace.backends.sqlite_exact import SQLiteExactBackend

    embedder = embedder or GateEmbedder()
    with (
        tempfile.TemporaryDirectory(prefix="index-hygiene-") as directory,
        patch.object(paths, "lock_dir", return_value=Path(directory) / "locks"),
    ):
        backend = SQLiteExactBackend()
        try:
            inner = backend.create_collection(directory, "gate_drawers")
            indexed = [
                index_text(message.text) if policy else message.text for message in corpus.messages
            ]
            included = [
                not policy or message.kind in {"message", "tool_call"}
                for message in corpus.messages
            ]
            vectors = embedder.documents([text for text, keep in zip(indexed, included) if keep])
            pending = iter(vectors)
            # Do not call the embedder on tool output in policy mode. The
            # temporary zero vectors satisfy the writer, then become empty
            # blobs below so they never enter vector retrieval.
            embeddings = [next(pending) if keep else [0.0] * len(vectors[0]) for keep in included]
            inner.add(
                ids=[message.id for message in corpus.messages],
                documents=[message.text for message in corpus.messages],
                metadatas=[
                    {
                        "wing": "gate",
                        "room": "chat",
                        "source_file": message.id + ".jsonl",
                        "drawer_id": message.id,
                        "kind": message.kind,
                    }
                    for message in corpus.messages
                ],
                embeddings=embeddings,
            )
            # No query has loaded the backend's vector cache yet.
            (database,) = Path(directory).glob("*.sqlite3")
            with closing(sqlite3.connect(database)) as connection, connection:
                for message, text in zip(corpus.messages, indexed):
                    connection.execute(
                        "UPDATE docs_fts SET document = ? WHERE doc_id = ?", (text, message.id)
                    )
                    if policy and message.kind not in {"message", "tool_call"}:
                        connection.execute(
                            "UPDATE documents SET embedding = ?, dim = 0 WHERE id = ?",
                            (b"", message.id),
                        )
            yield directory, SyntheticCollection(inner, embedder)
        finally:
            backend.close()


@contextmanager
def product_search(directory, collection):
    """Call the real default MCP handler with isolated storage and configuration."""
    from mempalace.config import MempalaceConfig

    config = SimpleNamespace(palace_path=directory, collection_name="gate_drawers")
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("MEMPALACE_", "MEMPAL_"))
    }
    with patch.dict(os.environ, environment, clear=True):
        # Also isolate the server's initial default config construction if it
        # has not been imported yet. No operator config is needed by this gate.
        # The server protects its protocol by redirecting Python stdout and
        # fd 1 during import. Restore both for this benchmark's CLI/caller.
        stdout_fd = os.dup(1)
        try:
            with (
                patch.dict(os.environ, {"MEMPALACE_CONFIG_DIR": directory}),
                patch.object(sys, "stdout", sys.stdout),
            ):
                from mempalace import mcp_server, searcher
        finally:
            os.dup2(stdout_fd, 1)
            os.close(stdout_fd)

        with (
            patch.object(
                searcher, "MempalaceConfig", partial(MempalaceConfig, config_dir=directory)
            ),
            patch.object(mcp_server, "_config", config),
            patch.object(mcp_server, "_refresh_vector_disabled_flag", lambda: None),
            patch.object(mcp_server, "_vector_disabled", False),
            patch.object(searcher, "get_collection", lambda *args, **kwargs: collection),
            patch.object(
                searcher,
                "get_closets_collection",
                side_effect=LookupError("synthetic corpus has no closets"),
            ),
        ):
            yield mcp_server.tool_search


@contextmanager
def index_compact_chat(corpus: Corpus, *, policy: bool, embedder):
    """Exercise the real drawer writer, split handling and nullable vectors."""
    from mempalace.backends.compact import CompactBackend
    from mempalace.backends.embedding_wrapper import EmbeddingCollection

    with tempfile.TemporaryDirectory(prefix="compact-hygiene-") as directory:
        backend = CompactBackend()
        try:
            inner = backend.create_collection(directory, "gate_drawers")
            collection = SyntheticCollection(EmbeddingCollection(inner), embedder)
            collection.message_ids = {}
            with product_search(directory, collection):
                from mempalace import mcp_server

                config = SimpleNamespace(
                    palace_path=directory, collection_name="gate_drawers", chunk_size=800
                )
                with (
                    patch.object(mcp_server, "_config", config),
                    patch.object(mcp_server, "_get_collection", lambda **kwargs: collection),
                    patch.object(mcp_server, "_wal_log", lambda *args, **kwargs: None),
                    patch.object(mcp_server, "_wal_result", lambda *args, **kwargs: None),
                    patch.object(mcp_server, "_invalidate_overview_caches", lambda: None),
                    patch(
                        "mempalace.backends.embedding_wrapper._embed_texts",
                        lambda texts, **kwargs: _product_embed_texts(
                            texts, **kwargs, embedder=lambda input: embedder.documents(input)
                        ),
                    ),
                ):
                    for seq, message in enumerate(corpus.messages):
                        if policy:
                            kind = {
                                "tool_result": "tool_output",
                                "tool_call": "tool_call",
                                "system": "system",
                            }.get(message.kind, "conversation")
                            result = mcp_server.write_mirror_units(
                                "gate_chat",
                                [
                                    {
                                        "seq_start": seq,
                                        "seq_end": seq,
                                        "message_class": kind,
                                        "part": 0,
                                        "text": message.text,
                                    }
                                ],
                            )
                            drawer_id = (result.get("drawer_ids") or [None])[0]
                        else:
                            result = mcp_server.tool_add_drawer(
                                "private_threads",
                                "gate_chat",
                                message.text,
                                source_file=message.id,
                            )
                            drawer_id = result.get("drawer_id")
                        if not result.get("success"):
                            raise RuntimeError("real compact drawer write failed")
                        collection.message_ids[message.id] = drawer_id
                        assert mcp_server.tool_get_drawer(drawer_id)["content"] == message.text
            records = collection.get(include=["metadatas"])
            kinds = {
                collection.message_ids[message.id]: message.kind for message in corpus.messages
            }
            collection.record_kinds = {
                record_id: kinds[meta.get("parent_drawer_id", record_id)]
                for record_id, meta in zip(records.ids, records.metadatas)
            }
            yield directory, collection
        finally:
            backend.close()


def token_diff(corpus: Corpus) -> dict:
    """Compare outside-run tokens with independent planted payload annotations.

    Exact span equality and exact index-view equality additionally detect
    accidental deletion/reordering that a bag-of-tokens check would miss.
    """
    lost = Counter()
    preserved = 0
    removed = 0
    matched = True
    for message in corpus.messages:
        expected = message.text
        outside = message.text
        for payload in message.payloads:
            outside = outside.replace(payload, " ")
            # Use fixture bytes for the expected placeholder, not detector spans.
            decoded = base64.b64decode(payload, altchars=b"-_", validate=True)
            mime = "image/png" if decoded.startswith(b"\x89PNG\r\n\x1a\n") else "binary"
            expected = expected.replace(payload, f"[base64 {mime} {len(decoded)} B]")
        actual = index_text(message.text)
        expected_spans = []
        for payload in message.payloads:
            for match in re.finditer(re.escape(payload), message.text):
                expected_spans.append((match.start(), match.end()))
        spans = find_base64_spans(message.text)
        matched &= sorted(expected_spans) == [(span.start, span.end) for span in spans]
        matched &= actual == expected
        tokens = Counter(tokenize(outside))
        lost.update(tokens - Counter(tokenize(actual)))
        preserved += sum(tokens.values())
        removed += sum(span.end - span.start for span in spans)
    return {
        "pass": not lost and matched,
        "outside_tokens": preserved,
        "lost_tokens": sum(lost.values()),
        "base64_chars": removed,
        "exact_spans_and_text": bool(matched),
    }


def run_gate(
    *,
    strategy: str = "default",
    seed: int = 38,
    noise: int = 24,
    embedding="synthetic",
    backend: str = "sqlite_exact",
) -> dict:
    if strategy not in {"default", "vector", "union"}:
        raise ValueError("strategy must be default, vector or union")
    if backend not in {"sqlite_exact", "compact"}:
        raise ValueError("backend must be sqlite_exact or compact")
    corpus = synthetic_corpus(seed=seed, noise=noise)
    embedder = GateEmbedder(embedding)
    # Each transcript format represents the same facts. Index them separately
    # so duplicate copies cannot crowd distinct facts out of the top five.
    formats = {
        fmt: Corpus(
            {fmt: source},
            tuple(message for message in corpus.messages if message.id.startswith(fmt + "-")),
            tuple(needle for needle in corpus.needles if needle.target.startswith(fmt + "-")),
        )
        for fmt, source in corpus.transcripts.items()
    }
    outcomes = {}
    for policy in (False, True):
        rows = []
        vector_tools = vector_total = 0
        for fmt, chat in formats.items():
            kinds = {message.id: message.kind for message in chat.messages}
            writer = index_compact_chat if backend == "compact" else index_chat
            with writer(chat, policy=policy, embedder=embedder) as (directory, collection):
                kinds = getattr(collection, "record_kinds", kinds)
                with product_search(directory, collection) as search:
                    from mempalace import searcher

                    vector_weight, bm25_weight = searcher._resolve_hybrid_rank_weights()
                    effective = (
                        searcher._resolve_candidate_strategy(
                            inspect.signature(search).parameters["candidate_strategy"].default,
                            collection,
                        )
                        if strategy == "default"
                        else strategy
                    )
                    for needle in chat.needles:
                        controls = {} if strategy == "default" else {"candidate_strategy": strategy}
                        # Measure each cutoff separately: product candidate pools
                        # depend on limit; slicing top-10 would not test top-5.
                        ranks = {}
                        for limit in (5, 10):
                            result = search(query=needle.query, limit=limit, **controls)
                            if result.get("error") or result.get("candidate_strategy_fallback"):
                                raise RuntimeError(f"product search failed: {result}")
                            ids = [hit["drawer_id"] for hit in result["results"]]
                            target = getattr(collection, "message_ids", {}).get(
                                needle.target, needle.target
                            )
                            ranks[limit] = ids.index(target) + 1 if target in ids else None
                        rows.append(
                            {
                                "format": fmt,
                                "query": needle.query,
                                "target": needle.target,
                                "kind": needle.kind,
                                "match": needle.match,
                                "top5": ranks[5] is not None,
                                "top10": ranks[10] is not None,
                                "rank_at_5": ranks[5],
                                "rank_at_10": ranks[10],
                            }
                        )
                        vectors = collection.query(
                            query_texts=[needle.query], n_results=10, include=["metadatas"]
                        )
                        ids = vectors.ids[0]
                        vector_total += len(ids)
                        vector_tools += sum(kinds[drawer_id] == "tool_result" for drawer_id in ids)
                    if backend != "compact":
                        stored = collection.get(include=["documents"])
                        assert dict(zip(stored.ids, stored.documents)) == {
                            message.id: message.text for message in chat.messages
                        }
        outcomes["policy" if policy else "today"] = {
            "needles": rows,
            "recall_at_10": sum(row["top10"] for row in rows) / len(rows),
            "vector_tool_hits": vector_tools,
            "vector_hits": vector_total,
            "vector_tool_share": vector_tools / vector_total if vector_total else 0.0,
        }
    today, policy = outcomes["today"], outcomes["policy"]
    checks = {
        "token_diff": token_diff(corpus),
        "needles_top5": {
            "pass": all(row["top5"] for row in policy["needles"]),
            "found": sum(row["top5"] for row in policy["needles"]),
            "total": len(corpus.needles),
            "cases": policy["needles"],
        },
        "vector_tool_share": {
            "pass": today["vector_tool_hits"] > 0 and policy["vector_tool_hits"] == 0,
            "today": today["vector_tool_share"],
            "policy": policy["vector_tool_share"],
            "today_hits": [today["vector_tool_hits"], today["vector_hits"]],
            "policy_hits": [policy["vector_tool_hits"], policy["vector_hits"]],
        },
        "recall_at_10": {
            "pass": policy["recall_at_10"] >= today["recall_at_10"],
            "today": today["recall_at_10"],
            "policy": policy["recall_at_10"],
        },
    }
    return {
        "pass": all(check["pass"] for check in checks.values()),
        "requested_strategy": strategy,
        "effective_strategy": effective,
        "embedding": embedder.label,
        "hybrid_weights": {"vector": vector_weight, "bm25": bm25_weight},
        "backend": backend,
        "seed": seed,
        "messages": len(corpus.messages),
        "checks": checks,
        "today": today,
        "policy": policy,
    }


def format_report(report: dict) -> str:
    checks = report["checks"]
    lines = [
        f"Index hygiene gate: {'PASS' if report['pass'] else 'FAIL'} (strategy={report['requested_strategy']}, effective={report['effective_strategy']}, messages={report['messages']}, seed={report['seed']})",
        f"Embedding: {report['embedding']}",
        f"Backend: {report['backend']}; hybrid weights: {json.dumps(report['hybrid_weights'], sort_keys=True)}",
    ]
    for name, check in checks.items():
        numbers = {key: value for key, value in check.items() if key not in {"pass", "cases"}}
        lines.append(
            f"{'PASS' if check['pass'] else 'FAIL'} {name}: {json.dumps(numbers, sort_keys=True)}"
        )
    for fmt in dict.fromkeys(case["format"] for case in report["today"]["needles"]):
        counts = []
        for mode in ("today", "policy"):
            cases = [case for case in report[mode]["needles"] if case["format"] == fmt]
            counts.append(f"{mode}={sum(case['top5'] for case in cases)}/{len(cases)}")
        lines.append(f"Separate collection {fmt}: top5 " + ", ".join(counts))
    for today, case in zip(report["today"]["needles"], checks["needles_top5"]["cases"]):
        ranks = " ".join(
            f"rank@{cutoff}={today[f'rank_at_{cutoff}'] or 'miss'}->{case[f'rank_at_{cutoff}'] or 'miss'}"
            for cutoff in (5, 10)
        )
        lines.append(
            f"{'PASS' if case['top5'] else 'FAIL'} top5 {case['kind']}/{case['match']}: {case['target']} {ranks} query={case['query']!r}"
        )
    if not checks["needles_top5"]["pass"] and report["effective_strategy"] == "vector":
        lines.append(
            "BLOCKED: default vector-only candidates cannot retrieve keyword-only tool output. Requires feat/search-union-default; rerun with --strategy union to measure the proposed candidate path."
        )
    elif not checks["needles_top5"]["pass"]:
        lines.append(
            "BLOCKED: union candidates still miss planted top-5 needles. Keep the policy disabled until ranking and recall pass the gate."
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=("default", "vector", "union"), default="default")
    parser.add_argument("--seed", type=int, default=38)
    parser.add_argument("--noise", type=int, default=24)
    parser.add_argument("--embedding", choices=("synthetic", "minilm"), default="synthetic")
    parser.add_argument("--backend", choices=("sqlite_exact", "compact"), default="sqlite_exact")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = run_gate(
        strategy=args.strategy,
        seed=args.seed,
        noise=args.noise,
        embedding=args.embedding,
        backend=args.backend,
    )
    # The MCP server redirects sys.stdout on import; CLI reports still belong
    # on the original stdout stream, including machine-readable JSON.
    print(
        json.dumps(report, indent=2) if args.json else format_report(report),
        file=sys.__stdout__,
        flush=True,
    )
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
