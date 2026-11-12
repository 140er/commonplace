### Merge policy as config data

  - An optional `[factory]` section declares what guards a merge: whether a gate is required, the named checks with the outcomes that count as a pass and those that never do, and how often the full suite runs. It is accepted, diffed and versioned with the config; experiments cannot change it.

  - The hub enforces the gate requirement on new `merge` journal steps (the card's newest unvoided gate must have passed). Check outcomes are not reported to the hub, so checks and batch cadence are declared for agents and check tools to read from the accepted version (`orch_config_get`, checkpoint, render, Project settings).

  - Timeout, skip and error outcomes can never be declared a pass. A config without `[factory]` behaves exactly as before.
