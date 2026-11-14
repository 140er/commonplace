- **Owns:**

  - Route tests for a person's moves and the node menu cases share one options script so the menu and the server cannot drift.

- **Tests:**

  - `tests/test_commonplace_card_move.py` runs `tests/card_move_options.mjs` and sends every offered and every disabled move to the server, and checks a card a person moved to review stays out of the owner queue. `tests/workbench_cardmenu.mjs` (cases notices, closesAtOnce) pins the move wording per destination against a stale queue and that the menu closes before the refresh ends.
