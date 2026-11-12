- **Owns:**

  - The session guide specifies board-loss acknowledgement and distinguishes unavailable boards from storage failures; triggerless configs explicitly skip bounded split-root reconciliation as well as event sweeps.

  - The session guide documents durable uncertainty across owner stop, board and journal failure isolation, local refusal reporting and owner-only session details.

- **Promises:**

  - Per-card event sweeps and bounded split-root reconciliation require an explicit card.todo trigger; persistent session watches are independent of those triggers and role leases.

