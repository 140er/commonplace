- **Tests:**

  - `tests/test_commonplace_oauth.py` holds `/api/me`'s `connection_id`: an OAuth bearer sees its own `conn_` id (equal to the authenticated principal's), a person session sees null.
