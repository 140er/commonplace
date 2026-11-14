- **Owns:**

  - Explicit owner-pin and local status-boundary regressions.

- **Promises:**

  - Behavioral RED witnesses cover the missing plan field and unpinned producer acceptance. Negative cases retain separate enrollment, owner pin and hub trust roots without enabling work.

- **How it fails:**

  - Malformed, missing, replaced or unmanaged pins cannot invoke a producer or forward status; independently signed foreign scope and ceiling requests remain refused.

- **Tests:**

  - tests/test_runner_issuer_pin.py, tests/test_runner_host_interface.py, tests/test_runner_install_commands.py.
