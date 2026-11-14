- **Owns:** `board.js` owns the Add card form and its submit; `app.js` hands the signed-in
  person (`me`) to the board binding. The Who select, its hint line and the `any` choice
  value live in `board.js` (`addCardForm`, `submitCard`, `whoHint`).
- **Promises:** Adding a card never starts a run by itself. Who defaults to the person
  adding the card ("Me (no run starts)"), a card assigned to a person is never startable,
  and the hint under Who says "Nothing starts when you add this card." "Any agent (starts
  a run now)" is an explicit, never-default choice; its hint says an agent run starts now if
  a machine is connected. A missing or empty Who is sent as the person adding the card,
  never as an unassigned card; only the explicit `any` value is sent unassigned, which is
  what lets a board's Todo trigger start a run.
- **How it fails:** Without a known person (`me` unset) the select starts on a disabled
  "Choose who" and is required, so the browser asks for a choice rather than defaulting to
  an agent. The server rule is unchanged: a card that arrives unassigned in Todo on a board
  with a Coworker config still fires its trigger, for API and agent callers.
- **Tests:** `tests/test_card_add_default_no_run.py` renders the form and submits it with
  node (`tests/card_add_default.mjs`), replays the payload through the board service and the
  Todo trigger sweep (default: no job; explicit `any`: one job) and reverts the default in a
  copy of `board.js`, expecting the check to fail; the `who-default` case of
  `tests/workbench_board.mjs` (run by `tests/test_workbench_board.py`) covers the markup, the
  hint and the submit fallback.
