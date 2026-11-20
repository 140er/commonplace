- **Owns:**

  - The native authority prerequisite documented in docs/runner-native-authority.md.

- **Promises:**

  - Current version 1 receipts retain setup-only authority. A future execution capability must bind current native enforcement, account/session identity, real paths and a short watchdog lease.

- **How it fails:**

  - Absent native issuer support and protected custody remain blockers. No runtime readiness, registry fence or trust root changes are introduced by this design.

- **Tests:**

  - tests/test_runner_issuer_pin.py, tests/test_runner_host_interface.py, tests/test_runner_setup_lifecycle.py.
