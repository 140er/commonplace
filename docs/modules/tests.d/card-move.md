- **Owns:**

  - Route tests for a person's moves and the node menu cases share one options script so the menu and the server cannot drift.

- **Tests:**

  - `tests/test_commonplace_card_move.py` runs `tests/card_move_options.mjs` and sends every offered and every disabled move to the server.
