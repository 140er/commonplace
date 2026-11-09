### Starts on a configured board

- Every config-driven start runs one fenced transaction (`BEGIN IMMEDIATE` on `runner.sqlite3`): resolve and hash, refuse under the restore flag or for a `plan`-labelled card (`plan_pending`), look up the existing step (or, for a person, the runner's own request key), check the cap, insert.

- A same-step retry returns the stored job; any mismatch is a 409 with the stored step.

- `card_start` on a configured board is person-owner only; every other caller gets `no: use orch_card_start`.

- Cancelling a job that belongs to a step is person-owner only.

- The cap counts distinct cards: queued, leased, starting or uncertain jobs; jobs started within `STARTED_HOLD_SECONDS`; and `doing` cards with any connection assignee.

- The limit is `min(config.max_agents, project_limits.max_agents)` when both are set.

- A `plan`-labelled card never starts from config until the plan is approved; the plan-approval row fires the trigger once.

- The plan is also re-checked when the runner reports `starting`; a refused job ends `cancelled` with outcome `plan_pending`.

