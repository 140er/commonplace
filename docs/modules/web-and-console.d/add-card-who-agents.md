- **Owns:** `board.js` draws the Add card form's Who choices; `app.js` loads the agent list
  for the open board (`loadBoardAgents`) and redraws the form around the typed words.
- **Promises:** Who keeps "Me (no run starts)" as the default and "Any agent (starts a
  run now)", then lists the board's people and, under "Bots and coworkers", the agents the
  server offers. Choosing an agent assigns the card to it. The option and the hint under
  Who say the same thing the server does: "(starts now)" and "Adding this card starts
  <name> now." only for the agent the server marks `starts_run`, otherwise "(ready for it,
  no run starts)" and "Ready for <name>. Nothing starts when you add this card."
- **How it fails:** A board with no agents, or a failed read of the list, leaves the field
  as it was. A refused create keeps the typed words and the chosen agent and says why.
- **Tests:** `tests/board_add_who_agents.mjs` (run by `tests/test_board_add_who.py`) covers
  the options, the wording, escaping, the hint change, the submit body and a refused
  create.
