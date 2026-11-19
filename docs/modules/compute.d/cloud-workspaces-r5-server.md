- **Owns:**

  - Cloud workspace rules added in review round 5 (`workspaces.py`, `control.py`): name uniqueness compares the folded name of every other record (`_taken`), not only the stored key, and maps a unique-constraint failure on the UPDATE to `name_taken`; `public` projects a record whose stored image reference cannot be parsed as `image_unreadable`; the control journal catches only `ProtocolDenied` and `ScopeDenied`.
- **Promises:**

  - Renaming a record into the name of a legacy duplicate (one the migration kept apart under `key\x00id`) is `409 conflict` / `name_taken`, never a server error; one unreadable stored reference never turns the list or a single read into an error.
- **How it fails:**

  - A `TypeError` or `OverflowError` raised inside a control transaction is not hidden as a store error: it propagates as the bug it is.
- **Tests:** `tests/test_compute_workspaces.py` (rename into a legacy suffixed duplicate, unreadable stored reference in the list, journal exception types).
