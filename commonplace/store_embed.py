"""Isolated workspace-configured rollback embedding process (no operator keys)."""

import json
import sys


def main():
    from mempalace.backend_copy import RollbackEmbedderMismatch
    from mempalace.backends.embedding_wrapper import _embed_texts
    from mempalace.embedding import get_embedder_identity

    batch = json.load(sys.stdin)
    actual = get_embedder_identity()
    expected = batch.get("identity")
    dimension = batch.get("dimension")
    if expected and (
        expected["model_name"].strip().casefold() != actual.model_name.strip().casefold()
        or expected.get("dimension")
        and expected["dimension"] != actual.dimension
    ):
        raise RollbackEmbedderMismatch("Configured embedder does not match stored identity")
    if dimension and dimension != actual.dimension:
        raise RollbackEmbedderMismatch("Configured embedder does not match stored vector dimension")
    result = _embed_texts(batch["documents"], metadatas=batch["metadatas"])
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    from mempalace.backend_copy import RollbackEmbedderMismatch

    try:
        main()
    except RollbackEmbedderMismatch:
        json.dump({"error": "embedder_identity_mismatch"}, sys.stdout)
        sys.exit(2)
