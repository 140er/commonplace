# Native runner authority design

Status: design for the native host prerequisite; execution remains unavailable.
This document does not provision an issuer, approve a provider account, or certify
the installed host. Legacy installation and grant receipts remain setup-only.

## Inspected boundary and implementation ownership

The existing adapter is `commonplace/runner/host_interface.py`. It checks the
owner's separately selected issuer pin and current signed setup grant, verifies
release bytes, and invokes `runner-status` through sealed executable bytes. The
request is bounded private stdin; its nonce-bound response is verified locally
by `host_authority.py` and independently at the hub by `host_status.py` and
`lifecycle.py`. Both paths retain `agent_ready: false`.

Two distinct T3 Code source repositories were inspected: the installed launcher's
source tree at `d4fc4a20ab20adc78b5f58b425784b902d430a6c`, and the registered
development project at `e7d0ff550f2ac51bac0958021e6caededa7ca18b`. Relevant source
paths in both are:

| Native source | Present behavior | Required additional boundary |
| --- | --- | --- |
| `apps/server/src/cli/acpMcpBridge.ts` and `mcp/AcpMcpStdioBridge.ts` | `acp-mcp-call` transports a tool invocation to an authenticated MCP endpoint. | Bind the caller to the particular signed runner authority; transport authentication alone supplies no runner grant. |
| `apps/server/src/mcp/OrchestratorMcpService.ts` | Resolves launches and applies caller/runtime rules. | Deny runner-originated launch, send, resume, delegate and mutation requests outside the current grant, including direct requests bypassing the installer. |
| `apps/server/src/orchestration-v2/Orchestrator.ts` | Prepares workspaces, dispatches runs and chooses provider sessions. | Recheck the same native authority before workspace effects, session acquisition, queued dispatch and recovery. |
| `apps/server/src/orchestration-v2/RuntimePolicy.ts` | Resolves a thread's mode and worktree/project directory. | Intersect with grant ceilings and descriptor-verified workspace roots; reject overrides that widen either. |
| `apps/server/src/orchestration-v2/Adapters/CodexAdapterV2.ts` | Constructs provider `turn/start` parameters, including cwd, approval and sandbox policy. | Reject a changed account/session or stronger policy immediately before the native provider request. |

Searching both repositories' server and packages found no `runner-status`,
`runner-host-status`, `scope_enforced` or `watchdog_seconds` implementation. Direct
credential-free discovery of the existing installed Node entrypoint succeeded
with version `0.1.3` and help that does not advertise `runner-status`. The launcher
itself failed under a disposable home because it resolves its code relative to
that home; that failure is not evidence of missing native capability. No existing
configuration or provider credential was imported for either probe.

The listed revisions are source inspection evidence, not proof of installed
release provenance. Hashing the small entrypoint shim does not pin its imported
bundle and dependencies. Neither CLI availability nor provider sign-in proves
the missing capability. The native changes belong to the host product, outside
this repository. They require their own managed checkout and release route;
editing either inspected shared checkout is not authorized by this design.

## Selected architecture and provisioning prerequisites

The owner selected a dedicated isolated native authority (option 1 below). No
authorized native issuer, protected principal or managed native implementation
checkout has yet been provisioned. The architectural alternatives were:

1. Provision a dedicated native host authority and isolated execution boundary.
   The owner names its protected principal/store, approves its public pin through
   an independent authenticated channel, and authorizes a host-product change in
   its own managed checkout. A disposable local instance can then prove the
   implementation without changing existing workspaces or host services.
2. Supply an existing independently administered native authority with the same
   enforcement and custody evidence, if one becomes available. Its signed release
   and public pin must be obtained independently; an endpoint URL or key alone is
   insufficient. Until that evidence exists, keep native execution unavailable.

The selected design uses distinct locked, non-login, non-sudo OS identities for
the authority/signer and job execution. A root-provisioned narrow launcher must
create per-capability job scopes with only the approved roots and normal
authorized provider integration exposed. Job processes must not read or trace
the authority, access owner homes or reach an unscoped native endpoint. The
presence of a sandbox binary or user namespaces does not prove those guarantees.

Creating those identities, protected state and the least-privilege launcher is a
privileged provisioning step. It must be performed by the authorized host
administrator, with the exact proposed steps reported through the coordinating
lead first. The native source work needs its own app-managed checkout and
approved release route. This repository slice creates no principal, key, unit,
sandbox or hub pin. No wrapper or setup-only receipt can fill that gap.

## Host change backup and rollback

Before any host configuration change, the authorized administrator records which
new identities, directories and units will be created, checks for collisions and
backs up every existing configuration file that will actually change. Backups
stay private on the same owned machine; credential contents are never copied to
the repository, evidence output or chat. Existing host services and workspaces
must not be reconfigured to become the runner instance.

The change record must include the backup locations, original ownership/modes,
new unit/scope identifiers and rollback commands before activation. Rollback
fences new admission, revokes the new authority and stops only its owned sessions
and job scopes. It restores only the recorded changed configuration and leaves
unrelated units and work untouched. Created principals remain locked and owned
state/audit records remain preserved for review; rollback does not delete user
data or silently re-enable an earlier key. No host configuration is changed by
this design-only slice, so no host rollback has been exercised yet.

## Trust roots and key custody

There are three distinct authorities. The hub authenticates owners, current
account/connection bindings and approved grants. The machine enrollment key
authenticates that installation's exchanges. The native issuer certifies what
the host is enforcing now. None can select another's trust root.

The owner provisions and controls the issuer key lifecycle. The native authority
generates its Ed25519 private key inside the owner-selected protected store;
private material is never delivered with a grant or artifact. A file-based store
requires a 0600 regular file under 0700 parents, outside repositories, job mounts,
model homes and logs. Its principal or proven OS sandbox must prevent job/model
processes from reading it, reaching its file descriptors, tracing the signer or
replacing its executable/state. File modes alone do not separate processes with
the same UID. A same-UID host with unrestricted job shell access therefore cannot
claim custody. The selected distinct-principal architecture still requires
privileged provisioning and proof of the per-job sandbox.

The signer accepts structured, validated enforcement snapshots from the native
authority, never arbitrary bytes or caller-supplied claims. It has no general
signing RPC. The model/job receives neither the issuer key nor a signing handle.
The issuer can attest only after the matching native enforcement lease and
watchdog are active. A child cannot assert `scope_enforced` on the signer's behalf.

Public issuer identity and fingerprint reach the owner through an authenticated
channel independent of enrollment, the downloaded install plan and host response.
The current single installer acceptance compares and pins that public key. The
hub administrator separately provisions the hub's trusted public pin; a machine
response cannot add it. Neither the machine key, the hub receipt key nor browser
approval implicitly becomes an issuer key.

Rotation uses a new key identity and explicit owner pin acceptance plus independent
hub configuration. There is no trust-on-first-use or response-selected rollover.
Revocation disables the old issuer, invalidates its outstanding capabilities and
stops only its owned runner work. Loss or compromise of custody fails closed;
recovery requires re-provisioning, new pin acceptance and fresh grants, without
deleting unrelated work or audit history. No rotation is performed by this change.

## Versioned authority and exact signed scope

Version 1 install receipts, host grants and host status remain their existing
setup protocol. In particular, a valid signature over `agent_ready: false` never
becomes execution permission. Changing a JSON flag or removing the registry's
`installation_setup_only` refusal is not a migration.

A future execution path needs a separately versioned native capability. Before
coding that path, both products must agree on its canonical schema. The proposed
capability binds all of the following, with unknown or missing fields refused:

- Protocol version and purpose, issuer/key identity, native release SHA-256,
  grant request ID, authority epoch, unique capability ID and scope digest.
- Workspace, owner, machine, enrollment and installation IDs, machine-key digest,
  provider instance, worker connection, grant version, binding epoch, settings and
  install-settings versions. Owner/account replacement invalidates the capability.
- Provider/account identity and credential generation obtained through the native
  product's normal current account interface; provider session and thread lineage
  are bound when allocated. A login boolean or a copied credential file is not
  that interface. If the product cannot expose the identity, no provider work runs.
- Exact project IDs and project labels, allowed topics and their provider/model
  mappings, workspace strategy, per-project and overall runtime ceilings, and
  any allowed MCP/tool surface. Extra projects, providers, fallback models or
  access to other workspaces are denied, including from descendants.
- Host-local real workspace roots and stable root identities, joined to the
  approved project IDs. A path supplied by the model or inferred solely from a
  project name is not authoritative. Mount and inode identity must be checked
  across dispatch; symlink, mount or worktree replacement invalidates authority.
- Issued/expiry timestamps, maximum five-second renewal/watchdog lease, hub
  freshness/authority epoch and a fresh challenge nonce. Canonical UTF-8 JSON uses
  sorted keys, finite values, no duplicate keys and explicit integer versions.

The host verifies hub signatures against separately provisioned hub trust and
proves possession of the delivered grant secret without exposing it. It checks
current owner/account/connection state, not just a still-valid old signature.
Grant secret hashes and signature bytes never appear in public audit output.

The signed status protocol must expose that exact enforced capability digest and
native release identity. The current version 1 status has neither an account nor
a real-path attestation and is insufficient for productive readiness. A future
version must be implemented and verified at both ends before any readiness path
can accept it. Unknown versions remain unavailable; there is no downgrade to a
successful version 1 setup response.

## Native admission and OS boundary

Native authority is attached to the authenticated runner principal at ingress.
Caller JSON cannot remove it, select another authority or become an unscoped
desktop principal. A separate isolated instance must not import the owner's
global desktop permissions, sessions, cookies or provider homes.

Every path capable of starting or widening work must apply the same admission
check: public RPC/MCP, CLI bridge, launch/send/resume/delegate, background actions,
recovered queues, runtime/model switches and direct provider session acquisition.
Checks occur before workspace checkout, filesystem effects, provider contact or
process creation. Thread titles and prompts do not enforce topics. Native state
binds each thread and descendant to the approved project, topic and authority.
Missing context denies the request rather than treating it as local-owner work.

Effective mode is at most the project and global ceilings. Invalid higher modes
are refused rather than silently upgraded or accepted through sandbox/approval
overrides. A full-access setting never grants access outside the approved roots
or to the issuer. Cwd alone is not filesystem confinement. A verified OS boundary
must constrain filesystem mounts, process access, inherited descriptors,
credentials and any network/IPC capable of reaching an unscoped host interface.
Until that boundary is selected and demonstrated, the host must refuse modes or
providers that cannot maintain it.

The native signer/authority, provider broker and runner-job processes require
explicit separation. Credentials stay with the normal authorized native provider
integration; grants cannot supply arbitrary homes or credential paths. Account,
session, worker-connection and authority generations are rechecked under the
dispatch lock immediately before each provider action. Replacement after queueing
causes denial and owned-work stop, without an external/provider fallback.

## Renewal, cancellation and recovery

The capability's short native lease is independent of the longer setup grant.
Only an authenticated current hub renewal, checked by the native authority,
extends it. A wrapper heartbeat, stale signed response or job activity cannot
renew it. A monotonic local watchdog enforces expiry within five seconds even
when the hub, runner adapter or issuer frontend is offline or stalled.

Nonce consumption and capability/authority epochs are atomic and durable.
Replayed status or start requests cannot create a second run or extend a lease.
After a restart, all recovered capabilities are inactive until fresh hub state,
native enforcement and account bindings have been verified. Clock rollback,
corrupt state, signer failure and unknown revocation state refuse renewal.

Stop/revoke has priority over renewal and launch. Under one native authority lock,
it marks the capability stopped before cancelling its provider sessions, queued
commands and process scope. Late start/completion/renewal replies cannot revive
it. A bounded confirmation distinguishes stopped from unknown; it never reports
success solely because an interrupt was sent. Stop remains callable without a
valid issuer pin or expired execution permission.

Track the specific native sessions and a non-escapable process scope per
capability. Stop that scope and its descendants only; never kill by executable
name, broad process group search or provider account. Existing unrelated host
threads, jobs and services remain running. Descendant escape and disconnected
provider sessions need actual OS/provider evidence, not just a recorded PID.

## Failure model, audit and proof

Threats include malicious job/model input, copied or replayed grants, altered pins,
compromised downloads, stale account bindings, path substitution, direct native
RPC bypass, queued/restart races and loss of network or adapter. A compromised
kernel/owner administration or an independently compromised hub trust root is
outside the confidentiality claim; same-UID shell access is not outside it unless
an effective sandbox prevents access to the signer and its state.

Audit records contain fixed refusal codes, safe opaque capability/job identifiers,
versions and public release/key fingerprints. Do not log grants, tokens, private
keys, provider account details, session cookies, user prompts, raw native error
responses or credential paths. Key-file read failures and host exceptions use
fixed redacted errors. Private proof may record public fingerprints and file
paths; it must not capture secret bytes, environment snapshots or authentication
screens. Public documentation contains no local-machine paths or account names.

Required implementation proof is separate from this design:

1. Run identical affected behavioral tests against the pinned base and changed
   head; report genuine denial/admission failures, never setup errors as RED.
2. Exercise actual native ingress directly with wrong pin, nonce replay, stale
   scope, foreign project/topic, excessive mode, changed real path, revoked or
   replaced owner/account/session/connection and expired capability. Observe
   denial before workspace or provider effects.
3. Obtain a fresh nonce-bound signed status from the actual native issuer while
   its enforcer/watchdog is active. Verify independently at machine and hub.
   A generated test key or self-reported enforcement flag does not prove this.
4. On a disposable local instance, stop owned work online and with the hub/adapter
   disconnected. Measure the watchdog bound and show a concurrently unrelated
   harmless process/thread survives. Prove restart and cancel/launch races.
5. Through an already authorized normal provider/account path, complete one
   harmless scoped run. Missing authorization is an explicit blocked step;
   there is no synthetic run or silent API/paid fallback.

Artifact delivery and the later fresh-target binding/run journey are separate
work. Delivery must preserve setup-only state until this native prerequisite has
landed and passed its own evidence and independent reviews. This design supplies
no live issuer signature, native enforcement proof or productive-run result.

## File contract for the current repository slice

The initial slice owns this design, `docs/system/runner-native-authority.md`, and
the matching new documentation fragments under `docs/modules/project-docs.d`,
`docs/modules/runner.d` and `docs/modules/tests.d`. Native source is read-only.
Any Python adapter changes require a separately reported exact file list after
the native interface and protected principal/store are available. It does not own
artifact delivery, Compute overview, Activity UI or validation infrastructure.
