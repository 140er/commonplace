- **Owns:** `navigation()` in `app.js` owns what the Deck stage shows before the
  first route has run; `deck-appearance.js` turns that route into the stage's
  `data-dimmed` value.
- **Promises:** The shell draws the stage once, before the route is settled and before
  any data read. Until that first route, `navigation()` reads the address instead of
  `state.base`, so a deep link to Today, Board, Memory, Compute, Bots, Settings or a
  card opens on a dimmed stage and Home on the undimmed hero. The stage never starts
  undimmed and flips to dimmed a second or two later. After the first route the stage
  follows `state.base` as before.
- **How it fails:** An address that resolves to another screen once the data arrives
  (an unknown hash, which falls back to Home) can still change the stage once; every
  routed screen keeps its first value.
- **Tests:** `tests/deck_stage_first_paint.mjs` (run by
  `tests/test_deck_stage_first_paint.py`) runs the real `navigation()` and Deck stage
  for each deep link before any route runs; it fails on the old behaviour, where the
  first paint read `state.base` and every deep link started undimmed.
