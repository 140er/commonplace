- **Owns:**

  - A visible Move to… control on every Deck card (board, drawer, desktop and phone) opens the existing card menu with the board's column names: Active, In review and Shipped. Needs you is not offered because it shows what waits for a person, not a place a card is moved to. `cardmove.js` holds the destinations, the status each sends, and how a refused move reads; the shell still owns the write.
  - Choosing In review sends one status change. The question form for a person's move is gone; agents still ask people through the server's question flow.
  - A second tap or a version bump from elsewhere does not report a change nobody made: a card already in the chosen status counts as moved, a card that only changed elsewhere is sent once more with its current version, and a card now somewhere else says so in words. Refusals show the server's own reason and a busy workspace says so.
- **Promises:**

  - Each move shows "Moved to <column>." with the column read from the card the server answered with (never from an owner queue read before the move); a card that landed somewhere other than asked says so. The menu closes the moment the server answers and does not wait for the board refresh.
  - The Move entry from Todo to Active reads "Marks it as started" ("Starts its assigned work" for typed work with a holder), because a person's move starts no run by itself. After Add card the notice names the column and the state the server gave the card, for example "Added “X” to Active (Ready)."
  - The menu lists exactly the moves the server accepts for a person from the card's status, and disables the others with a reason.
- **How it fails:**

  - A card waiting on an agent's question or an unapproved proposal has every move disabled with the reason.
- **Tests:**

  - `tests/workbench_cardmenu.mjs` (cases deck, review, stale, notices, closesAtOnce) and `tests/deck_board.mjs` cover the options, the single PATCH and the refusal wording.
