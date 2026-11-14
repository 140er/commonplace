# Deck shell

The opt-in Deck look uses a 252px desktop rail and the existing four phone tabs:
Today, Bots, Compute and Settings. Board and Memory are workspace links. The
`#/board` alias resolves to the current readable board; existing deep links stay
valid. The brand and New thread links use Home.

`deck-shell.js` owns the rail and phone chrome. The shell passes its existing bot
roster into the controller; navigation refreshes the readable coworker bot and
conversation lists with bounded requests. There is no extra polling timer.
Sections, unread counts, private markers and state words appear only when their
fields exist. Older lists use one Bots folder. The explicit team projection may
enrich personal bots by ID; names never join the two bot registries. Coworkers
and conversations nest under the bot that their recorded IDs name.

Today carries the owner queue count in amber. Bots carries the unread total in
violet on desktop. Folders, bots, workspace tools and the account menu have no
count pills. The phone header includes the screen title and an account wait dot;
the microphone item appears on every route except Home and uses the existing
default-off voice controller for the current composer. Desktop tree rows are
26px; phone navigation targets remain at least 44px. Classic markup is unchanged.

Directory refreshes coalesce, preserve folder disclosure state, and discard stale
responses when the account or workspace changes. Failed reads clear the affected
list and offer Retry. Sign-out clears the shell cache.

Browser-free contracts: `tests/test_deck_shell.py`. Optional gateway/browser
checks: `tests/test_deck_shell_browser.py`; `COMMONPLACE_BROWSER_TESTS=off` skips
them cleanly. The browser check covers desktop, tablet and phone geometry,
destinations, folder persistence and horizontal overflow.
