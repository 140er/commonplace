- **Owns:**

  - `tests/_compute_filesystem.py` gives credential and transport tests a root-owned view of fixed POSIX ancestors for containers that show unmapped owners, leaving modes, links and ownership inside the test directory real. `tests/test_compute_credentials.py`, `tests/test_compute_transport.py` and `tests/test_compute_script_sandbox.py` use it with real files, crypto and locks, no ambient keys and no live endpoints.
