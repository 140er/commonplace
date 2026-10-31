"""A0 embedding CPU/count comparison on synthetic agent chats with screenshots.

Run: uv run python -m benchmarks.placeholder_embeddings --repeats 3
Uses the configured local MiniLM CPU model; no private corpus is read.
"""

import argparse
import json
import statistics
import time

from benchmarks.index_hygiene_gate import synthetic_corpus
from mempalace.backends.embedding_wrapper import _embed_texts
from mempalace.embedding import get_embedding_function
from mempalace.stored_text import IndexSource, index_text


def run(repeats=3, chunk_size=800):
    corpus = synthetic_corpus()
    docs, metadata = [], []
    for message in corpus.messages:
        source = IndexSource(message.text)
        for start in range(0, len(message.text), chunk_size):
            end = min(start + chunk_size, len(message.text))
            docs.append(message.text[start:end])
            metadata.append(source.metadata(start, end))
    embedder = get_embedding_function(model="minilm", device="cpu")
    # Warm model loading and inference before timing either policy.
    embedder(input=["warm local embedding inference"])
    results = {}
    for policy in ("before", "after"):
        timings = []
        for _ in range(repeats):
            started = time.process_time()
            if policy == "before":
                vectors = embedder(input=docs)
            else:
                vectors = _embed_texts(docs, metadatas=metadata, embedder=embedder)
            timings.append(time.process_time() - started)
        results[policy] = {
            "embedding_cpu_seconds": statistics.median(timings),
            "cpu_samples_seconds": timings,
            "vectors": len(vectors),
            "embedding_characters": sum(len(d) for d in docs) if policy == "before" else sum(
                len(index_text(d, m, stored_form=False)) for d, m in zip(docs, metadata)
            ),
        }
    return {
        "model": "MiniLM CPU",
        "messages": len(corpus.messages),
        "chunk_size": chunk_size,
        "repeats": repeats,
        **results,
        "cpu_reduction_percent": 100 * (1 - results["after"]["embedding_cpu_seconds"] / results["before"]["embedding_cpu_seconds"]),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    print(json.dumps(run(args.repeats), indent=2))
