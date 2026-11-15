- **Owns:** The Memory page's wait line for a warming or busy memory: `retryLine` in
  `deck-memory.js`, drawn by both the Deck and the classic Memory view, and its styles in
  `deck-memory.css` and `map.css`.
- **Promises:** The line is one short paragraph, a status element that wraps instead of
  widening the page, so the page has no horizontal overflow at a 390 px phone width. It
  replaces the "Searching saved memory…" placeholder while it shows and is updated in
  place each second, so typing in the search box is not redrawn.
- **How it fails:** Without a wait hint from the server no line is drawn and the existing
  error message shows.
- **Tests:** `tests/memory_warming.mjs` renders the line in both looks, escaped, without the
  loading placeholder (the behaviour is in the memory-and-search module).
