- **Tests:**

  - `tests/test_compute_target_installer.py` models socket-activated SSH with missing or existing runtime directories and active or inactive services. It proves validation ordering, a read-only plan, no-op second install, manifest-backed restoration and uninstall of an existing installation, without live SSH or network calls in those cases.
