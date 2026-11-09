### Design before code

- A planner role must include `design_before_code`.

- On a configured board, approving a `plan` card runs `orch plan-check` on its body; only `task_approve` removes the `plan` label; a planner connection's card must pass plan-check before it reaches `todo` or `done`.

- Plan-check G3 requires each numeric line in (a) to lead with M, T, G, MEASURED, TARGET or GUESS after indentation and an optional Markdown list marker.

- G7 requires each mapped test file to exist within the current repository root at check time; `tests/test_orch_acceptance.py` is allowed.

- The root is the nearest enclosing Git checkout, or the current directory outside a checkout.

- Absolute paths and paths resolving outside the root are refused.

- `orch_card_start(board, role, card, step_key, epoch, token, config_ref?)` uses `role` to identify the lease and step lineage.

- Acceptance cases R1, R1b, R1d, R1e, R8b, R10a and R11e live in `tests/test_orch_acceptance.py`.

- `SYSTEM.md` and `MODULES.md` are kept current by `docs-check`, which runs in the test suite.

