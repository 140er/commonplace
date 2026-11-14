- **Owns:**

  - Verified runtime discovery, private producer transport, supervisor startup and owned provider-home cleanup.

- **Promises:**

  - Pinned bytes, exact versions and safe ownership remain required. Discovery is distinct from host authority. A nonce-bound producer uses private stdin; its host signature still requires an independent hub pin. Setup remains execution-disabled.

- **How it fails:**

  - Unknown host interfaces remain unavailable; invalid grants, altered files or blocked producer pipes stop renewal. A missing issuer pin blocks the separate host change.

- **Tests:**

  - tests/test_runner_host_interface.py, tests/test_runner_runtime_install.py, tests/test_runner_setup_lifecycle.py, tests/test_runner_install.py.
