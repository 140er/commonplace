- **Owns:**

  - Round 7 of the #615 review: node cases (`staleness`, `every-runner-status-ages`, `stale-states-keep-the-server-snapshot`, `skew`, `clock-step`, `pause`, `clock-ticks-in-place`) now assert that nothing flips: every runner status keeps its words, next step and blockers when the read is old, ages read "before HH:MM:SS", the list is rewritten once when the read turns old, and the banner button follows; browser cases for a failed read and a paused tab a quarter of an hour old (Classic and Deck, desktop and phone) with captures.
- **Promises:**

  - No sleeps: the fake clock moves time and each check waits on a locator.
- **How it fails:**

  - A chip, tone, next step or "ago" that the clock made fails these tests.
- **Tests:** The files above.
