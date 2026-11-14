- **Owns:**

  - `POST /api/boards/<slug>/chat` has one `space_post`, `checked` inventory entry for both ordinary connection replies and consented person messages. The phone branch selects the already-resolved tool instead of introducing a second HTTP-method dispatch.
- **Promises:**

  - Phone chat, decide and approve use `rest_write` with the current person's context. The existing person rule policy stays unchanged. Optional audit attribution records the actual OAuth connection and client name without changing the principal checked by the wrapper.
  - The wrapper returns the audit sequence so a board commit failure can record the existing cancellation row. Audit failure still rolls back the message, card change and idempotency receipt.
- **Tests:**

  - `tests/test_commonplace_rules_enforce.py` retains its AST count, unique-route and freshness assertions. The phone action and chat test files verify the checked path on success and refusal before any board operation.
