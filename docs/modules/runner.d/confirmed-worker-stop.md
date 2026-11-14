- **Promises:**

  - Launch run IDs survive delivery and replay. Versioned external run-stop requests and durable terminal evidence are fenced to the exact machine/job/attempt/run/request; unsupported hosts and missing evidence remain unconfirmed.

- **Tests:**

  - tests/test_runner_stop.py covers retained run IDs, confirmed termination, ignored, disconnected and unsupported hosts, stale attempts, terminal evidence and durable replay; migration tests retain old digests and payloads while adding nullable run-stop evidence columns in place.
