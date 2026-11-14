- **Owns:**

  - Memory search latency regressions: capacity verdict reuse (`tests/test_hnsw_capacity.py`), worker warm-up (`tests/test_worker_warmup.py`), the launcher switch (`tests/test_commonplace_workers.py`) and the search route's warming field (`tests/test_commonplace_memory.py`).

- **Promises:**

  - An unchanged palace is probed once however long the gaps between searches, so the unchanged-palace and aging-pickle cases fail on the previous behaviour; the real-worker case runs a worker subprocess against a small real palace.

- **How it fails:**

  - A palace whose files change between searches is re-probed, which these tests keep asserting.

- **Tests:**

  - The four files above.
