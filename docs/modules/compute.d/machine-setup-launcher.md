- **Owns:**

  - `GET /machine-setup.sh` (`pairing_routes.py`): the shell launcher with `runner/machine_setup.py` embedded. Public and identical for every caller; it carries no secret and no tenant data. Pairing now stores the machine's heartbeat as its own row, and the owner view prints the key fingerprint in the installer's eight-character groups.

- **Promises:**

  - A machine reads `offline` only after it has sent a heartbeat and then gone quiet for 15 minutes; a machine that never sent one stays at its last state (the setup service sends none yet). The causes `run_as_root` and `missing_runner_program` are part of the closed list the app explains.

- **How it fails:**

  - A launcher that cannot be built (embedded source containing the end marker) answers 503 instead of a broken script.

- **Tests:** `tests/test_machine_setup.py` and `tests/test_compute_pairing.py`.
