- **Owns:** The desktop title-bar seat for a page's main creating action in
  commonplace/web/static/head-actions.js, used by bots.js (New chat, New bot),
  deck-compute.js (Add a machine), deck-board.js (Add card) and memory.js (Add a
  note, Import memory files). app.js keeps a seated element through a title redraw.
- **Promises:** In the Deck look at 700px and wider, those elements sit at the right
  of the shared title bar next to the page title and are no longer drawn in the
  page. They are the same elements, so each keeps its handler and data attributes.
  On a phone, and in the Classic look, they stay in the page. Resizing across 700px
  moves them between the two places. A click is handled once wherever the element
  sits.
- **How it fails:** Without a title bar, a Deck look or a media query (a test
  harness), nothing moves and the page behaves as before. When a page stops or
  redraws, its element goes back to its place in the page or is dropped; a stale
  element never lands in another page's bar.
- **Tests:** tests/test_deck_compute_capy.py, tests/test_deck_memory.py,
  tests/test_deck_board.py and tests/test_commonplace_deck_bots.py cover the
  unchanged markup and handlers; the app.js shell harnesses cover the title redraw.
