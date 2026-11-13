### Budgets refuse new starts; they never kill running work

Card #361, slice 2. Slice 1 (`docs/system/cost-ledger.md`) added the cost events and read-only sums; this slice adds the owner's limits over them. It settles the open choices of that note as recommended there. **Default off and invisible:** with no budget set nothing changes: no new tool for any token, no new field on Needs me, no new rows, and every start behaves as before. No money moves and nothing is purchased; the only effect is that the hub itself refuses a new start.

**Assumptions (the recommendations of slice 1, taken as decisions):**

- A budget attaches to one scope (the workspace, an agent connection, a project or a card) and to the workspace's cost window, with one limit in one currency. One budget per scope; setting it again replaces it and starts a fresh warning cycle.
- What counts is the latest known cost of the scope's runs that started in the window, in the budget's currency. A run with unknown cost, or in another currency, is never added; it is counted as `unknown_cost_runs` / `other_currency_runs` and shown apart. The hub holds no prices and estimates nothing.
- Soft warning at 80% of the limit: a mark on the budget (`state: warning`) and one Needs me item for the owner (`budget_warnings`), written once per window and scope. A rolling window re-arms the mark once the condition ends.
- Hard stop at 100%: a NEW start whose workspace, agent connection, project or card has a budget at or over its limit is refused with the code `budget_exceeded`, naming the scope, the budget and the window and no amounts. The check runs inside the start's own admission transaction (`jobs.create_in`, so config-driven and trigger starts too), and again when a queued start would be leased; such a queued start is cancelled with `budget_exceeded`.
- Running work is never killed. A run that already started finishes and keeps reporting; the budget shows `running_runs` and the Needs me item says they will finish. Stopping a run stays a separate owner action.
- Unknown-cost runs never block a start. A budget may carry `unknown_runs_warn`: when that many runs of the scope have no known cost, only the warning is raised.
- Per-model sums and a per-machine view are left out: they are plain group-bys over the same events and no budget needs them.

**The race rule.** A start is admitted only while the scope's reported spend in the current window is below its limit, judged inside the one runner write transaction. That store has one writer at a time, so a start sees every report committed before it and none after it. At or over the limit every concurrent start is refused and none is inserted. There is no reservation: a start has no cost until a machine reports one, so several starts may enter while spend is still below the limit and the overshoot is bounded by the runs already in flight; this is why running work is counted and flagged rather than stopped.

**Marks.** `cost_budget_marks` holds one row per budget, level (`warning`, `limit`, `unknown_runs`) and window. Marks are written in the transaction of a cost report, of a run starting, or of a budget change (never in a refused start's rolled-back one). Needs me shows a budget while it has a mark in the current window and its condition still holds. The next window has no marks, so it warns again.

**Who.** `GET|PUT|DELETE /api/settings/budgets` is for a signed-in workspace owner only and every change is audited (`costs.budget`, `costs.budget_clear`). A member, an agent token and a machine credential get 404, so the existence of a budget is not shown to them; there is no tool. The refusal a requester sees names the scope and budget but never an amount. The tables live in the workspace's own runner file, so another workspace never appears.

**UI.** The mark is data on the route and on Needs me; presenting it is left to the UI work.
