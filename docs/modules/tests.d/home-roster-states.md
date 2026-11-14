- **Tests:** `tests/deck_home_roster.mjs` (run by `tests/test_deck_home_today.py`) mounts the
  real Home screen on a small fake DOM and checks the loading note while the first read is
  pending, the failure note with its automatic and manual retry, the empty note only after
  an empty success, bots kept across a later failure, and the Home entry in the rail and
  the phone More sheet (four tabs unchanged).
  `tests/deck_shell.mjs` now expects Home first in the rail and in the phone More sheet.
