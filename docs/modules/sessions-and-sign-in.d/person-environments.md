- **Owns:**

  - The native consent page shows an unticked `environments:person` checkbox only when that app asked for it. The name is removed before OAuth scope checks, so it is not a policy scope and the token's scope string stays the ordinary scopes.
- **Promises:**

  - The capability is recorded only when the person ticks the box on `cmmn://auth/callback`. A forged checkbox, a non-native redirect, or a request that did not ask for it records nothing. Withdrawing the connection's phone capabilities withdraws this grant too. A revoked token can no longer call the routes.
- **Tests:**

  - `tests/test_commonplace_environments.py` covers the checkbox, the unchanged token scope and a forged checkbox.
