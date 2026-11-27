## connection-requests

- **Paths:** `commonplace/connection_requests.py`
- **Owns:**

  - Member connection requests and their browser-only request, cancel, approve, decline, collect and revoke routes under `/api/connection-requests`.

- **Promises:**

  - A request belongs to its requesting member and workspace. Its name, note and decline reason stay verbatim.

- **How it fails:**

  - Unauthorized decisions are refused; an already closed request cannot receive a different decision. A failed connection creation rolls the whole approval back.

- **Tests:**

  - `tests/test_connection_requests.py` and `tests/test_connection_requests_ui.py`.
