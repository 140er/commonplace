- **Owns:**

  - `docs/glossary.md` is the one page that names each concept of the app once (project, board, card, status, lane, Bot, Coworker, agent, Waiting on you, Memory) and maps each to the tool or API name that keeps an older word (`board_add`, `space_add`, `task_update`, `needs_me`, `/api/bots`). It is a visible-label page only: no tool, route, response key or stored value is renamed by it (card #667).
- **Tests:**

  - `tests/test_commonplace_card_move.py` checks that the Move menu's five status names match the server's, and `tests/test_commonplace_public_surface.py` keeps the public surface unchanged.
