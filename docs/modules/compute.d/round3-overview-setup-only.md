- **Owns:**

  - `overview.py` shows a runner added through the setup guide that has never sent a heartbeat as "Set up and checked" (`set_up`, health unknown), from `pairing.setup_only_ids`, instead of Online.

- **Promises:**

  - The overview never calls such a machine Online or Offline: its setup-only service sends no heartbeat, so the only signals are its own setup reports. A heartbeat turns it into an ordinary runner row.

- **How it fails:**

  - A hub without the pairing tables has no setup-only machines and every row reads as before.

- **Tests:** `tests/test_compute_pairing.py`.
