- **Owns:**

  - The existing runner command group adds install/status/uninstall and the private setup-service entry through lightweight argument definitions and lazy dispatch.

- **Promises:**

  - Existing runner check/run/chat/report argument defaults stay unchanged. Setup commands do not open the control data directory. Credentials are read only from protected stdin during accepted enrollment.

- **Tests:**

  - `tests/test_runner_install.py` pins the parser additions and existing defaults; the existing runner checks cover their established output and behavior.
