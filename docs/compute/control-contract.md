# Compute control and UI build contract

2026-11-09. Continue real reversible feature implementation. This fixes the next
integration surface; it grants no remote effect or review verdict. Isolated
custody/protocol/transport modules are present. Public execution remains disabled
until the parent wires authenticated current authority and enrollment verification.

## Ownership

Control/API builder: `commonplace/compute/control.py`, `routes.py` and
`tests/test_compute_control.py`, `tests/test_compute_api.py`. No edits to existing
protocol, transport, credentials, scopes or chat code. Parent integrates extension
installation and shared docs/dependencies. UI builder: new
`commonplace/web/static/compute.js`, `compute.css`, isolated UI tests and minimal
existing app/rail/palette/index glue. Preserve Coworkers/project chat and unrelated
surfaces. Do not use concept sample data as production state.

## Authority and storage

Workspace-local crypto-aware `compute.sqlite3` for gateway/node metadata, explicit
discovery scopes, bounded observations, exact action proposals/approvals and audit.
Use existing control users/connections and canonical BoardService tasks; never
create a parallel identity/task registry. Custody may use a separate service-private
`compute-credentials.sqlite3` because its own AES-GCM encryption and safe-open
boundary differ from the workspace crypto connection; no private key ever enters
control rows, events, UI or REST. Avoid opening it in these public routes.

Routes are cookie-person sessions only, with current workspace owner for this
initial management surface. Reject PAT impersonation. Every mutation uses the
existing exact mandatory Origin/non-simple JSON boundary and bounded body. Recheck
current owner membership plus board/task permission at each operation; foreign IDs
are concealed. Caller fields never choose another workspace/user or confer
verification. Prefer the coworker routes' existing auth/boundary helpers rather
than a new weaker variant. Service constructors must not load/init operator keys,
scan files/directories, start threads, open networks or silently enable transport.

## REST (exact version-one surface)

All paths are under `/api/compute`. API module exports `install(gateway)` and
`handle(gateway, handler)`; parent installs it in the extension list. Export
`open_store(gateway, workspace_id)` from control.py for service integration.
Store mutation methods take an actual principal and revalidate it through the
existing control DB; not a boolean authority claim. Document public methods.

- GET root defaults: `{gateways:[],nodes:[],scopes:[],executions:[],capabilities:{transport:false,passive_discovery:false,scripts:false},can_manage:true}`.
  `transport` reports current automatic executor availability. It is a snapshot,
  not proof that an individual target is ready; dispatch rechecks authority,
  credentials and verified bindings.
- GET `/gateways`, `/nodes`, `/scopes`, `/executions`: named array.
- POST `/gateways`: `{board,name,address,port,account,host_key:{algorithm,key},credential_id,credential_version,pin_confirmed:true}` -> `{gateway}`.
  `key` is base64 full public SSH host-key bytes, not an unchecked display hash.
  Numeric management endpoint only. `pin_confirmed` records explicit owner
  confirmation, not a successful connection. New state `pending_verification`.
- POST `/nodes`: `{gateway_id,name,address,port,interface,account,host_key:{algorithm,key},credential_id,credential_version,pin_confirmed:true}` -> `{node}`.
  Exact existing gateway/project, private numeric destination per existing
  protocol validation; no observation auto-enrolls. State pending_verification.
- POST `/scopes`: `{gateway_id,task_number,agent_id?,cidrs:[],interfaces:[],methods:[],expires_at,limits:{hosts,concurrency,rate_per_second,duration_seconds,results}}` -> `{scope}`.
  `expires_at` is finite Unix time. Project derives from the gateway's canonical
  board. User derives from current session; agent_id, if supplied, must resolve to
  a live same-workspace connection. Otherwise current person actor is the exact
  agent dimension. Canonical task belongs to that board. Use NetworkScope and
  DiscoveryLimits, not duplicate looser validators. Only passive methods may be
  granted in this slice; sources unavailable, no actual collection triggered.
- POST `/scopes/<id>/revoke` `{}` -> `{scope}`.
- POST `/executions`: `{node_id,task_number,operation,arguments:{},request_id}` -> `{execution}`.
  Only `system.info.v1`. Derive immutable gateway/node pins, epochs, credential
  references/versions, workspace/board/user/session, task and effective Limits
  from current server records. Canonical SHA256 digest binds exact action plus
  permission-relevant values. Persist state `awaiting_approval`; no network or
  model call. Exact same user/request_id/payload returns same execution; changed
  payload conflicts. No private chat history is mirrored.
- POST `/executions/<id>/approve`: `{expected_digest,expires_at}` -> `{execution}`.
  Current owner only, exact freshly recomputed digest, bounded finite expiry.
  Record current session digest privately, never raw cookie. Approval state
  `approved` authorizes automatic background execution by an enabled worker,
  without another dispatch confirmation, but does not confirm start/completion.
  The worker consumes consent after rechecking authority and verified bindings.
  A public request cannot mark a gateway/node verified or supply transport keys.
- POST `/executions/<id>/cancel`: `{}` -> `{execution}`.
  Pre-dispatch proposal/approval cancellation is confirmed locally. In-flight
  cancellation records intent and does not confirm remote termination.

Responses explicitly project metadata (no sessions, key material, credentials,
callback objects). Gateway includes id/name/board/address/port/account/host_key/
credential_id/credential_version/epoch/state/created_at. Node adds gateway_id and
interface. Scope includes id/context/cidrs/interfaces/methods/expires_at/limits/
revoked. Execution includes id/node_id/gateway_id/board/task_number/operation/
arguments/digest/state/reason/created_at/updated_at and bounded limits. Audit may
be persisted internally without copying request text or secret material.

HTTP success status201 for creates,200 for reads/replays/actions. Errors are fixed
codes: invalid_request400, sign_in401, owner_required403, not_found404,
conflict409, unsupported_operation400, transport_unavailable503 where applicable.
No live execution endpoint is invented. Bound listing sizes/payloads and text.

## UI

Discoverable `#/compute` workspace-owner view with existing warm Workbench style.
Real inventories, empty/setup states, gateway/node enrollment forms, full public
host-key confirmation, exact scope grants/revocation, typed read-only execution
proposal, explicit digest approval and cancel state. Use only the REST above.
Show pending verification, automatic execution consent, and current worker
availability honestly. Approval permits automatic background execution over SSH
without another dispatch confirmation, including when a currently unavailable
worker becomes available before approval expires. It does not confirm execution.
Discovery cancellation is offered for queued, claimed, dispatching and uncertain
attempts. A cancellation request does not confirm remote termination. Link canonical tasks;
use app lifecycle/AbortController and response fences on navigation/signout/workspace changes. Escape remote/user metadata.
No raw secret input, fake gateway, active scan, arbitrary shell or generic proxy.
Scripts follow the immutable bundle, exact approval and verified runtime contract.

## Checks

Meaningful synthetic tests: persistence/reopen, owner/session/PAT/Origin/foreign
workspace failures, current board/task/connection authority, immutable pins and
finite bounds, idempotent proposal/conflicting replay, digest tamper/stale epoch,
expiry/revocation/cancel, no network/key access in routes, UI escaping and stale
response isolation. Only quick syntax/lint/normal commit hooks per child; parent
runs grouped tests and real browser integration. No child tests/builds or live
connections. No independent review/gate claims.
