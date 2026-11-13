### Compute management interface

The owner-only `#/compute` screen consumes authenticated Compute inventories and
existing project lists. Gateway/target forms record full public host keys and
explicit confirmation, never private keys. Enrollment remains pending verification.
The UI can shape passive scopes, revoke them, propose exact system.info.v1 actions
or immutable workspace.script.v1 bundles, and confirm an exact SHA256 digest.
It displays automatic worker availability and labels approval as permission for
automatic background execution over SSH without another dispatch confirmation.
Approval does not confirm that execution started or completed. Discovery
cancellation supports queued, claimed, dispatching and uncertain attempts, with
intent distinguished from confirmed termination. Server authority, pin verification
and digest recomputation remain mandatory. Client validation is not authorization.

Every visit binds responses to its exact session object, workspace, actor and
route. Route departure/sign-out/session expiry abort requests and clear form and
approval state; stale responses cannot populate another visit. Only proposal
creation has idempotent retry identity; ambiguous other mutations require inventory
reload. The management API is installed through the normal extension list;
service/execution wiring remains separate. Full Chromium integration exercises
the real gateway, workspace store and proposal approval lifecycle with synthetic
enrollment data and no remote connections.
