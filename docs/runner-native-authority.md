# Native runner authority design

Status: design for the native host prerequisite; execution remains unavailable.
This document does not provision an issuer, approve a provider account, or certify
the installed host. Legacy installation and grant receipts remain setup-only.

## Native integration boundary

The runner adapter checks the owner's independently selected issuer pin and
current signed setup grant, verifies release bytes, and invokes the native
status interface through sealed executable bytes. The bounded private request
carries a nonce; both the local verifier and the hub independently verify the
response. Existing version 1 receipts retain `agent_ready: false`.

Native integration must enforce one current authority at each entry point:
authenticated RPC and CLI ingress, thread/run admission, workspace preparation,
provider-session acquisition, queued/recovered dispatch, and the final provider
request. Runtime policy must intersect project, topic, real workspace roots and
mode ceilings with that authority. Neither a transport-authenticated caller nor
an adapter preflight can supply these native checks.

A compatible native release must implement the signed status and scoped
execution protocol and pass its actual enforcement evidence before execution
can become available. CLI presence, public help, a source revision or provider
sign-in alone proves none of those capabilities. Installation-specific source
inventory and probe results belong in private operational evidence, not this
design.

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
