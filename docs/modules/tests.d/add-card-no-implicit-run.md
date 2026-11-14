- **Tests:** `tests/test_card_add_default_no_run.py` and its node helper
  `tests/card_add_default.mjs` prove, without a browser, that the Add card form's default
  Who sends the signed-in person, so a configured board's Todo trigger enqueues no run;
  choosing "Any agent" still enqueues one; and a copy of `board.js` with the old default
  fails the check. The `who-default` case in `tests/workbench_board.mjs` covers the form
  markup, the hint text and the submit fallback.
