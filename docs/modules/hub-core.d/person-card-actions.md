- **Owns:**

  - `person_actions.py` installs the protected card action prefix only when the instance enables phone actions. Every request verifies the OAuth access token, its recorded person/workspace/client consent, current membership, and absence of machinery or board coordination authority. Each operation resolves the person's current web rights and uses the existing board service.
- **Promises:**

  - `cards:decide:person` and `cards:approve:person` are separate from legacy `cards:decide`. Existing clients and tokens acquire neither automatically. PAT creation refuses these scopes. The instance switch is `COMMONPLACE_PHONE_ACTIONS=on`; any other value disables new consent and actions. No new public route or MCP tool is added.
  - Each action requires a positive card version and an `Idempotency-Key`. Receipts and card changes commit in one board transaction; mismatched retries return 409. Card history retains the person's actor and records the OAuth client name. The control audit records each grant and successful action; a board commit failure after an audit appends a cancellation row.
- **Tests:**

  - `tests/test_commonplace_person_actions.py` proves consent, defaults, scope isolation, current member rights, machinery refusal, revocation, concurrent retries, conflict refusal, audit failure rollback and credential log exclusion.
