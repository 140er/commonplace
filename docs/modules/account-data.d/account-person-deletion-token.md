- **Owns:**

  - `person_token.py` owns the browser re-check forms and dedicated deletion proof. `account_delete_checks` and `account_delete_tokens` are person-keyed credential control tables, exported with credential hashes redacted and removed by account purge.
- **Promises:**

  - The existing account person-only promise has one narrowly scoped extension: only `POST /api/account/deletion` accepts a consented native client bearer together with its one-use deletion proof and matching PKCE verifier. Account read, export and cancellation remain session-only. The shared deletion checks, refusal bodies, grace period, hand-over and purge remain unchanged.
  - A proof spends its compare-and-set before running those checks, including when a blocker refuses the request. It never enters a general authenticator or policy scope list.
- **Tests:**

  - `test_commonplace_account_person_token.py` covers client/person isolation, unchanged member/owner purge behavior, grace, replay, concurrency, account export redaction and table purge.
