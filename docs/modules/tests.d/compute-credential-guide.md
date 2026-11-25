- **Tests:**

  - `tests/test_compute_credential_list.py` runs the credential list against real custody files (reference-only rows, other workspaces and key material absent, unprovisioned and unsafe custody, owner-only, read-only route) and executes the documented operator step. `tests/compute_ui.mjs` has a `credentials` case (select, plain-words checks, copyable step equal to the documented one, unsafe values refused, unreadable list, manual entry payload unchanged), run by `tests/test_compute_ui.py`.
