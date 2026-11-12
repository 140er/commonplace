### Costs are events over the run ledger

Card #361 asks for costs and budgets per workspace, agent connection, project and card. This is slice 1 of it: a cost event ledger and read-only sums. Nothing here limits, warns or stops anything; budgets are slice 2 and wait for the owner's reading of this note.

**One store, no parallel one.** Every start already has a `run_ledger` row in the workspace's `runner.sqlite3` (see `docs/runner.md`, Run ledger) carrying the card, board, project, worker connection, model, provider and whatever the machine reported. A cost event is an append-only row in `cost_events` in the same file, keyed by the run (`job_id`), written in the same transaction as the ledger update it follows. A report that touches tokens or cost, a report that ends the run, and the explicit `cost_unknown` flag each append one event holding the run's totals so far. A run's current cost is its latest event; earlier ones stay as history. Runs that were reported before the table existed get one `backfill` event when the store opens.

**Unknown is a value, never zero.** The hub holds no prices and estimates nothing. A machine that cannot tell the cost says `cost_unknown` (`runner report --cost-unknown`); a run that ends with no cost reported gets an unknown event at its end; a started run nobody reported on has no event. All three count as `unknown_cost_runs` in every sum and are listed apart under `unknown_cost`; none adds to a money total. A reported zero is a real zero. An unknown cannot overwrite a cost already reported (409), and a later token report keeps a declared unknown.

**Sums.** `costs_summary` (MCP) and `GET /api/costs` return, for one window, the sum for the workspace and per agent connection, project, board and card: runs, runs with known and unknown cost, tokens (runs that reported none are counted in `token_unreported_runs`) and cost per currency. Currencies are never converted or mixed. A run belongs to the window its start falls in. Only runs that started (or may have: `uncertain`) are counted; queued, failed and cancelled starts have no cost to report.

**Window.** Default is the UTC calendar month. The owner sets the workspace window with `PUT /api/settings/costs` (`calendar_month`, `calendar_week` from Monday, `calendar_day`, or `rolling_days` with `days` 1 to 366); it is audited and only a signed-in owner may change it. One read may override either end with `since` and `until` (a date or a UTC timestamp, the end exclusive).

**Scope.** A signed-in owner reads every run of the workspace. Anyone else reads the runs of boards whose wing their token can read, and the sums cover only those runs (`scope: "readable"`). A machine credential reads nothing. The store is one workspace's file, so another workspace never appears.

**Open choices for slice 2 (budgets), for the owner to decide:**

- Budgets attach to a scope (workspace, agent connection, project, card) and to the window above, in one currency each; a run in another currency or with unknown cost does not count against a limit and is shown as unknown.
- Soft warning at 80% of a budget: a mark in the UI and a Needs me item for the owner, once per window and scope.
- Hard stop at 100%: refuse NEW starts on that scope with a coded reason that names the scope, the budget and the window. The check belongs in the start's own admission transaction so it cannot race.
- Work already running when a budget is reached (recommended): let it finish, flag it in the UI and Needs me, and never kill it silently. Whether a running run may be stopped is a separate owner action.
- Whether unknown-cost runs should ever block starts (recommended: no, but a count of unknown runs above a threshold raises the warning).
- Whether to add per-model sums and a per-machine view (both are plain group-bys over the same events).
