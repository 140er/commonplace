- **Owns:**

  - Focused runtime and host producer regressions.

- **Promises:**

  - Behavioral RED witnesses cover discovery, release size, dropped producer status and startup lock contention; negative tests preserve pin, scope, expiry and private transport checks.

- **How it fails:**

  - Blocked I/O is bounded and killed; unsupported producers cannot receive the grant or reuse a former response. Live evidence remains private.

- **Tests:**

  - tests/test_runner_host_interface.py, tests/test_runner_runtime_install.py, tests/test_runner_setup_lifecycle.py.
