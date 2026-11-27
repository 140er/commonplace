### Today email shutdown

The Today email thread is stopped before the control database closes, as every other background worker is. A stopped notifier runs no further pass, and a pass in flight checks the stop under the database lock before each statement, so no query reaches the database after its connection closes. Closing waits at most one second for a pass that is still running, which keeps email sign-in cleanup from queuing behind it.
