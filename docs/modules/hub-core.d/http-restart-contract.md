- **Owns:**

  - WorkerSupervisor retains restart failures per workspace across generations and pins each transport attempt until its response is consumed; launcher-owned fact handles belong to that generation.

- **Promises:**

  - Paused generation pins preserve WorkersPaused retry hints; a worker reinserted after a reap timeout is retiring before it becomes visible again.

  - A matching authenticated no-dispatch recycling refusal permits at most one replacement retry after all old pins finish and the child is reaped. Successful DRAINING responses are final; busy, malformed contracts, body loss and transport errors never replay. Pause and close remain authoritative.

- **How it fails:**

  - The three-failure circuit refuses requests for 60 monotonic seconds; the next request probes through single-flight. A 30-second replacement wait returns unavailable while an old generation remains alive. Unknown child counts stay unknown; 16 observed successful storage completions reset failures.

- **Tests:**

  - Paused-pin and timeout-reinsertion regressions exercise the real WorkerSupervisor and gateway boundary without transport replay.

  - Worker generation-contract and restart-circuit tests cover deterministic base regressions; existing worker isolation, gateway and backup-drain suites preserve the ownership boundary.
