### Test fixtures do not race background clocks

Regression tests compare control-database snapshots and drive the real gateway, whose dispatchers (webhooks, phone notifications) advance their `meta` cursors on a background clock. A before/after "nothing else changed" snapshot therefore ignores those cursors, and the shared hub test client lets a caller allow a long tool call, such as a 500-drawer batch delete, more than the default 30 seconds. Both are test-only; product behaviour is unchanged.
