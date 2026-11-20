- **Owns:**

  - The resolved connection ID on `projects/model_modes.Decision` and its internal `account_connection_id` record field, used to preserve admission identity in both manual and config-driven starts and board sessions.

- **Promises:**

  - A resolved personal machine carries the actual active connection's stable ID, including a substituted config machine and an existing private machine in legacy mode. Public account views retain their explicit keys and omit connection and person IDs. Shared decisions carry no connection pin.

- **How it fails:**

  - Recreating a connection for the same person, machine and label produces a different identity. Persistent consumers must refuse missing or changed pins rather than infer the old identity from labels; the resolver still refuses unavailable mode state and ineligible accounts.

- **Tests:**

  - `tests/test_model_mode_account_fencing.py` covers the resolved identity through sessions and both card-start paths; the existing model-mode and run-record tests retain resolver and public-view contracts.
