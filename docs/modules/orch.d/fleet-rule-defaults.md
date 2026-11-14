- **Promises:**

  - Journal inserts persist a card destination or an explicit park reason before any result; checkpoints include parked finished results, including next notes. Older steps gain only additive destination metadata.

  - Fleet rules: one repair per failing pull request gate with a bound (`repair_attempts`), findings routed by the hub to the live owning role's checkpoint with acknowledgement (`orch_finding`, `orch_finding_ack`), and a recorded reason for every fresh runner start with `agent_has_capacity` refusing a start while a context-holding agent has capacity. A board overrides them in the accepted config's `[fleet]` table. Additive tables `orch_repairs` and `orch_findings` and the column `orch_steps.fresh_reason`.

- **Tests:**

  - Destination regressions in `tests/test_orch_durable.py` cover journal, gate and job-derived results, owner checkpoint visibility and idempotent migration preserving existing history.

  - Fleet-rule regressions (`test_fleet_*` in `tests/test_orch_durable.py` for repairs and findings, in `tests/test_orch_runner.py` for fresh agents) cover joined failures and the bound, owner routing, parking reasons, acknowledgement, the context-holder capacity refusal, recorded fresh-start reasons and each `[fleet]` override.

