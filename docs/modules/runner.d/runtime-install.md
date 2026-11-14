- **Owns:**

  - `runtime_manifest.py`, `runtime_host.py`, `runtime_install.py` and `runtime_status.py` implement selected signed release discovery/installation, capability probes, deterministic approved mapping caches and bounded setup facts.

- **Promises:**

  - Version pins come only from current machine-bound signed receipts. All selected digests verify before execution/publication; existing and unselected components stay untouched. Runtime files are private, content-addressed and journaled for retry and stop-before-discard removal.
  - Mapping and presence reports grant no authority. Missing headless/auth interfaces remain incomplete; login and host grants are deferred. Setup-off responses and manual runner behavior retain their existing contracts.

- **Tests:**

  - `tests/test_runner_runtime_install.py` covers clean/partial/occupied hosts, synthetic signed artifacts, tampering, cache edits, private creation, convergence, isolation, status fencing and isolated Linux probes.
