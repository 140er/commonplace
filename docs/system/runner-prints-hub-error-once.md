### The runner prints a hub error once per poll

- When the hub is unreachable or refuses the runner, one poll pass prints one `runner: hub unavailable (...)` or `runner: hub refused (404)` line. The optional resource report no longer adds a duplicate line before the lease call fails the same way. Different errors in the same pass are still each printed.
