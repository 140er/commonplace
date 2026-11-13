- **Promises:**

  - The board's agent-status header and Today project status render the server's `waiting_summary` and `waiting_task_summary` respectively as escaped text. Both derive from identical reason counts; Today says tasks and free agents. Clients never infer counts from the visible column or a local capacity default.
  - Deck capacity summaries stay neutral. Deck working rows use one clause and omit missing elapsed times; the classic rendering stays unchanged. Phone boards use 16px gutters and keep the project title beside the primary action.
  - Today hides the composer until it has a bot destination. An empty roster shows New bot, which opens the existing creation form once; late creation requests cannot reopen a stopped bot screen.
  - Read-only metadata validates an existing store header before SQLite can consult its WAL. A damaged main file fails closed even when a surviving WAL could return stale rows. Absent stores retain the unconfigured response without initializing directories, files or schema.

- **Tests:**

  - `tests/board_waiting_summary.mjs`, wrapped by `tests/test_board_waiting_contract.py`, checks both status surfaces, escaping and an absent summary. Existing waiting-note rendering remains a fallback for older responses.
  - `tests/commonplace_deck_today.mjs` covers named destinations, roster changes and the New bot route; `tests/commonplace_bots_view.mjs` covers the creation entry and stopped screens. `tests/test_admin_store_integrity.py` pins damaged-main/live-WAL refusal and absent-store reads without side effects.
