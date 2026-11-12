- **Tests:**

  - `tests/test_commonplace_oauth.py` holds the native redirect: only the exact `cmmn://auth/callback` registers (other schemes, path, query, fragment, case and userinfo variants and confidential methods are refused), it completes authorize and token exchange with S256, `plain` or no method returns `invalid_request` with no code, its consent CSP names the `cmmn:` scheme, and https registrations keep their origin CSP.
