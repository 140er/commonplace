- **Owns:**

  - A visible Move to… control on every Deck card (board, drawer, desktop and phone) opens the existing card menu with all five statuses in the words of the status select: Backlog, Todo, Doing, Review, Done (card #667). Each shows the lane it lands in. Waiting on you is not offered because it shows what waits for a person, not a status. `cardmove.js` holds the statuses, which ones a person may move to from each status, and how a refused move reads; the shell still owns the write.
  - Choosing In review sends one status change. The question form for a person's move is gone; agents still ask people through the server's question flow.
  - A second tap or a version bump from elsewhere does not report a change nobody made: a card already in the chosen status counts as moved, a card that only changed elsewhere is sent once more with its current version, and a card now somewhere else says so in words. Refusals show the server's own reason and a busy workspace says so.
- **Promises:**

  - Each move shows "Moved to <status>. It shows in <lane>." with the lane read from the card the server answered with (never from an owner queue read before the move); a card that landed somewhere other than asked says so. The menu closes the moment the server answers and does not wait for the board refresh.
  - The Move entry from Todo to Doing reads "Marks it as started" ("Starts its assigned work" for typed work with a holder), because a person's move starts no run by itself. After Add card the notice names the lane and the state the server gave the card, for example "Added “X” to Active (Ready)."
  - The menu lists every status. The moves the server accepts for a person from the card's status are enabled and the others are disabled with what to do first (Backlog to Doing: "Move it to Todo first."); `tests/test_commonplace_card_move.py` sends every entry to the server and checks that it agrees. A card face and the drawer header show the real status next to the summary word ("Ready · Todo"). The words are written once in the "Words used in Commonplace" section of `docs/projects.md`.
- **How it fails:**

  - A card waiting on an agent's question or an unapproved proposal has every move disabled with the reason.
- **Tests:**

  - `tests/workbench_cardmenu.mjs` (cases deck, review, stale, notices, closesAtOnce) and `tests/deck_board.mjs` cover the options, the single PATCH and the refusal wording.
