- **Owns:**

  - The launcher's background warm-up switch for gateway workers: it sets `MEMPALACE_WORKER_WARMUP=1` for each worker it starts, and `COMMONPLACE_WORKER_WARMUP=0` on the gateway turns that off.

- **Promises:**

  - The operator's own `MEMPALACE_WORKER_WARMUP` and `MEMPALACE_EAGER_WARMUP` still never reach a worker; only the launcher sets the warm-up variable. A worker started on the first memory call warms itself up in a background thread and the call does not wait for it.

- **How it fails:**

  - With the switch off, workers load lazily and the first search pays the cold loads, as before.

- **Tests:**

  - The launcher sets or omits the warm-up variable (`tests/test_commonplace_workers.py`).
