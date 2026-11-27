- **Owns:**

  - `test_connection_requests.py` covers member request and cancellation, owner needs, owner-only workspace-scoped decisions, idempotency and races, one-time private collection, rollback, expiry, trust, restricted-origin cards, private-memory refusal, both revokers, account export and account/workspace purge.
  - `connection_requests.mjs`, also run by `test_connection_requests_ui.py`, drives member typing, form and cancellation handlers, both approval choices, errors and stale Today reads, and checks request states, exact escaped copy and shared counts. Member purposes accept plain words without owner identifier validation. The public route inventory includes the request routes.
- **Tests:**

  - The member submission regression returns 404 on the base and 201 with the request flow. UI condition checks wait for the expected state without fixed sleeps.
