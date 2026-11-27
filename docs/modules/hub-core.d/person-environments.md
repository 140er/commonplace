- **Owns:**

  - `environments.py` keeps one person's environments: stable id, name, address and optional machine id. `GET /api/environments`, `PUT /api/environments/<id>` and `DELETE /api/environments/<id>` require the separate `environments:person` consent. `memory:read` alone is refused.
- **Promises:**

  - Rows are keyed by the signed-in person. A member with the same consent sees only their own rows. The table has no pairing secret, token or credential column, and a request that names one is refused before a write. An unchanged put returns the same public row and does not move `updated_at`.
- **Tests:**

  - `tests/test_commonplace_environments.py`.
