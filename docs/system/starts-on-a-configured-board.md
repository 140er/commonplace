### Starts on a configured board

- Every config-driven start runs one fenced transaction (`BEGIN IMMEDIATE` on `runner.sqlite3`): resolve and hash, refuse under the restore flag or for a `plan`-labelled card (`plan_pending`), look up the existing step (or, for a person, the runner's own request key), check the cap, insert.

- A same-step retry returns the stored job; any mismatch is a 409 with the stored step.

- `card_start` on a configured board is person-owner only; every other caller gets `no: use orch_card_start`.

- Cancelling a job that belongs to a step is person-owner only.

- The person owner may send `card_start` the card alone on a configured board: machine, project and topic come from the config, and values that are sent must still equal it. `GET /api/card-starts/options` (person owner only, read-only) tells the card panel what it may offer and why nothing can be started; it creates no job.

- The cap counts distinct cards: queued, leased, starting or uncertain jobs; jobs started within `STARTED_HOLD_SECONDS`; and `doing` cards with any connection assignee.

- The limit is `min(config.max_agents, project_limits.max_agents)` when both are set. Both accept 1–100. Per machine, `max_in_flight` and `max_starts_per_hour` still apply at lease time, and the runner refuses a leased start with the pre-spawn code `low_memory` when its available memory is below `min_free_memory_mb` (above 20 configured agents, it must also cover the launch reserve) (the job ends `failed`, shown by `card_start_status`; the guard never blocks when the probe is unreadable).

- A `plan`-labelled card never starts from config until the plan is approved; the plan-approval row fires the trigger once.

- The plan is also re-checked when the runner reports `starting`; a refused job ends `cancelled` with outcome `plan_pending`.

Only an explicit accepted config limit above 20 enables the added CPU guard and launch reservations. Known CPU shortages can refuse a start (`low_cpu`); missing or stale measurements are unknown and cannot supply a resource refusal. Short-lived local reservations serialize startup bursts across clients sharing the machine journal. Fresh numeric reports constrain leases and the board summary, while the runner rechecks locally before launching. The board reports both its configured limit and runner capacity; the effective ceiling is their minimum.
