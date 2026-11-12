- **Promises:**

  - `mempalace/embedding_service.py` is an opt-in shared embedding service (`embedding_service` / `MEMPALACE_EMBEDDING_SERVICE`, off by default): one process holds the `minilm` or `embeddinggemma` model and serves it over an owner-only Unix socket, returning the in-process function's float32 values unchanged. Texts stay on the machine and are neither logged nor kept; memory is bounded by one model and caps on concurrent runs, connections, request bytes and texts. `embeddinggemma2` and `openai-compat` are never routed to it. See `docs/embedding-service.md`.

  - `embedding_service` is a search setting (`hub_client.SEARCH_SETTINGS`, scope: every model but `openai-compat`): setting `MEMPALACE_EMBEDDING_SERVICE` keeps a CLI search off the hub, and the hub's search fingerprint hashes the resolved socket path, because a down or different service ranks lexically or with another model.

- **How it fails:**

  - With the embedding service switched on and down, callers raise `EmbeddingServiceUnavailableError` (a model error). They do not load the model themselves and never use another model or provider. Search ranks lexical candidates and marks the result `degraded: "lexical"` with a warning (the CLI prints the reason on stderr); backends without a bounded lexical index return the error. Writes that embed are refused; reads that do not embed keep working. A service asked for a model it does not hold refuses.

- **Tests:**

  - `tests/test_embedding_service.py`: bit-identical vectors (stub model and real MiniLM), the unchanged default, refusals and limits, concurrent callers within the inference cap, socket ownership, search degrade and write refusal against a palace, and one model load across several processes.
