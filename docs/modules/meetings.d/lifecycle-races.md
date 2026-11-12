- **Promises:**

  - A stop or leave that lands while a step is in flight is never overwritten: the store's `advance` refuses to move a meeting that is `removed` (a stop) at all, and refuses to move one that is `ending` or `processing` (a leave) back to joining, announcing or live. A stop or leave during the announcement post therefore ends the meeting with the capture gate never opened (a stop discards and posts its notice because the announcement was already sent; a leave keeps nothing since nothing was captured), and a stop that lands as a meeting ends naturally still discards.

  - The meeting's lease is renewed by a small heartbeat while a provider call blocks, stopping a little after the hard `max_minutes` limit, so a slow join or post is not ended `interrupted` while its worker runs. The worker beat only starts as many meetings as `max_concurrent` allows, and decides to go idle under the lock `kick` takes, so an invite never waits for a restart.
