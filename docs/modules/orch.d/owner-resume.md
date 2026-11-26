- **Owns:**

  - `durable.owner_resume`, `tools.owner_resume` and the `POST /api/orch/boards/<b>/roles/<r>/owner-resume` route: the signed-in owner's Resume of a stopped Coworker, and the `history` list the checkpoint returns (newest lease claims, releases, owner stops and owner resumes with actor and time). It is a web action, not an MCP tool (`tests/test_orch_lease_status.py`).

- **Promises:**

  - Resume runs the ordinary `resume` (restore and config fences, in-doubt marking) and hands the claim straight back with `release` inside the same call, writes one `owner_resume` journal row, re-arms the board's trigger cursor and emits `orch.lease.resumed`. The response carries the epoch only; no lease token reaches the page, a log or the history.
  - Only the signed-in workspace owner may use it; members, agent tokens and seats get `owner_only` and nothing is written. Stop is the existing owner-only force-release.

- **How it fails:**

  - While a lease is live Resume answers `held` (409) and changes nothing; a pending restore answers `restore_pending`, a board without an accepted config `not_configured`. If handing the claim back fails, the owner's claim is force-released so the Coworker is never blocked for the lease TTL.
