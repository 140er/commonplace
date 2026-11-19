- **Owns:**

  - `machine_setup.py`: the one-command machine setup program (design in `docs/system/compute-enroll.md`). Standard library only at import, so the hub can embed its source in the shell launcher it serves and run it on a machine with nothing of Commonplace installed; it imports the runner installer only after every check passed.

- **Promises:**

  - Checks run first and read-only (not root, supported system, Python, `cryptography`, the runner program, user service manager, free disk); each failure is reported to the app by cause through the code-authenticated check route and does not spend the code. The code is read from standard input, never printed, logged or stored outside the installer's own exchange; plain HTTP is accepted only to this machine.
  - After a successful install it reports `files`, `service` and `check` with the machine's own credential and prints the same fingerprint groups the app shows. It never touches SSH.

- **How it fails:**

  - A broken command exits 2 without contacting anyone; an unreachable hub is said in plain words; an interrupted install is reported as the step it reached and the same command resumes it.

- **Tests:** `tests/test_machine_setup.py`.
