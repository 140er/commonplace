- **Owns:** `deck.js` owns the per-browser look switch (storage key `cmmn.ui`, values
  `deck` and `classic`); `deck-prepaint.js` repeats the same rule before first paint.
- **Promises:** Deck is the look for every visit. Only a stored `classic` (set by the
  Settings Look switch or `?ui=classic`) opts out and stays across reloads; `?ui=deck`
  or the switch stores `deck`. No key, an unknown value, or storage that cannot be read
  all mean Deck. The page is marked before the first paint, so Deck never flashes classic.
- **How it fails:** If storage cannot be written the look holds for this visit only;
  if it cannot be read the page draws Deck. The two copies of the rule must agree, and
  the node harness runs both against the same storage states.
- **Tests:** `tests/commonplace_deck.mjs` (run by `tests/test_commonplace_deck.py`)
  covers first visit, stored classic and deck, `?ui=` across a reload, throwing
  storage, the pre-paint script and the Look switch; the wrapper also reverts the old
  classic-by-default rule in a copy and expects the harness to fail.
