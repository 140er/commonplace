### Orchestrator trigger sweep never wedges

- A trigger start that cannot be planned is a coded miss that advances the board's cursor; only infrastructure errors leave the cursor. A split start falls back to the `[runner]` role when the config has no `main` role, and an origin release fires the trigger.

- Repeated sweep failures are visible: the second consecutive failure of a board alerts the owner once (`orch.trigger.failing`, `needs_me.orchestrator_failing`) until a sweep succeeds.

- A todo start refused for a transient reason (`at_cap`, `project_limit`, `busy`, `settings_unavailable`, `config_changed`, `worker_unavailable`, `started_recently`) is retried by the sweep with bounded backoff (1, 5, 15, 30, 60 minutes, then `retry_exhausted`); every refusal stays a miss and the attempts are kept in `orch_start_retries`. A permanent refusal is never retried.
