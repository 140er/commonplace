### Instance console settings and filter recovery

Workspace settings include workspace identity, account-plan source, effective limits
and orchestration even when no boards store exists. Only the spaces inventory
reports an unconfigured store in place of rows. Instance settings accept no query
filters; validation refusals carry no target IDs. Draft filter text survives
independent section settlement, stale form submissions prevent navigation, and
expired pagination cursors require a refresh before more pages can load.

### Workspace settings presentation

The workspace settings panel renders its settings table and Refresh action only;
project row counts and pagination controls belong to the spaces inventory. The
API retains paginated project-cap metadata for workspace settings. Operator
settings accept no query keys, list routes accept pagination, and workspace
filters apply only to the global workspace, people, connection and audit lists.

### Instance management metadata decision

The decision (locked P1=A, L1=A, B1=A): `/app/#/instance-admin` is a separate,
read-only console. Its `/api/instance-admin/` GETs require a session person and
exact server-fetched `users.is_admin=1`, including admin members. Viewing never
creates actor membership or content authority. Explicit SELECT/DTO allowlists
exclude memory taxonomy, card/message content, credentials, repository links,
host paths and raw audit reasons. Existing workspace Admin and operational
metadata keep their person/current-workspace-owner predicate. The additive
`can_manage_instance` is navigation only: its shared lookup failure leaves
`/api/me` 200 with both `can_manage_instance` and `can_admin_instance` false;
protected routes remain 503. The exact authority denial is unchanged.
Host/framing and rate checks precede console denials; each real identity is
charged once before a target-free audit, with 429 before further denial writes.
Namespace authority precedes same-origin JSON validation; unread-body refusals
close before sending. Only authorized console reads skip home-account storage
and load-plan admission. Ordinary content and runner restrictions remain.
Inventories page before opening only the selected workspace's existing RO
stores. GET never initializes stores, migrates schemas, expires claims or creates
seats. Legacy removed/pause timestamps are null. Missing stores are unknown;
corrupt sources fail closed. Existing account-plan/project-cap/orchestration
metadata is shown honestly, with no new independently editable numeric cap.
Server reads and coordinated audit lock/SQLite waits share a two-second budget;
clients bound transport and body decoding independently per section at five
seconds. No continuous console polling; selection, navigation, expiry and
capability loss abort and clear metadata, discarding late responses.
Every accepted read/filter appends a fixed action/decision/reason with the real
actor and target. Session empty connection IDs map to null. Read success requires
audit success; failure returns 503 without metadata. Denial stays 403 if auditing
is unavailable, with a fixed diagnostic: denial-record loss is an accepted
residual. Audit pagination fixes an upper sequence ceiling before its self-write.
Audit remains append-only and unbounded: N accepted reads/day add at least N
rows/day; rate limits bound bursts, not retention. Operators plan disk/backup
capacity; disabling limits retains their explicit risk.
Later deliberate membership actions may grant normal target member/owner content
authority, never implicit authority to the actor. They are not implemented in
this read-only release. Self-grants must be refused, owner selection explicit
with confirmation/audit, and last-owner/usable-admin/self guards preserved.
An admin may invite a different email they control and sign in as that target;
this accepted alternate-address residual requires an invite audit and cannot be
closed by same-user-ID guards. No impersonation or instance-flag delegation.
