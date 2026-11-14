### Compute management REST and durable authority

Script requester self-approval is denied by default, including a second session
for the same user. Only trusted workspace startup configuration
`allow_script_self_approval=True` permits it; dispatch rechecks that policy.
Fixed read-only operations keep their existing same-owner approval policy.
Taskless discovery scopes remain unsupported: the NOT NULL task column rejects
them before any metadata can persist, including the literal "None". Development
installs include the Compute crypto/SSH dependencies so transport tests collect.
Pre-claim denials and terminal/uncertain outcomes (including recovery
and observed exit codes) enter the private audit journal in the same transaction;
no output or exception text is included. Target execution reads uploads and
staged inputs relative to held directory handles, verifies their approved digests
and gives bwrap sealed memfd snapshots via `--ro-bind-data`. The writable job bind
also uses its held directory descriptor. Sealing uses Python constants when present
and Linux kernel ABI constants otherwise; all four seals must be read back before
launch. Unsupported or unverifiable sealing returns a confirmed denied
`sandbox_unavailable` outcome without spawning a process. Synthetic barrier tests prove substitution
refusal or approved-byte use; they do not prove real-host confinement.

`/api/compute` is installed with the gateway and admits only cookie-person current
workspace owners, including reads. Mutations reuse the mandatory exact Origin and
bounded JSON boundary. Every store operation rechecks the exact session, current
owner membership and canonical board/task authority; PATs cannot impersonate
owners. Workspace-local crypto-aware compute.sqlite3 contains public enrollment
metadata, scoped grants, proposals, approvals and append-only audit; credential
custody is separate and is never opened by these routes. Constructors remain lazy.

Gateway/node creation requires explicitly confirmed full public host-key bytes;
records remain pending_verification with no public verified-state setter. Scopes
are passive-only and bind exact current owner/optional live connection, project,
gateway and canonical task. Bounds: lists100, gateways200, nodes/scopes1000,
executions2000; scope expiry at most one hour, proposal15minutes, approval5minutes
and no later than proposal expiry. Exhaustion conflicts without deleting history.
Exact user/request-key/payload replay reuses a proposal; changed payload conflicts.
Approval recomputes the entire binding/digest and current authority before storing
consent. Epoch/key/task/session changes invalidate it. An enabled executor may
consume approval and execute automatically in the background. Management requests
do not perform network I/O directly. Cancellation can record intent without
confirming remote termination.
