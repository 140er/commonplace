- **Owns:**

  - `person_actions.py` verifies separate native-phone consent records, the live OAuth bearer, current person/workspace/client binding and absence of machinery or board coordination authority. The same authority serves card actions and the three chat routes; OAuth principals remain non-person globally.
- **Promises:**

  - The phone keeps exactly its requested `memory:read` OAuth scope; no policy or OAuth scope is added. Separate unticked capabilities are `cards:decide:person`, `cards:approve:person` and `chat:person`. Only a new native PKCE consent records them. The default-off switch is `COMMONPLACE_PHONE_ACTIONS=on`.
  - Card actions require a positive version and an `Idempotency-Key`; receipts commit with the card. Chat posts use the person's ordinary board operation without granting the bearer `memory:write`. Grants and successful writes record the person, connection and client name; audit failure rolls back the action.
- **Tests:**

  - `tests/test_commonplace_person_actions.py` and `tests/test_commonplace_person_chat.py` cover consent, unchanged scopes, current rights, machinery and workspace isolation, revocation, concurrent retries, audit rollback, chat authorship and bounded private list projections.
