### Deck shell

The opt-in Deck look uses a 252px desktop rail and the existing four phone tabs:
Today, Bots, Compute and Settings. Board and Memory are workspace links. The
`#/board` alias resolves to the current readable board; existing deep links stay
valid. The brand and New thread links use Home.

`deck-shell.js` owns the rail and phone chrome. The shell passes its existing bot
roster into the controller; navigation refreshes the readable coworker bot and
conversation lists with bounded requests. There is no extra polling timer. Sections,
unread counts, private markers and state words appear only when their fields exist.
Older lists show bots directly under the heading; only named sections become
folders. The explicit team projection may enrich personal bots by ID; names never
join the two bot registries. Coworkers and conversations nest under the bot that
their recorded IDs name.

Today carries the owner queue count in amber. Bots carries the unread total in
violet on desktop. Folders, workspace tools and the account menu have no count
pills. Each bot shows a violet unread count or its recorded last-message time. The
phone header includes the screen title and an account wait dot; the microphone item
appears when no message composer is visible (except Home). A visible composer
carries its own microphone and uses the existing default-off voice controller.
Desktop tree rows are 26px; phone navigation targets remain at least 44px. Classic
markup is unchanged.

Directory refreshes coalesce, preserve folder disclosure state, and discard stale
responses when the account or workspace changes. Failed reads clear the affected
list and offer Retry. Sign-out clears the shell cache.

Browser-free contracts: `tests/test_deck_shell.py`. Optional gateway/browser checks:
`tests/test_deck_shell_browser.py`; `COMMONPLACE_BROWSER_TESTS=off` skips them
cleanly. The browser check covers desktop, tablet and phone geometry, destinations,
composer microphone placement and horizontal overflow.

The desktop content stays inside the stage at an 8px inset, with a 12px radius and
the existing dimmed artwork and veil. Child states are feature-detected; absent
state fields render nothing. The Bots heading links to the existing New bot action.
The account footer shows the authenticated workspace role beside the workspace name.
The bare Board alias resolves only in Deck; classic routing stays unchanged.

Bot avatar marks use one letter by default. Bots sharing a first letter receive
distinct two-character marks, assigned in name/identity order for stable refreshes.
The narrow Today composer uses “Ask anything” and ellipsis truncation for overflow.

Home and New thread use the registered root hero; Today uses its registered screen.
The shell shares the existing roster callback and retains the personal queue ledger,
palette callbacks and settings navigation. Microphone placement follows visible
composers, including the Home toolbar and open Today reply dialogs.
