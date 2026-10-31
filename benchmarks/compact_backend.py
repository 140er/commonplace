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

os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")
os.environ.setdefault("OMP_NUM_THREADS", "2")

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
        shutil.rmtree(root / backend.name)
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


def storage_and_latency(root, size, *, chroma=False, progress=None, dominant=False):
    generator = PalaceDataGenerator(seed=42)
    # Reuse the same 10k synthetic templates with a unique drawer prefix.
    templates = [generator._random_text(800, 800)[:780] for _ in range(10000)]
    rng = np.random.default_rng(42)
    factories = [CompactBackend] if dominant else [CompactBackend, SQLiteExactBackend]
    if chroma:
        from mempalace.backends.chroma import ChromaBackend

        factories.append(ChromaBackend)

    def drawer_wing(i):
        if dominant:
            return "w7" if i < int(size * 0.8) else f"w{i % 24 + (i % 24 >= 7)}"
        return f"w{i % 25}"

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
                        "wing": drawer_wing(i),
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
        bulk_state = col.maintenance_state()
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
            "bulk_load_state": bulk_state,
            "settled_state": col.maintenance_state(),
            "background_maintenance": "synchronous"
            if backend.name in {"compact", "sqlite_exact"}
            else "no completion hook advertised",
            "open_ms": round(open_ms, 3),
            "first_query_with_load_ms": round(load_ms, 3),
        }
        if backend.name == "compact":
            from mempalace.mcp_server import _bulk_drawer_records, _collection_logical_drawer_record
            from mempalace.search_util import build_where_filter

            wing = build_where_filter("w7", known=[f"w{i}" for i in range(25)])
            where = build_where_filter("w7", "r9", known=[f"w{i}" for i in range(25)])

            def phase(col=col, q=q, wing=wing, where=where):
                return {
                    "vector": timed(lambda: col.query(query_embeddings=[q], n_results=10)),
                    "vector_wing": timed(
                        lambda: col.query(query_embeddings=[q], where=wing, n_results=10)
                    ),
                    "vector_wing_room": timed(
                        lambda: col.query(query_embeddings=[q], where=where, n_results=10)
                    ),
                    "lexical": timed(
                        lambda: col.lexical_search(query="authentication database", n_results=10)
                    ),
                    "lexical_wing": timed(
                        lambda: col.lexical_search(
                            query="authentication database", where=wing, n_results=10
                        )
                    ),
                    "lexical_wing_room": timed(
                        lambda: col.lexical_search(
                            query="authentication database", where=where, n_results=10
                        )
                    ),
                    "get_drawer_split": timed(
                        lambda: _collection_logical_drawer_record(col, "p1234")
                    ),
                    "get_drawers_bulk": timed(
                        lambda: _bulk_drawer_records(col, [f"p{i}" for i in range(1200, 1232)])
                    ),
                }

            entry.update(phase())
        entry["maintenance_results"] = {}
        for kind in sorted(backend.maintenance_kinds):
            outcome = col.run_maintenance(kind)
            entry["maintenance_results"][kind] = {"status": outcome.status, "stats": outcome.stats}
        entry["after_maintenance_state"] = col.maintenance_state()
        if backend.name == "compact":
            entry["after_maintenance"] = phase()
            start = time.perf_counter()
            col.update(ids=["d0"], embeddings=[q])
            col.query(query_embeddings=[q], n_results=10)
            entry["write_plus_query_ms"] = round(1000 * (time.perf_counter() - start), 3)
        results[backend.name] = entry
        backend.close()
        entry["after_maintenance_bytes_per_drawer"] = round(
            sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / size, 2
        )
        del col
        shutil.rmtree(path)
        if progress is not None:
            progress(results)
        print(json.dumps({"drawers": size, "backend": backend.name, "result": entry}), flush=True)
    return results


def drawer_metadata(i, wing, room):
    """Seventeen synthetic fields, approximately 440 bytes when serialized."""
    return {
        "wing": wing,
        "room": room,
        "parent_drawer_id": f"p{i // 4}",
        "source_file": f"source/file{i // 50}.md",
        "filed_at": "2026-10-01T00:00:00Z",
        "hall": "hall_events",
        "chunk_index": i % 9,
        "added_by": "convo-miner",
        "entities": "topic-a;topic-b;project-c",
        "authored_at": "2026-10-01T00:00:00Z",
        "ingest_mode": "convos",
        "extract_mode": "exchange",
        "normalize_version": 4,
        "id_recipe": 2,
        "convo_chunker_version": 3,
        "source_mtime": 1793390223.5,
        "source_dir_id": f"dir-{i // 5000:04d}-0123456789abcdef",
    }


_LATENCY_WORKER = r"""
import importlib.util, json, sys, time, threading
import numpy as np
import psutil
if sys.argv[3] != '-':
    spec=importlib.util.spec_from_file_location('mempalace.backends._compact_benchmark', sys.argv[3])
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    Backend=module.CompactBackend
else:
    from mempalace.backends.compact import CompactBackend as Backend
b=Backend()
try:
    c=b.get_collection(sys.argv[2], 'bench', options={'read_only':sys.argv[1]=='probe'})
    q=json.loads(sys.argv[4])
    if sys.argv[1]=='write':
        c.upsert(ids=['external'], documents=['synthetic external write'], metadatas=[json.loads(sys.argv[5])], embeddings=[q])
    else:
        process=psutil.Process()
        peak=[process.memory_info().rss]
        stopped=threading.Event()
        def sample():
            while not stopped.wait(.002):
                peak[0]=max(peak[0],process.memory_info().rss)
        sampler=threading.Thread(target=sample)
        sampler.start()
        try:
            begin=time.perf_counter()
            c.query(query_embeddings=[q], n_results=10, include=[])
            elapsed=(time.perf_counter()-begin)*1000
        finally:
            peak[0]=max(peak[0],process.memory_info().rss)
            stopped.set()
            sampler.join()
        cache=c._handle._vector_cache[1]
        arrays=[getattr(cache, name) for name in ('matrix','rowids','norms','wing','room','rowid_index')] if hasattr(cache,'matrix') else list(cache[1:])
        result={'cold_query_ms':elapsed,'cache_mib':sum(a.nbytes for a in arrays if isinstance(a,np.ndarray))/2**20}
        result['peak_rss_mib']=peak[0]/2**20
        print(json.dumps(result))
finally:
    b.close()
"""


def read_only_latency(
    root, size, *, dominant=False, repeats=7, backend_type=CompactBackend, baseline_module=None
):
    """Time the searcher's read-only path; one synthetic disk store per case."""
    import statistics
    import subprocess
    import sys

    root = Path(root).resolve()
    temporary = Path(tempfile.gettempdir()).resolve()
    if root == temporary or temporary in root.parents:
        raise ValueError(
            "choose an explicit on-disk work directory outside the system temp directory"
        )
    if root.exists():
        raise ValueError("benchmark store already exists")
    backend = backend_type()
    try:
        writer = backend.get_collection(str(root), "bench", create=True)
        rng = np.random.default_rng(77)
        for start in range(0, size, 2000):
            count = min(2000, size - start)
            writer.add(
                ids=[str(i) for i in range(start, start + count)],
                documents=["synthetic searchable record"] * count,
                metadatas=[
                    drawer_metadata(
                        i,
                        "w0" if dominant and i < size * 0.8 else f"w{i % 25}",
                        f"r{(i // 25) % 30}",
                    )
                    for i in range(start, start + count)
                ],
                embeddings=rng.normal(size=(count, 384)).tolist(),
            )
        query = rng.normal(size=384).tolist()

        def worker(mode):
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    _LATENCY_WORKER,
                    mode,
                    str(root),
                    str(baseline_module) if baseline_module else "-",
                    json.dumps(query),
                    json.dumps(drawer_metadata(size, "w0", "r0")),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return json.loads(completed.stdout) if mode == "probe" else None

        cold = [worker("probe") for _ in range(3)]
        measurements = {key: statistics.median(sample[key] for sample in cold) for key in cold[0]}
        measurements["metadata_keys"] = len(drawer_metadata(size // 2, "w0", "r0"))
        measurements["metadata_bytes"] = len(
            json.dumps(drawer_metadata(size // 2, "w0", "r0"), separators=(",", ":"))
        )
        reader = backend.get_collection(str(root), "bench", options={"read_only": True})

        def query_ms(where=None):
            start = time.perf_counter()
            reader.query(query_embeddings=[query], n_results=10, where=where, include=[])
            return (time.perf_counter() - start) * 1000

        query_ms()
        measurements["first_wing_filter_ms"] = query_ms({"wing": "w0"})
        for name, where in {
            "unfiltered": None,
            "wing": {"wing": "w0"},
            "room": {"room": "r0"},
            "wing_room": {"wing": "w0", "room": "r0"},
        }.items():
            query_ms(where)
            measurements[name + "_ms"] = statistics.median(query_ms(where) for _ in range(repeats))
        own, writes = [], []
        for _ in range(repeats):
            begin = time.perf_counter()
            writer.upsert(
                ids=["own"],
                documents=["synthetic own write"],
                metadatas=[drawer_metadata(size, "w0", "r0")],
                embeddings=[query],
            )
            writes.append((time.perf_counter() - begin) * 1000)
            own.append(query_ms())
        measurements["own_write_ms"] = statistics.median(writes)
        measurements["query_after_own_write_ms"] = statistics.median(own)
        external = []
        for _ in range(3):
            worker("write")
            external.append(query_ms())
        measurements["query_after_external_write_ms"] = statistics.median(external)
        return {key: round(value, 3) for key, value in measurements.items()}
    finally:
        backend.close()
        if root.exists():
            shutil.rmtree(root)


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
    parser.add_argument(
        "--dominant-wing",
        action="store_true",
        help="80 percent of drawers share one wing; compact only",
    )
    parser.add_argument(
        "--read-only-latency",
        action="store_true",
        help="Measure synthetic balanced and 80-percent-wing read-only search paths",
    )
    args = parser.parse_args()
    if args.read_only_latency:
        result = {}
        for size in args.sizes:
            for dominant in (False, True):
                name = f"{size}-{'dominant' if dominant else 'balanced'}"
                result[name] = read_only_latency(args.work_dir / name, size, dominant=dominant)
                print(json.dumps({name: result[name]}), flush=True)
                if args.output:
                    args.output.write_text(json.dumps(result, indent=2) + "\n")
        return
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
                dominant=args.dominant_wing,
            )
            if args.output:
                args.output.write_text(json.dumps(result, indent=2) + "\n")
        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
