### Today email shutdown

The Today email thread is stopped before the control database closes, through a pre-close callback. A stopped notifier runs no further pass, and a pass in flight checks the stop under the database lock before each locked block of statements and before each read that takes its own lock, so no new query starts after the stop. The lock is not held while the Today list is computed. Closing waits at most one second for a read already under way, which keeps email sign-in cleanup from queuing behind it.
