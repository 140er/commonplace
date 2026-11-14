- **Owns:**

  - The capacity verdict's reuse rule in `mempalace/backends/chroma.py`: a verdict is served again while the probed files keep the same signature, and a fine-grained timestamp is what lets it outlive the 10 s coarse-filesystem backstop. The worker's background warm-up (`_warm_worker_for_search`, `_start_worker_warmup`), its state in the HTTP status payload and the additive `warming_up` field on a search result.

- **Promises:**

  - A verdict is never re-probed by the clock alone where a stamp proves sub-10 ms timestamps; any change to the database, its journal, the metadata pickle or the header still re-probes. A verdict that turns "diverged" with time alone (a standing gap behind an aging pickle) is served only until then, and a reused verdict reports the current metadata age. Coarse-timestamp filesystems keep the 10 s ceiling. The warm-up runs the ordinary search path once after the files-only probe, never opens a diverged index through Chroma, never blocks startup or a request, and never raises. A search that arrives during it carries `warming_up: true`; no other search result changes.

- **How it fails:**

  - A warm-up that cannot finish leaves the state `failed` and logs a warning; the first search then pays the cold loads as before. A palace without a database file is left untouched and counts as done.

- **Tests:**

  - Capacity verdict reuse, the aging-pickle flip and the reported age (`tests/test_hnsw_capacity.py`); warm-up order, failure, background start, the result flag, the status payload and a real worker warming a real palace (`tests/test_worker_warmup.py`).
