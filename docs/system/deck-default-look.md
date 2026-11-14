### Deck is the default look

Decision: every person gets the Deck look on a first visit; classic stays as an
explicit opt-out. The switch is one browser key, `cmmn.ui`. Deck is on unless the
stored value is exactly `classic`; a missing key, any other value, or storage that
throws all mean Deck. The Settings Look switch writes `deck` or `classic` (turning it
off stores `classic` instead of removing the key, so the choice survives a reload).
`?ui=deck` and `?ui=classic` still switch the look once and are then dropped from the
address.

The page script runs after the module graph, so `deck-prepaint.js` (a plain blocking
script, because the page allows no inline script) marks the document before first
paint with the same rule, including `?ui=`. Deck-only code paths and `deck.css` key off
`data-ui="deck"` on the root; classic leaves the attribute off, as before.

Deck screens today: Home, Today, Compute, Coworkers, plus Bots and Settings in the bar.
The other routes (Needs me, Projects and boards, cards, Memory, Console, Rules, Admin,
Start, Decide) are the existing pages drawn with Deck styling, not separate screens.
