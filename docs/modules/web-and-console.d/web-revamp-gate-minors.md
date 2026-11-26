- **Owns:** `deck-work.js` owns `settledPool` and the `CARD_READS` bound on the Work screen's
  per-project card reads; `deck-system.css` owns the `--primary`, `--primary-ink` and `--ok`
  colour roles (with `--violet`, `--violet-ink` and `--cyan` kept as aliases for one release);
  every screen that adds its own stylesheet reads the asset manifest's `styles` list with a
  guard; `bots.js` waits for the shared bot list before it resolves a deep link to a frozen
  bot; `deck-home.js` is gone, Home is rendered by `deck-today-capy.js`.
- **Promises:** A Work refresh keeps at most four card reads in flight however many
  projects there are, and still reads every project; one failed project shows the partial
  note while the rest load. A page whose asset manifest has no `styles` list still opens.
  A link straight to a frozen bot opens its sharing sheet, never a conversation read for a
  bot the page has not found yet. The runner-machine refusal and the Attest form hint say
  the provider instance name lives in the T3 settings stored on that machine, with no file
  path in the text.
- **Tests:** `tests/deck_work.mjs` (bound, order, partial failure, manifest guard),
  `tests/commonplace_deck_bots.mjs` (frozen deep link waits for the shared list),
  `tests/deck_shell.mjs` (colour roles and aliases), `tests/cardruns.mjs` and
  `tests/admin_runners_ui.mjs` (no path in the copy), `tests/deck_home_roster.mjs`,
  `tests/deck_home_today.mjs` and `tests/needs_me_cards.mjs` (the Home checks that still
  describe live behaviour).
- **How it fails:** A card read that fails leaves that project's earlier cards on screen
  and marks the page partial. If the shared bot list cannot be read, the roster works
  without the frozen bots and a deep link to one shows no such bot.
