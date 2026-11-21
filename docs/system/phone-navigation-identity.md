### Phone navigation continuity

Optional badge reads can refresh navigation after the page becomes interactive. Replacing identical phone tab markup detaches the selected link and loses keyboard focus. The shell records the last generated markup for each phone tab mount and preserves that mount's children when the markup is unchanged. Both navigation and chat dock refreshes use this path.

A different badge or route still renders. A new mount also renders, independently of the previous mount's markup. Browser tests drive a real background response while a tab is focused, a real owner queue update, a synthetic replacement shell mount and a Settings click.

The measured detached-tab failure and its regression are distinct from historical zero-height touch measurements, whose cause has not been established. These controls do not verify Activity consent stopping or full application remount behavior.
