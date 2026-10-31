"""Isolated workspace-configured rollback embedding process (no operator keys)."""

import json
import sys


def main():
    from mempalace.backends.embedding_wrapper import _embed_texts

    batch = json.load(sys.stdin)
    result = _embed_texts(batch["documents"], metadatas=batch["metadatas"])
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
