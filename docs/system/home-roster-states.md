### Home tells loading, failure and no bots apart

- A usability re-test saw "No bots yet" on Home for one or two seconds while the bot list was slow, and during a deploy while it answered 500. That told a person with bots that they had none. Home was also reachable only through the logo.
- Decision: Home keeps one status for the roster (loading, error, ready). "No bots yet" is drawn only for a successful empty read. A failed read shows "Could not load bots, retrying", retries on its own with a growing delay capped at 30 seconds, and offers Retry now. Once bots have loaded, a failed refresh keeps them and uses the old message.
- Home entry: the rail gets a Home link above Today. The phone bottom bar already holds four tabs, More and the voice button, so Home is the first entry in the More sheet instead of a sixth tab.
- Not changed: the rail's own bot list note, the Today empty note, the Add card form, the Move menu and the Compute screen.
