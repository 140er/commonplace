- **Owns:**

  - `machines.cancel_start` hands a cancel to the runner and answers its `state` as it is: `cancelled` for a queued or leased start, `started` for a launched agent whose stop is only requested.

- **Promises:**

  - Cancelling a launched agent is not refused: the runner records one stop request, answers `started` and says `cancelled` only after it confirmed the stop. A start that is already cancelled or failed is refused as finished and an unknown one as not found.

- **How it fails:**

  - A launched start without a retained run id keeps the stop unsupported; a stop the runner cannot confirm stays requested and never reads as stopped.

- **Tests:**

  - `tests/test_bots_machines.py` pins the answer for a launched agent without and with a run id, a repeated request and the plain refusal for a finished start.
