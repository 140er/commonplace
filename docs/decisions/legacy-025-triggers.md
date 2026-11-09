### Triggers

- Only boards with an accepted live `[runner]` config are swept.

- The sweep reads committed `task_events` after a per-board cursor; it fires on a move into `todo` from another status (add, move, decide) and on the plan-approval row.

- The cursor is armed in two steps because board events and the cursor live in different files: a configuring accept commits the cursor as NULL with a new generation, then arming sets it to `COALESCE(MAX(task_events.id), 0)` only if the generation is unchanged.

- A NULL cursor is never swept.

- Events before arming are skipped (never replayed).

- Every candidate is re-checked against the recorded `(cursor, gen)` as the first statement inside its fenced transaction; the job or the coded miss and the cursor advance commit together.

- Only infrastructure errors leave the cursor where it was.

- Before the fenced transaction the sweep's only `runner.sqlite3` write is the arming update of an unset cursor: it opens the orchestrator store without creating tables or converting a restore marker.

- The actor is rebuilt from the event stamps and must pass `requester_ok`; a member's move does not start work.

