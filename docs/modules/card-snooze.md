## card-snooze

- **Paths:** `commonplace/snooze/`, `tests/test_card_snooze.py`
- **Owns:**

  - Snoozing a card until a time (an ISO date and time with a timezone or offset, at most a year ahead) or until another card on the same board is done, in the board database table `card_snoozes` (additive: one row per snoozed card, created with the board schema). Free-text "wake when..." conditions are not part of this module; no model is called.

  - `store.py` holds the rules (validation, set, clear, wake, the `snoozed_sql` condition other modules reuse) and the clock seam `clock()`; `worker.py` is the due-time pass that logs and announces wakes; `routes.py` serves `/api/card-snooze/<n>` (GET, POST, POST `.../cancel`). The card operations are `BoardService.task_snooze` and `task_unsnooze` (MCP tools of the same names), so the app and agents share one set of rules. Time parsing is `schedule_time.at`.

- **Promises:**

  - A snoozed card (Backlog or Todo) is not offered as work: it is left out of `task_list` unless `include_snoozed` or `all_statuses` is set (the board view shows it with a `snoozed` mark), `task_take_next` skips it and refuses an explicit take (409 `snoozed`), moving it to Doing is refused, a card start is refused (`snoozed`), the no-progress nudge (`orch/stall.py`) and the orchestrator's unstarted list ignore it, and the waiting reason `snoozed` ("Snoozed until 2026-11-12 09:00 Europe/Amsterdam" or "Snoozed until #12 is done") wins over every other reason.

  - Waking is correct without the worker: a card whose time has passed (or whose condition card is done) is no longer snoozed anywhere it is read. The wake itself (delete the snooze row, bump the version, log "Snooze ended: ..." on the card, emit `card.woke`) happens once: only the transaction that deletes the row logs it, across the lazy wake on any board read, the worker (every 30 seconds, `COMMONPLACE_SNOOZE_WORKER=off` stops it) and restarts. A crash between the commit and the event leaves the card woken and logged without the event.

  - Owner or assignee only: a workspace owner (person) or the card's assignee snoozes and unsnoozes; others get 403. Snoozing again replaces the earlier snooze.

- **How it fails:**

  - Refusals carry a code: `invalid_snooze` (not exactly one of `until` and `after_card`), `timezone_required`, `invalid_timezone`, `invalid_time` (also a wall time the clocks skip), `in_past`, `too_far`, `invalid_condition`, `self`, `other_board`, `condition_met` (the card is already done), `not_snoozable` (not Backlog or Todo), `not_snoozed` (unsnooze of a card that is not snoozed).

  - Not built yet: snoozing a thread (chat message), free-text conditions, an app control for it, and re-triggering a runner-configured board's todo start when a card wakes (a wake is not a todo event).

- **Tests:**

  - `tests/test_card_snooze.py` covers, with a fake clock: hidden while snoozed and back at the time, the idle nudge, starts, the waiting reason, wake once (worker, restart, racing, lazy), the card condition, unsnooze by owner and assignee, every refusal, the REST routes and `schedule_time.at`.
