- **Tests:** `tests/test_card_create_busy.py` holds the board write lock and checks that
  card create and the board list answer 409 `busy` with the plain sentence and
  `Retry-After`, and that a retry adds one card. `tests/board_add_failure.mjs` (run by
  `tests/test_board_add_failure.py`) drives the real `bindBoard` submit for a 409 reply, a
  silent network failure, the message surviving a redraw, a double submit across a redraw
  and a retry that clears the message.
