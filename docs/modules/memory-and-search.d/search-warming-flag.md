- **Owns:**

  - The `warming_up` field on `GET /api/memory/search`, and the head start the Memory page's `GET /api/memory/rooms` request gives the memory worker.

- **Promises:**

  - The field is present, and `true`, only when the palace worker reported it was still warming up for that search; the results are exactly what they would be otherwise. A rooms request from a caller allowed to search memory starts the workspace's worker in the background; a refused caller starts nothing and the rooms response is unchanged.

- **How it fails:**

  - A worker that does not report it leaves the response unchanged.

- **Tests:**

  - The route carries the field only while the worker warms up (`tests/test_commonplace_memory.py`).
