- **Tests:** `tests/test_enospc_writes.py` holds the full-disk regressions: ENOSPC injected on
  the journal path and in the store, a real gateway and worker over temporary data (REST 507,
  MCP error, same save lands once), the floor, status, the admin deploy view, the daemon queue
  and the log handler. `tests/test_wal.py` now uses a permission failure for "a journal
  failure never breaks the call" (a full disk is refused), and
  `tests/test_admin_instance_api.py` covers the two new deploy fields.
