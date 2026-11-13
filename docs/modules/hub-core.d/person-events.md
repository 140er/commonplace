- **Owns:**

  - `person_events.py` projects the current person's web event feed for an explicitly consented native client. `events:person` is a separate capability; OAuth scope stays `memory:read`.

- **Promises:**

  - Read-only, default off, live binding and kill-switch checks, authenticated person/client/workspace cursors, stable opaque ids, plain redacted summaries and local navigation targets. Cursor secrets never enter audit.

- **Tests:**

  - `tests/test_commonplace_person_events.py` covers the real consent boundary, pagination, replay binding, live revocation, machinery refusals, rate limiting and read-only authority.
