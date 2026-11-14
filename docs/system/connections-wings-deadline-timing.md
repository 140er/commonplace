### Wall-clock budget tests are timing tests

This belongs to the Decisions section.

- A test that asserts a wall-clock budget carries the `timing` marker (owner rule, 31 Oct: timing tests are never a merge blocker). The two connections wings deadline tests (1.5 s) failed under load on two PRs while passing alone, so they are marked; the default selection excludes the marker and the serial timing lane still runs them.
