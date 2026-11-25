- **Owns:**

  - Original account records on `runner_sessions` and the internal nullable `account_connection_id` on sessions and `runner_jobs`, with additive migrations that preserve existing rows and thread bindings.

- **Promises:**

  - Sessions compare their current account decision with their original mode, source, kind, label, person and connection identity before every lease and start. A different valid account or a replacement connection cannot reuse an existing thread.
  - Card jobs keep their admitted mode and account attribution. Their original personal connection must remain active on the same machine and belong to the same person before leasing and starting; changing mode settings alone does not reroute an admitted job. Public status and launch views expose no connection or person IDs.
  - Refusing a queued job leaves its lease attempt and start counters untouched, updates only its existing ledger row to cancelled, and creates no start or cost event.

- **How it fails:**

  - A changed or missing pinned identity is refused with `account_changed`; unreadable connection state fails closed with `mode_unavailable`. An org or legacy shared job is refused if its machine becomes personal. Existing machine, requester, grant, binding and board fences remain in force.
  - Old sessions without original account fields retain their thread and state but cannot lease under an invented identity. Old personal jobs without a connection pin are cancelled; old shared jobs remain eligible only on shared machines. An owner can still record a session stop request.

- **Tests:**

  - `tests/test_model_mode_account_fencing.py` uses actual connection registration and revocation for valid-to-valid session changes, revocation and same-label replacement after job admission or lease, unchanged accounts across mode changes, historical attribution, private response fields, counters and migration of populated stores.
