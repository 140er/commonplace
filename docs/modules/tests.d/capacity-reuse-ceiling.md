- **Owns:**

  - The bounded reuse of the capacity verdict on fine-timestamp filesystems (`tests/test_hnsw_capacity.py`, `tests/test_worker_warmup.py`).

- **Promises:**

  - A verdict is re-probed past the ceiling even when every stamp is unchanged, and a same-size sqlite write in the same timestamp tick is noticed through SQLite's change counter.

- **How it fails:**

  - A verdict served with no time bound, or one that misses a same-tick write, fails these tests.

- **Tests:**

  - The two files above.
