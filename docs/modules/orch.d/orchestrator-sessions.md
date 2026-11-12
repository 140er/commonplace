- **Owns:**

  - The session guide specifies board-loss acknowledgement and distinguishes unavailable boards from storage failures; session-enabled triggerless configs explicitly skip bounded split-root reconciliation as well as event sweeps.

  - The session guide documents durable uncertainty across owner stop, board and journal failure isolation, local refusal reporting and owner-only session details.

- **Promises:**

  - Per-card event sweeps and bounded split-root reconciliation are skipped for a session-enabled (`session = true`) config without a card.todo trigger, and unchanged for every other config; persistent session watches are independent of those triggers and role leases.

