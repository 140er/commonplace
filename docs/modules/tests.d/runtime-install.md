- **Owns:**

  - `tests/test_runner_runtime_install.py` uses in-process enrollment/receipt/status routes and fake runtime adapters; synthetic zipapps exercise isolated Linux capability probes without external requests.

- **Promises:**

  - The base assertion fails on missing runtime approval behavior, not import errors. Tests never download a real package, initiate login or enable work. Existing public pins and account/guard checks remain required.

- **Tests:**

  - Targeted runtime, installer, service, receipt, enrollment and binding checks, pinned off-state baselines, public-surface, account and documentation checks.
