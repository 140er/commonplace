- **Owns:**

  - Owner-selected installer pin, immutable local trust storage and scoped status preflight.

- **Promises:**

  - One owner acceptance includes the displayed issuer fingerprint. Enrollment never selects issuer trust. Local and hub verification remain independent; project, topic, ceiling, identity, nonce and grant checks retain setup-only authority.

- **How it fails:**

  - An absent pin prevents producer invocation and host-status acceptance. Altered pins, stale grants, wrong signatures and out-of-scope requests refuse. Shutdown does not require issuer trust; native host enforcement remains a separate prerequisite.

- **Tests:**

  - tests/test_runner_issuer_pin.py, tests/test_runner_host_interface.py, tests/test_runner_install.py, tests/test_runner_setup_lifecycle.py.
