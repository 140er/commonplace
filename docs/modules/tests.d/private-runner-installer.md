- **Owns:**

  - `tests/test_runner_install.py` covers the private installation lifecycle with a recording host and in-memory enrollment requests.

- **Promises:**

  - Tests use synthetic credentials and an empty home, prohibit live connection/service operations, inspect creation flags and modes, scan output and manifests for sentinel secrets, inject interrupted delivery and file/service failures, and preserve unrelated files and both original linger states.

- **Tests:**

  - The new installer assertions run alongside existing runner configuration/check/unit, target installer, public-boundary, disabled handoff and documentation fragment checks. No browser files change.
