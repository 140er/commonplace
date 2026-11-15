- **Tests:** `tests/test_enospc_writes.py` holds the full-disk regressions: ENOSPC injected on
  the journal path and in the store, a real gateway and worker over temporary data (REST 507,
  MCP error, same save lands once), the floor, status, the admin deploy view, the worker's floor
  default and override, the gateway's status vocabulary, the daemon queue and the log handler. `tests/test_wal.py` now
  uses a permission failure for "a journal failure never breaks the call" (a full disk is
  refused), and `tests/test_admin_instance_api.py` covers the two new deploy fields.
  `tests/test_tokenless_http_hub_isolation.py` holds the regression for a trusted-identity setting
  left in the test process: the `tokenless_http_hub` fixture now clears it, so a tokenless loopback
  server starts whatever an earlier test left behind.
