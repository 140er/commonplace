### Compute discovery domain boundary

`commonplace.compute.scopes` contains pure, inert validation only. It does not
register gateway routes, read credentials, scan networks, enroll nodes or execute
commands. Scope authority must come from authenticated server-side approvals;
constructing a value is not a grant. At dispatch, workspace, project, gateway,
user, agent and task must all match; expiry is exclusive and revocation denies.
CIDRs are canonical, limited to RFC1918/IPv6 ULA, with at most 16 networks and
interfaces. Only approved passive/active methods are eligible; no active method
is enabled implicitly. Host/rate/concurrency/time/result ceilings are finite
positive integers (4096 hosts/results, 64 concurrent, 100 per second, 300 seconds
maximum); the future dispatch service must account for aggregate consumption.
No remote transport or execution capability is claimed by this domain module.

- **Batch merge lane:** `CLAUDE.md` ("Merging pull requests") is the agent rule. A PR into `develop` merges on a gate PASS plus a quick PR check subset PASS on its exact head, squash with `--match-head-commit`, by its builder. The full suite runs per develop batch, not per PR; a red batch holds merges until a revert or fix lands. The coordinator never merges; `develop` to `main` needs the owner.

- **OAuth consent navigation:** Only a consent form bound to a validated client and
  exact registered redirect adds that redirect's parsed origin to CSP `form-action
  'self'` and shows that same escaped origin as the code destination. Login forms and generic HTML pages remain self-only, including failed
  login retries; a retry that renders authenticated consent gets the same single
  origin allowance. Other CSP directives, PKCE, state and exact redirect matching
  are unchanged. Redirect hosts must be DNS labels with numeric ports; IPv6 literals are refused because Chromium
  ignores them in CSP host-sources; schemes remain HTTPS or loopback HTTP. New registrations
  and authorization GET/POST reject unsafe legacy registrations locally before
  rendering or redirecting. HTTPS hosts ending in digits or `0x` hex must be canonical
  dotted-quad IPv4. Registered-but-invalid errors request re-registration; missing
  registrations retain their separate error. Live redirect registrations must be
  inventoried before deploy; that inventory is not performed here. No stored client
  data is migrated.

- **Instance index figures (#284):** Only the existing instance-admin person/owner
  authority receives additive `mempalace_status.index_stats`; all normal overview
  fields and diagnostics remain exactly as before. Only the added figures avoid
  worker/collection operations; the normal overview retains its existing path. Other callers keep the
  existing overview shape, without feature fields. `/api/admin/instance/index-stats`
  pages workspace IDs in creation/ID order, 100 per page, and closes the control
  snapshot before workspace file reads. Each SQLite query has a 500 ms
  monotonic budget capped by the page deadline, 10 ms busy wait and a progress interrupt; the console request
  retains its 2 s budget and returns unknown for remaining workspaces. Status reads its one workspace under the same 2 s budget.
  Figures expose only `{value, reason}` and workspace IDs. Chroma live SQLite
  embedding rows supply drawers and vector_count (including changes not yet
  synced into HNSW); collections.dimension supplies dimension. Capacity comes
  only from the exact 100-byte, little-endian 64-bit version-1 header pinned by
  Chroma 1.5.9's hnswlib commit 6868102bde454dc761136e1994490133a6a026bb.
  Oversized, truncated and unsupported headers are refused. Model identity lives
  outside permitted SQLite sources (`model_not_in_sqlite`); tombstones cannot be
  proved across unsynchronized SQL/header sources (`unsynchronized_sources`).
  Never estimate either. The segment catalog is read in primary-key order with
  a nine-row cap; more than eight segments returns `segment_catalog_limit`.
  A workspace store override cannot be established from the allowed sources:
  return `backend_not_established`, including compact stores with old Chroma files.
  Generation pointers are only stat-ed: `generation_not_established` prevents
  reading JSON pointers or reporting figures from retired legacy directories.
  A missing pointer with sibling generation directories returns the same reason.
  Rollback-journal databases are read directly. WAL header bytes 18–19 must
  have both existing sidecars, otherwise `wal_unavailable` prevents creation.
  Paths are lstat-ed and symlinks refused (`symlink`); writer contention returns
  `busy`, not corruption. Missing/empty HNSW state uses `header_unavailable`.
  Non-admin index-stats requests receive the exact unknown-section 404.
  Drawer count, vector count and HNSW capacity sum every wing, private ones
  included, so they are reported only for a workspace the viewer currently owns
  (person private reads are owner-only); for any other workspace they are null
  with reason `private_scope`. Status applies the same rule through the loaded
  `private:read` scope. Model and dimension describe the index and stay visible.
  Missing/corrupt/unsupported stores return nulls with stable reasons. No settings,
  dependencies, admission changes, background jobs or index-content reads added.

- **Workspace Admin foundation:** `/app/#/admin` is discoverable only when
  `/api/me.can_admin` is true (signed-in person and current-workspace owner).
  The additive `can_admin_instance` uses the operational predicate below. New GET-only users/connections/runtime/limits/stuck
  inventories (the runtime panel's one write is the owner's machine requester edit: one owner-only add or remove per request on the stored list) bind the decoded path ID to the authenticated workspace before any
  projection source read. Members and owner connections receive the exact
  person-owner refusal; foreign workspace IDs are concealed with 404. Personal
  data remains workspace-local; no global directory or mutation action is added.
  Operational deploy and backup GETs require a signed-in person + current-workspace
  owner + exact server-fetched `users.is_admin=1`, read per request through the
  bounded RO connection, including `/api/me`. Authenticated ordinary failures use
  exactly “Instance administrator access required”; missing users deny and read
  faults on instance data routes fail closed with 503 before source reads.
  A capability-read fault in `/api/me` preserves the 200 bootstrap response with
  `can_admin_instance:false`; data routes always recheck authority. Signed-out keeps
  the existing gateway 401 challenge. No authority extends to other workspaces’ personal data.
  The installed route inventory mirrors both authenticated Admin GET prefixes.

- **Pre-push quick check:** `git push` runs `commonplace.guards.prepush` on the
  files the push changes (git's pre-push input, diffed against the remote tip or
  the development branch): ruff check and format on changed Python, other
  detected stacks, `docs-check` against the base, and the fast tests whose
  names match the changed modules. Default budget 60 s (`--budget` or
  `COMMONPLACE_PREPUSH_BUDGET`) covers all steps together: a step that is
  still running when it ends is stopped, later steps are not started, and the
  run lists them as skipped or cut off. A failed check blocks the push (exit 1);
  an incomplete run does not, and never prints a pass. Every run states that it
  is a quick check, not the full suite or CI. The hooks are one pair of shell
  scripts in the clone's shared hooks directory, written by
  `python -m commonplace.guards.hooks install`; each run picks the environment
  of the worktree it runs in (`COMMONPLACE_PYTHON`, `.venv`, `VIRTUAL_ENV`,
  `python3`) and, when none has the tools, exits with the setup command.

- **Admin read and lifecycle bounds:** New projections use an existing SQLite
  `mode=ro` connection, connection-local `query_only`, at most 250 ms busy/connect
  wait and a 2 second monotonic request budget with progress interruption. They
  acquire no application locks, initialize no stores and invoke no memory worker.
  They capture UTC once through `gateway.admin_clock.utc_now`; the paired
  `monotonic` seam controls deadlines. Credential expiry authentication retains
  its existing clock. Explicit field allowlists omit credentials, raw policy and
  login links; display redaction does not modify stored content. Lists page in batches of 100 with full filtered counts, using creation time/ID,
  project slug/ID or card number as appropriate. Each UI load,
  including initial identity/body decoding, settles within its 5,000 ms client
  deadline independently of other sections and the shared refresh fan-in. Abort,
  page epochs and request generations clear private state on disposal or expiry.
  Only the exact Admin route bypasses shared refresh; card navigation restores a
  regular base view and loads the requested card without new inventory requests.
  Inventory counts share the row membership/user joins; Retry preserves the failed
  cursor and append or replacement mode. Sign-out and expiry stop an existing Admin
  controller; it is null until mounted.
