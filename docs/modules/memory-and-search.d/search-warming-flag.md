- **Owns:**

  - The `warming_up` field on `GET /api/memory/search`.

- **Promises:**

  - The field is present, and `true`, only when the palace worker reported it was still warming up for that search; the results are exactly what they would be otherwise.

- **How it fails:**

  - A worker that does not report it leaves the response unchanged.

- **Tests:**

  - The route carries the field only while the worker warms up (`tests/test_commonplace_memory.py`).
