- **Owns:**

  - Final round (`overview.py`, `pairing.py`): the Not finished reason reads "No: setup not finished" and says its fact once; the waiting next step names the fingerprint step; a machine that takes no work shows "Not used in this version" instead of run capacity and resource figures; no copy promises a card in Needs me.

- **Promises:**

  - Every place the copy names is a place the app renders (`tests/test_app_places.py`).

- **How it fails:**

  - A rename of a section or a pointer to Runner machines without Admin fails that test.

- **Tests:** `tests/test_compute_overview.py`, `tests/test_app_places.py`, `tests/test_compute_pairing.py`.
