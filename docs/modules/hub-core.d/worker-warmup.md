- **Owns:**

  - The launcher's background warm-up switch for gateway workers: it sets `MEMPALACE_WORKER_WARMUP=1` for each worker it starts, and `COMMONPLACE_WORKER_WARMUP=0` on the gateway turns that off. `Gateway.warm_memory` starts a workspace's worker in the background for a page about to search, at most once per workspace every 30 seconds.

- **Promises:**

  - The operator's own `MEMPALACE_WORKER_WARMUP` and `MEMPALACE_EAGER_WARMUP` still never reach a worker; only the launcher sets the warm-up variable. A worker started on the first memory call warms itself up in a background thread and the call does not wait for it. `warm_memory` never blocks and never raises: a backup pause, a closing gateway or a failed start is dropped and the search that follows reports it as usual.

- **How it fails:**

  - With the switch off, workers load lazily and the first search pays the cold loads, as before.

- **Tests:**

  - The launcher sets or omits the warm-up variable (`tests/test_commonplace_workers.py`); the warm-on-intent hook's throttle, error handling and refusal (`tests/test_commonplace_memory.py`).
