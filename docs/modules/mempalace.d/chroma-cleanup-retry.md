- **Promises:**

  - Deferred Chroma client and guard cleanup keeps its worker slot until the queues drain. A transient cleanup failure is logged and retried automatically with capped exponential backoff (50 ms doubling to 1 s, reset after a success); it no longer waits for another caller to schedule a new worker.

- **Tests:**

  - `tests/test_chroma_client_lifecycle.py`: a worker failure retries without another close, and repeated failures on either queue back off to the cap and then drain.
