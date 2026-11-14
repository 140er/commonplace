- **Owns:**

  - A visible Move to… control on every Deck card (board, drawer, desktop and phone) opens the existing card menu with the board's column names: Active, In review and Shipped. Needs you is not offered because it shows what waits for a person, not a place a card is moved to. `cardmove.js` holds the destinations, the status each sends, and how a refused move reads; the shell still owns the write.
  - Choosing In review sends one status change. The question form for a person's move is gone; agents still ask people through the server's question flow.
  - A second tap or a version bump from elsewhere does not report a change nobody made: a card already in the chosen status counts as moved, a card that only changed elsewhere is sent once more with its current version, and a card now somewhere else says so in words. Refusals show the server's own reason and a busy workspace says so.
- **Promises:**

  - Each move shows "Moved to <column>." and the board redraws with the card in that column.
  - The menu lists exactly the moves the server accepts for a person from the card's status, and disables the others with a reason.
- **How it fails:**

  - A card waiting on an agent's question or an unapproved proposal has every move disabled with the reason.
- **Tests:**

  - `tests/workbench_cardmenu.mjs` (cases deck, review, stale) and `tests/deck_board.mjs` cover the options, the single PATCH and the refusal wording.
