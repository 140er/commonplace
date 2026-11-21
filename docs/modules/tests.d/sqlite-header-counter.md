- **Tests:**

  - `tests/test_sqlite_header_counter.py` covers regular and short headers, missing and invalid paths, regular symlinks, real FIFOs with queued bytes, replacement at open, sockets, descriptor cleanup, open refusal and same-stamp SQLite writes. The existing mid-call permission-change FIFO regression and HNSW capacity cache tests remain unchanged.
