- **Owns:** `tests/deck_stage_first_paint.mjs` and `tests/test_deck_stage_first_paint.py`
  prove the Deck stage's first paint on a deep link.
- **Promises:** The harness reads `data-dimmed` straight after the first `navigation()`,
  before any route or read, for Today, Board, Memory, Compute, Bots, Settings, Needs and
  card addresses (dimmed) and for Home (undimmed), and checks the stage follows
  `state.base` once a route has run. It fails on the previous behaviour.
- **How it fails:** A change that makes the first paint wait for the route or for data
  fails by route name.
- **Tests:** Run the two files above.
