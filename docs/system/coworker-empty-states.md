### Coworker page: every state says what and why

A usability re-test still found empty or bare lines on the Coworker page: a waiting
bot showed only "Waiting on you", an idle bot "Idle", and every list row read
"No summary recorded". Cause: the Coworker directory sends no state or summary per
conversation, and the page ignored the bot's own roster row (state, waiting count,
last message). Decisions:

- The page reads that roster row and words it. No new endpoint and nothing stored.
- Order: a running job, then a decision waiting on you or a disabled bot, then the
  newest finished run, then the bot's working state or last message, then a plain
  statement that nothing is recorded.
- "Nothing recorded" is said differently for an owner (no run yet) and for a member of
  a shared bot (nothing of theirs yet; the owner's other work is not shared with them).
- Loading and failed reads have their own words so they never look like an idle bot.

Known gaps: cards the owner holds on the bot's project are not read here, so the page
links the board instead of listing them; the roster does not say what a running turn
is about.
