"""Reproducible compact gates on public synthetic benchmark content.

Run with ``python -m benchmarks.compact_backend --sizes 100000 1000000``.
Outputs aggregate JSON only. Temporary palaces are removed after each run.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

os.environ["OPENBLAS_NUM_THREADS"] = "2"
os.environ["OMP_NUM_THREADS"] = "2"

import numpy as np
import zstandard as zstd

from mempalace.backends.compact import CompactBackend
from mempalace.backends.sqlite_exact import SQLiteExactBackend
from tests.benchmarks.data_generator import PalaceDataGenerator


def timed(function, repeats=30):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        function()
        samples.append(1000 * (time.perf_counter() - start))
    return {
        "p50_ms": round(float(np.percentile(samples, 50)), 3),
        "p95_ms": round(float(np.percentile(samples, 95)), 3),
    }


def dictionary_sizes():
    generator = PalaceDataGenerator(seed=42)
    docs = [generator._random_text(800, 800)[:800].encode() for _ in range(10000)]
    samples, total = [], 0
    for doc in docs:
        if total + len(doc) > 1024 * 1024:
            break
        samples.append(doc)
        total += len(doc)
    result = {}
    for size in [8192, 32768, 65536]:
        dictionary = zstd.train_dictionary(size, samples)
        compressor = zstd.ZstdCompressor(level=3, dict_data=dictionary)
        result[str(size)] = {
            "dictionary_bytes": len(dictionary.as_bytes()),
            "bytes_per_document": round(
                (
                    sum(len(compressor.compress(doc)) for doc in docs[len(samples) :])
                    + len(dictionary.as_bytes())
                )
                / (len(docs) - len(samples)),
                2,
            ),
        }
    return result


def recall(root, n=5000):
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

    generator = PalaceDataGenerator(seed=42)
    needles = generator.needles
    docs = [needle["content"] for needle in needles]
    docs += [generator._random_text(800, 800) for _ in range(n - len(docs))]
    queries = [needle["query"] for needle in needles]
    embedder = ONNXMiniLM_L6_V2(preferred_providers=["CPUExecutionProvider"])
    embedder._download_model_if_not_exists()
    options = embedder.ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    embedder.model = embedder.ort.InferenceSession(
        str(Path(embedder.DOWNLOAD_PATH) / embedder.EXTRACTED_FOLDER_NAME / "model.onnx"),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    vectors = []
    for start in range(0, n, 256):
        vectors.extend([v.tolist() for v in embedder(docs[start : start + 256])])
    qvectors = [v.tolist() for v in embedder(queries)]
    backends = [CompactBackend(), SQLiteExactBackend()]
    rankings = []
    for backend in backends:
        col = backend.get_collection(str(root / backend.name), "mempalace_drawers", create=True)
        col.add(ids=[str(i) for i in range(n)], documents=docs, embeddings=vectors)
        rankings.append(col.query(query_embeddings=qvectors, n_results=10, include=[]).ids)
        backend.close()
    overlap = np.mean([len(set(a) & set(b)) / 10 for a, b in zip(*rankings)])
    planted = [np.mean([str(i) in hits for i, hits in enumerate(ranking)]) for ranking in rankings]
    return {
        "drawers": n,
        "queries": len(queries),
        "model": "all-MiniLM-L6-v2",
        "overlap_recall_at_10": round(float(overlap), 4),
        "compact_planted_recall_at_10": float(planted[0]),
        "float32_planted_recall_at_10": float(planted[1]),
    }


def storage_and_latency(root, size, *, chroma=False, progress=None):
    generator = PalaceDataGenerator(seed=42)
    # Reuse the same 10k synthetic templates with a unique drawer prefix.
    templates = [generator._random_text(800, 800)[:780] for _ in range(10000)]
    rng = np.random.default_rng(42)
    factories = [CompactBackend, SQLiteExactBackend]
    if chroma:
        from mempalace.backends.chroma import ChromaBackend

        factories.append(ChromaBackend)
    results = {}
    for factory in factories:
        backend = factory()
        path = root / backend.name
        col = backend.get_collection(str(path), "mempalace_drawers", create=True)
        rng = np.random.default_rng(42)
        start = time.perf_counter()
        for offset in range(0, size, 1000):
            end = min(size, offset + 1000)
            vectors = rng.normal(size=(end - offset, 384)).astype(np.float32)
            vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
            col.add(
                ids=[f"d{i}" for i in range(offset, end)],
                documents=[
                    (f"drawer {i:09d} " + templates[i % len(templates)])[:800]
                    for i in range(offset, end)
                ],
                metadatas=[
                    {
                        "wing": f"w{i % 25}",
                        "room": f"r{(i // 25) % 30}",
                        "parent_drawer_id": f"p{i // 4}",
                        "parent_entry_id": f"p{i // 4}",
                        "chunk_index": i % 4,
                        "chunk_total": 4,
                    }
                    for i in range(offset, end)
                ],
                embeddings=vectors.tolist(),
            )
        write_seconds = time.perf_counter() - start
        backend.close()
        disk_bytes = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
        start = time.perf_counter()
        backend = factory()
        col = backend.get_collection(str(path), "mempalace_drawers", create=False)
        open_ms = 1000 * (time.perf_counter() - start)
        q = rng.normal(size=384).astype(np.float32).tolist()
        start = time.perf_counter()
        col.query(query_embeddings=[q], n_results=10)
        load_ms = 1000 * (time.perf_counter() - start)
        entry = {
            "bytes_per_drawer": round(disk_bytes / size, 2),
            "write_seconds": round(write_seconds, 2),
            "open_ms": round(open_ms, 3),
            "first_query_with_load_ms": round(load_ms, 3),
        }
        if backend.name == "compact":
            entry["vector"] = timed(
                lambda col=col, q=q: col.query(query_embeddings=[q], n_results=10)
            )
            entry["vector_wing"] = timed(
                lambda col=col, q=q: col.query(
                    query_embeddings=[q], where={"wing": "w7"}, n_results=10
                )
            )
            where = {"$and": [{"wing": "w7"}, {"room": "r9"}]}
            entry["vector_wing_room"] = timed(
                lambda col=col, q=q, where=where: col.query(
                    query_embeddings=[q], where=where, n_results=10
                )
            )
            entry["lexical_wing_room"] = timed(
                lambda col=col, where=where: col.lexical_search(
                    query="authentication database", where=where, n_results=10
                )
            )
            # Use the actual MCP hydration function, with the collection path
            # directly, to include sorting and exact chunk reassembly.
            from mempalace.mcp_server import _collection_logical_drawer_record

            entry["get_drawer_split"] = timed(
                lambda col=col: _collection_logical_drawer_record(col, "p1234")
            )
        results[backend.name] = entry
        backend.close()
        del col
        shutil.rmtree(path)
        if progress is not None:
            progress(results)
        print(json.dumps({"drawers": size, "backend": backend.name, "result": entry}), flush=True)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sizes", type=int, nargs="*", default=[100000, 1000000])
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(".analysis/compact-benchmark-work"),
        help="On-disk directory for isolated temporary benchmark stores",
    )
    parser.add_argument(
        "--chroma", action="store_true", help="Measure Chroma at the smallest requested size"
    )
    parser.add_argument("--skip-recall", action="store_true")
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="compact-gates-", dir=args.work_dir) as tmp:
        root = Path(tmp)
        result = {"dictionary": dictionary_sizes()}
        print(json.dumps(result), flush=True)
        if not args.skip_recall:
            result["recall"] = recall(root / "recall")
            print(json.dumps({"recall": result["recall"]}), flush=True)
        for size in args.sizes:

            def save_progress(entries, size=size):
                result[str(size)] = dict(entries)
                if args.output:
                    args.output.write_text(json.dumps(result, indent=2) + "\n")

            result[str(size)] = storage_and_latency(
                root / str(size),
                size,
                chroma=args.chroma and size == min(args.sizes),
                progress=save_progress,
            )
            if args.output:
                args.output.write_text(json.dumps(result, indent=2) + "\n")
        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
