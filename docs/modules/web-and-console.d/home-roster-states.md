- **Owns:** `deck-home.js` owns the roster note under the Home composer (`rosterNote` and the
  loading, error and ready status it follows); `deck-shell.js` owns the Home entry in the rail
  and in the phone More sheet.
- **Promises:** Home says "Loading bots…" until its first bot read answers, "Could not load
  bots, retrying" with a Retry now button when that read fails, and "No bots yet. New bot"
  only when a read succeeded with an empty list. A failed read is retried by itself after
  3, 6, 12 and then every 30 seconds until it succeeds; Retry now reads at once. After a
  good read a later failure keeps the bots on screen. The rail lists Home before Today and
  marks it current on `#/`; the phone bottom bar keeps its four tabs and More, with Home
  as the first entry in the More sheet.
- **How it fails:** A refused read (sign-in lost, not found) clears the bots and shows the
  failure note, not the empty one, so nobody is told they have no bots when the list could
  not be read.
