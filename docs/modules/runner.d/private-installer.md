- **Owns:**

  - `installer.py`, `install_files.py`, `install_host.py`, `install_cli.py` and the standalone `bootstrap.py` implement slice 2: protected-stdin enrollment, private durable state, key-bound recovery, a setup-only user service and reversible linger management. `docs/runner-installer.md` is the operating guide.

- **Promises:**

  - Plan changes nothing. Explicit acceptance runs preflight before code redemption, persists the private key before exchange, writes private artifacts exclusively and atomically, and records owned actions for restart and uninstall. Recovery returns the original delivery; no code or token enters output, service arguments or the phase manifest. The service performs no network operation or work execution.
  - Reinstall with the same intent converges without mutation. Failed activation rolls back service and linger changes while retaining recoverable enrollment state. Uninstall confirms service stop before discarding unchanged owned files, preserves unrelated artifacts and retains linger needed by another service. Existing command defaults and workspace opt-in remain unchanged.

- **Tests:**

  - `tests/test_runner_install.py` exercises the isolated filesystem and recording user manager against the real enrollment handlers without sockets. Existing runner configuration, check and identity tests retain their pinned contract.
