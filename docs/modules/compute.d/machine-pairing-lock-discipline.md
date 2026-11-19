- **Owns:**

  - The pairing routes' reads of the shared control connection (`pairing_routes.py`): the owner role comes from the principal that `routes.handle` has just re-read under the control lock, and the existence check before an accept or close body is read runs under that lock. The ready-state next step names where a provider and attestation are done (Runner machines in Admin).

- **Promises:**

  - No pairing route reads the shared connection outside the control lock; an unlocked read raced other request threads and showed a signed-in person the sign-in page in a real browser.

- **How it fails:**

  - A statement made without the lock fails the lock test for every pairing route (`tests/test_compute_pairing.py`).

- **Tests:** `tests/test_compute_pairing.py` (control-lock test) and `tests/test_compute_pairing_browser.py`.
