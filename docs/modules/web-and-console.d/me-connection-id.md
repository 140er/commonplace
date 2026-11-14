- **Promises:**

  - `GET /api/me` returns `connection_id` for an OAuth bearer (its own connection) and `null` for a person session. A native client needs it to start the account-deletion authorize step; the id alone grants nothing.
