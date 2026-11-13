- **Owns:**

  - `tests/test_commonplace_person_actions.py` tests native PKCE consented capabilities with unchanged OAuth scopes, owner/member web authority, person/workspace/client binding, machinery refusal, live revocation, default-off visibility, concurrent card idempotency and audit rollback.
  - `tests/test_commonplace_person_chat.py` tests the three phone chat routes, person-message semantics, normal connection behavior, unchanged rate limits, phone-only list fields, shared web activity classification, plain redacted previews, unreadable project/private conversation isolation and constant query count with 50 projects. Existing public-surface and web snapshot pins remain unchanged.
