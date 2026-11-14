- **Owns:**

  - Deterministic HTTP restart and worker-generation fixtures inject clocks, launchers, marker publication and transport responses; only fixture PIDs/start identities are signalled by process smoke.

- **Promises:**

  - The original replay fake tolerates the pinned base Worker and asserts HTTP send occurred, so setup errors cannot masquerade as no-retry RED evidence.

  - The entrypoint RED asserts a live monitor before any replacement barrier. Timer cooldown and orphan rechecks use injected time; event deadlines only guard broken harnesses and are never expected RED evidence.

- **How it fails:**

  - Process fixture initialization failures close owned processes and pipes. Restart timing and monitor RSS are measurements, with no performance pass threshold.

- **Tests:**

  - test_http_restart_review.py supplies deterministic old-head RED cases; test_http_restart_startup.py verifies real user-entrypoint bind/lease/duplicate-monitor exit codes and logged post-ready recovery.

  - test_http_generation_restart.py, test_worker_generation_contract.py, test_worker_restart_circuit.py, test_worker_lifecycle.py and test_http_monitor_process.py cover HTTP ownership and completion; forwarding/isolation fixtures include the coordinator and private handles.
