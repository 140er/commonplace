- **Tests:** `tests/test_board_add_who.py` and its node helper
  `tests/board_add_who_agents.mjs` prove that Who lists the board's agents next to Me
  and Any agent, that a member sees only their own, that a board without agents keeps the
  old field, and that the text shown for each choice matches what the server does (one
  queued run for the Coworker's worker, none for any other agent or for Me).
