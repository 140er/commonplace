- **Owns:** `deck.js` owns the Look switch (Deck | Classic) and its click handler;
  `deck-settings-capy.js` orders it first in the Deck Settings layout; `agents.js`
  groups the Rotate and Revoke buttons of each connection under a collapsed
  "Access tokens" disclosure; `deck-dither.js` bounds its canvas to the window.
- **Promises:** Look is the first section of Settings in both looks, as two plain
  buttons (Deck, Classic) with the one in use pressed, so the way back to Deck is as
  visible as the way to Classic. Choosing a look stores `deck` or `classic`, redraws
  the current route once and leaves the address alone; choosing the look already shown
  does nothing. The stage and preview canvases stop before the page loses its Deck
  mark, and a canvas never renders larger than the window. Rotate and Revoke stay
  separate from the everyday toggles, with the same buttons and handlers.
- **How it fails:** If storage cannot be written the look changes for this visit only.
  Before this change a canvas without its Deck-only CSS size grew with its own bitmap
  on every resize callback, and a phone froze for about a minute after Classic was
  chosen; the pre-switch stop and the window bound each prevent that on their own.
- **Tests:** `tests/deck_look_switch.mjs` (run by `tests/test_deck_look_switch.py`,
  which also breaks each rule in a copy and expects the harness to fail) covers
  placement, both-way persistence, one redraw, the same route, stop-before-flip, the
  canvas bound and the token group; `tests/test_deck_look_switch_browser.py` repeats
  the journeys at phone and desktop widths when a browser exists.
