# System

Decisions, numbers, stores and accepted residuals that hold across modules.
`MODULES.md` (the blocks are in `docs/modules/`) says which module owns each
path; this file, with the sections in `docs/system/`, says what the whole
system promises. A change that alters a decision, a number, a store or a
residual updates this file in the same change; `docs-check` enforces that
(see "Keeping this file current").

Labels: **M** = measured (from code or a run), **T** = target, **G** = guess
that rollout must measure. `file:line` references are to the tree in which the
entry was written.

## Decisions

### Coworker UI lifecycle

`/app/#/coworkers` adds a discoverable private conversation surface without
replacing existing project chat. Server data drives bot profiles, DM/group
membership, messages, jobs/task links and scoped memory. Multiple-bot groups
require an explicit or unambiguous recipient; ordinary group discussion starts
no bot broadcast. Message receipt and pending work are distinct from answers; canonical task
admission is labeled Task created, not task completion.
Conversation controls wait for both the detail and authorized bot directory,
including when detail arrives first. Input limits match server message/memory
bounds. The composer remains available during background jobs; ambiguous HTTP retries
reuse their original request key. Conversation changes, lost access, workspace
changes and sign-out invalidate older responses and clear private views. No
worker identity, model response or live execution is fabricated in this UI.
An addressed human message retains its person author even when `bot_id` names
the recipient. Bot names, badges and styling require the exact matching bot actor.
Human group invitations currently use explicit existing workspace user IDs;
a member directory is not implemented by this slice.

### Coworker background runtime

The worker starts with the chat installation and stops before database close.
Four bounded front threads rotate registered workspaces and preserve each
conversation/bot's order; at most two calls enter one configured provider endpoint.
Model requests have a 3.5 second timeout, bounded input/output and atomic per-day
attempt accounting. This is a responsiveness limit, not measured real-provider
latency. An endpoint circuit opens after repeated failures; no silent provider
fallback or automatic failed-turn replay occurs.
Board-less conversations use local providers only; inherited workspace hosted
settings are refused with `needs_configuration` before context preparation,
credential resolution or provider calls. Hosted conversations associated with
a board still require the existing project Turn on; no coworker Turn on exists.

The internal Store.runtime_job rechecks the exact originating session, live
membership, bot binding, running state and memory visibility before context is
sent and before publication. Its session digest is never in public job views.
Only the conversation's bounded history and explicit verbatim memory enter model
context. Recall ranks the at-most-100 visible memories by query word overlap
with recency as a tie breaker, examines at most 32 ranked candidates, and includes
at most five whole records totaling 4000 characters; no record is summarized
or cut to fit. This bounded lexical selection does not promise exhaustive recall. No workspace-wide memory search or tool execution is granted to model
output. Explicit `/task TITLE` plus optional exact body admits a canonical todo
card on the conversation's authorized board; task_created acknowledges admission,
not task completion. When existing orchestration and a runner are configured,
the internal person-owner start_chat_task checks the opaque canonical origin,
current card/configuration and caller authority, then uses the canonical fenced
runner admission with a stable hashed origin key. No broad control/boards lock
spans admission. Replay reuses the same runner job; configuration changes conflict.
Unconfigured or refused admission preserves the real card and a typed reason.
Runner queued/leased/starting/started/uncertain/cancelled facts are distinct from
the finished front turn; the UI names these admission outcomes and links the
canonical task for current progress. This retains the existing runner admission
snapshot/fence semantics, not a new instantaneous revocation guarantee.
Execution still requires existing accepted runner configuration and actual worker
launch. These tests exercise durable admission, not a real runner process. A lost acknowledgement does not trigger
blind replay. Tests use blocked synthetic providers; no production model latency
or real worker launch is claimed by these checks.

### Coworker conversation storage and REST boundary

The coworker app adds private DM/group audiences alongside existing Space chat;
it does not mirror private messages into public board timelines. Its
workspace-local `coworkers.sqlite3` uses the existing workspace crypto connection
and creation gates; no plaintext-encryption guarantee beyond that configured
mechanism is implied. Bot presentation/configuration grants no worker connection
or execution rights. Existing connections, workspace users and canonical cards
remain authoritative. Bot creation is owner-only.

The person-session `/api/coworkers` routes require a mandatory exact allowed
Origin and bounded non-simple JSON request for writes. They persist original
messages and addressed jobs atomically without provider calls. Reads and writes
require current conversation membership, with foreign/private IDs concealed.
Jobs retain only the originating session digest, never the cookie, and recheck
that authority before dispatch/publication. Exact retries return stored intake;
changed text/recipient under the same key conflicts. Multi-bot group messages
without a single resolved target do not wake every bot. Explicit conversation
memories remain verbatim; tombstones hide active recall and fence responses
created with older visibility. Pending jobs survive restart; uncertain running
jobs become interrupted instead of blind redispatch. Runtime installation is
separate; a stored pending job is not evidence of model response or worker launch.

### Coworker task intake idempotency

The internal person-only `BoardService.ensure_chat_task(origin_key, request)`
creates the canonical card and actor-scoped opaque origin binding in one boards
transaction. Exact request retries return the same visible card; changed board,
title or body conflicts. Distinct origin keys preserve legitimately repeated
requests. No private conversation text is stored in the binding or copied into
Space chat; the caller must obtain explicit intent before sharing requested task
text with the selected board. This internal seam adds no public agent tool or
permission and rechecks existing board write authority on replay.

### Canonical work progress in coworker conversations

Existing conversation GETs add per-viewer job.task_progress snapshots from the
canonical card, exact originating runner admission, task events and visible
evidence. Nothing is copied into shared chat messages and no GET launches,
approves or updates work. The existing card remains the decision/artifact flow.
Fresh origin and viewer sessions, membership, visibility version, bot binding,
conversation board and canonical task/origin authority fence source reads and
publication. Lost front acknowledgements recover the existing origin/card link
without relaunching its runner. Legacy jobs without a captured binding fail closed.

Each response projects at most20 tasks, selects six events from32 candidates and
five evidence candidates, and omits whole fields over2000 bytes with an explicit
notice. Source text remains exact except existing credential redaction. Runner
admission, task status, comments, recorded decisions and evidence are separate
facts; snapshots do not prove execution or instantaneous revocation. The composer
stays available while the conversation refreshes canonical progress.

### Targeted coworker controls

`POST /api/coworkers/jobs/<originating-job-id>/controls` accepts only explicit
follow_up/stop intents with actor/operation request keys. Same-origin person
sessions, current conversation/origin/viewer/bot/visibility and exact canonical
origin/run bindings are rechecked before admission, effect and publication. A
workspace-crypto-aware task_controls journal commits a conservative uncertain
receipt before crossing stores; exact retries return that receipt and never
repeat possibly applied effects. Changed payloads conflict. Limits:4000 instruction
characters,100 immutable intents per job and10 projected receipts per viewer.

Follow-ups save only the explicit exact words as canonical task comments and
report that worker application is unsupported. Queued/leased stops use canonical
runner cancellation to fence launch. Starting stops record intent with termination
unconfirmed; already-started worker stop is unsupported by current runner APIs.
No control launches replacement work, approves a decision or treats task/front
completion as proof of termination. The UI retains stable attempts through lost
acknowledgements and reconciles receipts on reload while keeping the composer usable.

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

### Passive gateway cache discovery component

PassiveCollector reads only explicitly configured trusted cache suppliers under
fresh exact DiscoveryContext/NetworkScope checks. Active methods, unknown sources,
foreign actors/tasks/projects/gateways, expired/revoked or expanded grants deny.
ARP/IPv6 neighbor parsing keeps numeric address/interface and bounded inert
metadata, preserving gateway/source/time provenance through cautious deduplication.
Host/rate/result/time/source-byte/line/output ceilings and cancellation are checked
throughout each collection. Blocking trusted callbacks cannot be forcibly stopped;
durable cross-job quotas/persistence and controller integration are separate.

### Typed helper protocol and script runtime components

`compute/protocol.py` carries the strict, bounded wire shared by the controller
and the target helper, including chunked script uploads and the typed passive
cache request. `compute/scripts.py` and `compute/script_sandbox.py` hold immutable
script bundles and a default-off confined Python runtime, and
`compute/target_helper.py` is the only target-side entry. These modules are inert
until trusted configuration enables them and perform no remote effect on import.

### Credential custody and pinned SSH transport components

`compute/credentials.py` holds encrypted SSH key custody: strict protected wrapping
keys and binding-authenticated ciphertext, with only public enrollment data
returned. `compute/transport.py` and `compute/script_transport.py` open the pinned
two-hop SSH channel for the fixed probe and confined helper command, and
`compute/fence.py` guards authority writes with a cross-process fence. All of them
are inert until trusted configuration enables them, deny on host-key mismatch,
replay or unknown operations, and use no live endpoints in tests.

### Coworker task dispatch component

A coworker chat message that carries explicit task syntax reaches the canonical
boards only through `BoardService.ensure_chat_task` and `start_chat_task`: the
origin binding commits atomically with a stable per-actor key, the existing
person-owner fenced runner admission is requested, and a refusal leaves the
card in place without inventing a worker launch. Model output never executes
tools. The coworker store, runtime and task controls stay local to the workspace.

### Compute control startup configuration

`ControlDB` takes an optional `compute_config` at service startup. When present it
registers the control database with the cross-process authority coordinator, attaches
persisted write guards and wraps the database lock so each transaction holds the
coordinator for its whole duration; shutdown signals every service before joining
it. Without the argument nothing changes, and no request, import or environment
variable can supply the configuration or enable execution. The owner-session
management routes and script control path are installed through the normal
extension list and perform no remote effect until the trusted configuration enables
them.

### Compute execution component

`compute/executor.py` runs durable execution attempts for approved actions: each
attempt is recorded before any exec, checks exact signed enrollment, current
authority and custody at every stage, consumes its approval once and keeps an
uncertain outcome reserved rather than assuming termination. Published evidence is
bounded and redacted. The owner UI shows the observed exit, or an uncertain
cancellation, and never implies dispatch from approval alone. The executor is
inert until trusted configuration enables it.

### Compute discovery service component

The discovery service stores one durable reservation per collection and charges the
reserved scope ceilings permanently, so a restart never re-collects automatically
and unknown concurrency slots stay reserved. Collection reaches a target only through
the pinned outer SSH adapter and only when trusted configuration enables discovery;
owner-session routes expose scopes, proposals and cancellation. Cancellation records
intent and distinguishes it from confirmed termination.

### Compute operator recovery component

`compute/operator.py` gives a trusted operator fenced recovery for controller
execution and discovery, and for helper tunnels and passive reservations that stayed
uncertain. It requires the existing persisted guards before attaching, writes a
private audit note for each action and has no HTTP route. Recovery never implies that
a remote process terminated.

### Workspace page controllers for Compute and Coworkers

`web/static/coworkers.js` and `compute.js` are self-contained page controllers: they
abort stale requests when the session, workspace or route changes and render only
escaped text from server-authoritative state. They are deliberately not wired into
the shell, rail, phone navigation or palette: the Deck look owns Compute navigation,
and a classic-look entry needs a separate design decision. Neither controller enables
remote execution; that stays a trusted server-side configuration.

OSCacheSupplier supports only the fixed /proc/net/arp cache, no caller path,
subprocess, DNS or probe; IPv6 needs an explicitly trusted supplier. Helpers expose
typed collect_passive only when collector, independent protected ceiling and
fresh scope resolver are all explicitly configured. Default remains unavailable.
Synthetic tests inspect no current host/LAN cache. Observations grant no enrollment
or execution rights and cannot become commands or memory instructions.

### Durable Compute execution component

The default-off ExecutionService uses signed, exact enrollment receipts from
separately pinned provisioning public keys; a browser owner cannot assert live
verification. Receipts expire within one hour and rebind proposals on verification
changes. A participating AuthorityFence must cover control, boards, Compute,
custody, enrollment and gateway admission. Explicit trusted ExecutionConfig now
installs bounded cross-process POSIX locking, durable authority epochs and
persisted SQL guards for central local authority writers. Private registered
functions reject unparticipating legacy connections; missing/replaced participant
files, changed guards or unknown workspaces deny. ControlDB locks/transactions,
BoardStore connections/transactions, Compute journals/enrollment and custody
transactions participate. UPDATE guards cover permission-relevant columns;
connection last-used timestamps, progress counters and display text are excluded.
Real heartbeats renew claim_until, and sign-ins/new cards insert rows, so they can
still advance the epoch. In-flight attempts re-read exact current authority and
active custody through _fresh instead of comparing the coordinator-wide epoch.
Task snapshots exclude the renewable claim_until, so a holder's heartbeat or
comment on the bound card preserves agent-bound discovery evidence. Assignee,
status and the canonical claim_expired_at marker remain bound. Older bindings
compare without claim_until while preserving original consent and permit bytes.
Task-snapshot presence must match; task-less approval grants no dispatch.
Permits retain the request epoch; session/connection/membership/consent/task/origin
release or credential revocation still denies effects and publication. Exact legacy
guards are migrated under the coordinator; unknown guards still fail closed. These local domains exclude gateway_admission. Explicit trusted
admission_mode=controller-ssh-permit-v1 installs a separate wire adapter:
request/ready/go/challenge, fresh fenced durable one-use permit issuance,
lock release before send, independent helper validation/replay consumption,
then opened and the independently pinned inner SSH hop. Mode changes alter the
canonical approval digest. Controller and helper journals preserve earlier
records through additive migration, and issued-permit uncertainty keeps its
capacity reservation. Unconfigured execution still stops with
gateway_admission_unintegrated. The fixed-operation channel does not enable
passive collection, which needs a separate purpose-bound admission path. No instantaneous
remote revocation or filesystem rollback guarantee is claimed.

Approved actions are consumed once into append-only identity-bound execution_runs
before keys or network are opened. Exact origin/approver sessions, fresh canonical
authority, pinned enrollment, credential versions and expiries are rechecked at
stages. Claims cannot be deleted/rebound or automatically replayed. Stage 4 commits
before either exec channel opens. Recovery and returned failures below stage 4
settle known non-runs. Missing permits prove non-runs only in wire mode; legacy
stage-4 unknowns retain capacity. Authenticated confirmed script non-runs settle too.
Real uncertain outcomes retain capacity until the trusted local operator CLI
records external reconciliation, its private note and an audit event through the
fence (terminal reason operator_settled). The CLI checks every participant's
expected guards before attaching; missing guards cannot initialize an unrelated
workspace. No HTTP settlement route is exposed.
Pre-effect cancellation can be confirmed;
later cancellation is intent until exact exit evidence arrives. Canonical tasks
are not marked complete by probe completion. Shutdown requests cancellation and
reports thread completion; it cannot force-stop Python threads or prove remote kill.

Custody opens lazily from an already-provisioned private workspace file and
external wrapping-key path. A handle rechecks current binding at actual SSH auth.
Results remain workspace-local: at most4096 bytes are considered for UTF-8
redaction before persistence, binary/oversize output is omitted, serialized
evidence is at most8192 bytes. Lost audience authority withholds remote text.
The owner UI displays admitted/running/unknown/observed-exit facts, redacted
evidence and cancellation intent separately from stored proposal approval.
Trusted per-workspace startup configuration wires service installation and
shutdown without key initialization. Host shutdown refuses to report clean if
worker threads remain. Independent provisioning and
external reconciliation/restore procedures remain separate requirements.
Compute management fence timeouts return a bounded503 authority_unavailable.

### Durable passive discovery

The owner-session `/api/compute/discoveries` API records exact scoped intake and
serves workspace-local observation history. Its crypto-aware
compute-discovery.sqlite3 ledger permanently charges reserved scope ceilings;
unused, cancelled and denied allocations are not refunded. Uncertain attempts
retain concurrency/rate slots and never automatically recollect. Backups require
independent reconciliation before restoring collection.

Trusted explicit discovery startup reuses the registered workspace authority fence,
custody and pinned outer SSH helper, with separate signed gateway discovery
receipts. Imports and HTTP requests never load keys, read host caches or connect.
A local lease alone does not qualify as remote admission participation. Explicit
trusted discovery_enabled, discovery_sources and controller-ssh-permit-v1 mode
select the separate purpose-bound passive adapter: ready, durable collection
intent, challenge, fresh fenced one-use permit, helper validation and durable
replay consumption before any cache supplier call. Exact scope, request digest,
attempt, epochs, source configuration and short expiry are bound on both peers.
Discovery completion validates the durable permit independently of publication
freshness, releasing its slot while withholding observations after authority loss.
No issued permit means failed with slot=0. Uncertain controller discoveries have
fenced operator recovery after restart, with an audit note and terminal reason
discovery_operator_settled; quotas, attempts and permits remain intact. Discovery attempts/permits use immutable durable journals; helper replay records
have a separate passive namespace and preserve existing execution records.
Discovery-only startup installs the same required local authority participation
without enabling execution. Central shutdown signals and joins configured workers.
The Compute screen shows collection availability, exact current scope/source forms,
replay-safe intake, escaped observations/usage and cancellation intent. A passive
observation never enrolls or authorizes execution on a target.
Default collection ceilings are32 hosts,32 results,32 observations/second and10
seconds, further clamped by scope and workspace/gateway policy. Response result
counts are bounded by the configured results, source lines and output bytes.

### Immutable script bundles and confined target helper

The workspace.script.v1 component stores exact UTF-8 Python source in a
crypto-aware immutable bundle store (32KiB/script,256 bundles,100 listed).
Manifests bind existing identities/enrollment, exact script/runtime digests,
arguments, input hashes/paths, output paths, unprivileged runtime and finite
resource limits. Value construction grants no authority. Owner script REST and existing Compute approvals/ExecutionService now consume
these bundles. Trusted scripts_enabled and ScriptRuntimeConfig bindings require
an independently signed ScriptRuntimeReceipt, accepted only through the private
offline verifier seam and stored in guarded enrollment_verifications.script_runtime.
No public request supplies runtime identity, confinement flags, roots or keys.

A separately provisioned target helper uses a fixed SSH command after the pinned
inner connection. Its strict begin/ready/chunk/commit/result/data/done exchange
is bounded by64 frames,192KiB and40 seconds, with exact digest checks and a
separate cancellation frame. Private atomic uploads use protected openat paths;
durable target claims prevent replay of incomplete or restored attempts.
The64-claim cap counts retained claims: finished claims with recorded expiry and
a protected, parseable terminal result.json with termination_confirmed exactly
true can be pruned only more than300 seconds after expiry. Unconfirmed/uncertain,
malformed/unreadable, unfinished, unexpired and legacy claims remain fail closed.
Before creating a claim, scratch needs16 available inodes and1MiB available block
space; low resources/statistics failures deny as sandbox_unavailable, an
authenticated known non-run. This check does not reserve space across jobs.
After confirmed termination, iterative cleanup restores directory permissions
and removes up to4096 entries without a depth cutoff or descriptors per ancestor.
Cleanup I/O errors preserve observed results; later admissions still require
resource headroom.
Lost control-channel receives become target cancellation intent, including before
spawn. After child/pipe exit, group emptiness is polled within the remaining
original one-second kill bound. One final observation determines state and
confirmation; populated groups remain uncertain/unconfirmed with scratch
retained. Running groups must be observed empty before scratch cleanup; failures
with no spawned process are confirmed non-runs. Target result persistence/cleanup
does not settle a controller run whose result acknowledgement was lost.

Execution defaults off. It requires a non-root capability-free host identity,
mandatory namespaces, no network, a digest-bound read-only root-owned runtime,
bounded tmpfs, delegated cgroup-v2 CPU/memory/pids controls and descendant kill.
No raw host-shell fallback exists. Missing provisioning or kernel features deny.
Results distinguish observed exit, cancellation intent and unknown termination;
controller projections redact bounded output and expose artifact metadata only.
Actual positive confinement has not been verified without a separately
provisioned disposable fixture; deterministic wire/storage tests do not prove it.
The owner script UI follows the frozen REST surface for exact UTF-8 upload,
source/manifest inspection, replay-safe creation and immutable bundle proposals.
No runtime roots, verification flags, keys or upload inputs appear in the form;
real API/browser tests cover upload, source inspection, immutable proposal,
exact approval and reconnect using signed synthetic runtime metadata. Actual
outer/inner SSH wire fixtures never execute host scripts. Logical manifest epoch
is None; the fresh upload request carries authority epoch and approval digest,
covered by the outer immutable operation and purpose-bound one-use permit.
Old integer-epoch script documents and the fixed-probe wire remain unchanged.

### Isolated Compute custody and SSH transport

Compute is an opt-in dependency extra (Paramiko >=5.1,<6); installation enables
no routes, listeners or remote effects. The isolated credential store uses
service-private POSIX safe-open checks, AES-256-GCM wrapped workspace keys and
metadata-bound encrypted Ed25519 credentials. Explicit wrapping-key initialization
is separate from database initialization/open. Missing material never regenerates
a key over ciphertext; only public enrollment records leave custody. Pending keys
require explicit activation; exact-bound authority callbacks, expiry and local
revocation are checked again when a transport handle yields private bytes.
Python memory zeroization and remote authorized-key removal are not guaranteed.
Offline wrapping-key rotation/recovery and the OS worker/service isolation remain
integration prerequisites, not implemented release claims.

The helper/transport modules default disabled and accept only protected enrolled
numeric routes. Full outer gateway and nested target host keys are compared
before their separate public-key authentications. No ambient SSH config, agent,
password, generic forwarding API or direct-target fallback is used. Only the
literal system.info.v1 probe is compiled. Bounded exact JSON control frames
(maximum 8KiB), five-second admission challenges and durable replay consumption
precede route opening. Defaults/ceilings: 30-second runtime, five-second connect,
64KiB combined output, 1MiB tunnel bytes and two active tunnels per gateway.
Failed opens and successful target.close release helper concurrency, including
after abnormal relay endings, while retaining replay tombstones. Close failures
and crash-orphaned consumed rows stay counted. Named helper tunnel/passive rows
have operator recovery under the same SQLite admission fence, with private audit
notes and operator_closed state. Late completion cannot re-reserve them. Closure
alone does not prove target process termination.
Remote output is raw bounded bytes and must pass redaction/audience checks before
publication. Passive observation sources and scripts are deliberately unavailable.

Synthetic socketpair fixtures exercise actual Paramiko handshakes and encrypted
custody keys through two SSH hops, successful fixed-probe output, and independent
wrong-pin refusal before the affected authentication. They use no network/device
endpoint. Public enrollment/approval APIs, controller authority fencing,
background execution/outbox integration and deployment prerequisites are still
required before enabling this transport for users. The pure callbacks do not
claim atomic cross-store revocation. Custody filesystem tests require native
POSIX ownership semantics. Environments that remap ownership must fail the
safe-open checks.

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


### Inert area configuration preview

Area declarations can be parsed, rendered, diffed and proposed, with up to eight
distinct sub-orchestrators directly under the single parentless orchestrator.
Area, parent and kind stay frozen in experiments. Store acceptance and direct
write reparse then refuse any area-bearing config with `area_not_supported`
(400, path `roles`) before effects, for live and experiment channels alike.
Refused accepts preserve the proposal; refused writes roll back their insert.
Accepted versions, cursors/generations, leases, jobs and events stay unchanged.
This preview creates no area seats, credentials, subscriptions or runtime, adds
no activation switch, and keeps existing owner/local CLI and no-area behavior.

### Legacy client memory safety stop

Legacy sync refuses memory installs and old/unmarked configurations. Transcript-only
reinstall binds versioned local consent to the exact explicit destination; no hosted
default or automatic migration remains. CLI checks precede tokens/state; daemon checks
precede all state and transport. Import-kind OR native-memory-source operations are
inactive at every scan, read, retry, rearm and dispatch boundary. Existing rows, source
statuses, seen history and SQLite schema are preserved. Status reports retained rows
as disabled with coverage unverified, excluding them from pending/parked counts.
Caught-up transcript cursors end continuous backfill despite retained memory rows;
no-stream and capture-off semantics remain intact, and status inspection is read-only.

Consent binds the endpoint only. A changed workspace token at the same endpoint is
still accepted; principal/rotation binding belongs to a later slice. Import credentials
are not feed credentials: feed selection accepts only Claude or Codex capture tokens.
This safety refinement was selected from the plan review; missing selected-stream
tokens still fail during initialization even when another capture token serves the feed.
Stop old processes before update and explicitly reinstall
transcript-only or uninstall: unchanged service restart policies retry old rejected
configs. No deployment, replacement importer or memory-coverage completion is claimed.

The generic `agent-log` capture source extends feed token selection to its own
capture token, still never an import credential. Its records name `agent` and
`session_id`; the capture identity `<agent>:<session_id>` lives in the filing
actor's private namespace, so privacy, opt-in, cadence and idempotency are those
of the native sources. Native agent names are reserved and a stream cannot carry
records of another agent or session. No Cursor database reader is claimed.

### Runner-start void semantics and limits

- A same-step retry of an unvoided step returns the stored job; any mismatch is a 409 with the stored step.

- A voided runner-start key instead returns `step_voided` (409), even for a changed request; other kinds retain `step_conflict`.

- Cancelling a job that belongs to a step is person-owner only through the standalone cancel route.

- Owner/local void also cancels linked queued or leased jobs atomically.

- Voided steps keep counting their jobs; refusal is `at_cap` with workspace orchestration off and `project_limit` with it on.

- A card may declare a path contract: `paths`, up to 32 repo-relative globs set with `task_update(paths=[...])` (`[]` clears) and shown on the card. Globs are stored canonical and brace-free: empty and `.` segments dropped, `name/..` collapsed, `.` is the repo root `**`, `{a,b}` alternatives expanded into separate globs (at most 64 per glob and 64 stored per card). A `..` that would leave the repo or follows a wildcard, a leading `/` (also at the start of a brace alternative, whatever precedes the group), an unbalanced or comma-less brace group and a larger expansion are refused when the contract is set. **Who may change it:** `paths` is an editing field, so only the holder of a `doing` card, the review editor, or the creator of an unassigned todo or backlog card (and a person) may change it. While the card has a runner job (queued, leased, starting, uncertain or started, of any age) only the workspace owner (a signed-in person with the owner role) may clear or narrow it; anyone else who may edit the card can only keep every glob and add more. An agent that is refused because of a running job asks the owner to clear that card's paths (`task_update(paths=[])`). Any change, the owner's included, takes the runner write lock before the board transaction (runner, then board: the order of starts), so it cannot land between a start's contract read and its job commit; a non-owner's clear waits for the start and is then refused. A card that counts as running (a live job, or a `doing` claim held by a connection) that gains globs is checked like a start against the other running cards inside that lock, whoever edits it, and is refused with `path_overlap` (or `path_contract_too_large`), so two running cards cannot be edited into an overlap; a card that does not run is checked when it starts. A workspace without a runner database has no jobs, so a contract change there skips the lock and does not create the database. **What is checked:** every runner start of a card refuses with `path_overlap` (409) while another card of the board that holds a start declares a glob that can match the same path: `orch_card_start`, owner `card_start` on a configured board, the trigger sweep, and `card_start` on a board without an orchestrator config (checked inside the runner write transaction before the job is inserted). A card holds a start while it has a runner job that is queued, leased, starting, uncertain or started, whatever its own status and **whatever the age of a started job** (the cap still counts a started job for one hour only), or while it is `doing` with a connection assignee. The jobs, the doing cards and every contract are read inside the lock, and a running card's paths are read as they are now. The refusal names the running card and both globs. **Known gaps:** (1) `started` is the last state of a job (there is no completed or stopped state, `cancel` refuses a started job and `resolve` only takes an uncertain one; confirmed stop of a launched worker, #384, is not in), so a crashed or finished worker's card keeps blocking overlapping contracts until the owner clears its `paths`; no action ends the job itself, and the rule stays fail closed by decision. (2) Only runner starts and edits that add globs to a running card are checked: a card a connection takes by hand (`task_take_next`, or `task_update` to `doing`) is not, so two connections can hold overlapping cards. Two behaviours follow, both pinned by tests: the holder of a manual `doing` claim (no runner job) may clear or narrow its own contract, since the owner-only rule applies only while a runner job exists (globs it adds are checked against the other running cards); and a `doing` claim held by a person (`user:` assignee) does not hold a start at all, because only a connection's claim counts as running. The refusal for a claim says it is claimed by its holder, not that a job exists. A single `task_update` that both adds globs and claims the card (`status` to `doing`) is checked as a card that is not yet running, so it skips the add re-check; the end state is the same as two calls in sequence. Covering manual claims with the owner rule and the overlap check, at the `doing` transition, is a later slice. (3) A claim takes the runner write lock from inside its board transaction (board, then runner; pre-existing), while a paths edit takes the runner lock first (runner, then board), now including the owner's edits and the add re-check. The two can wait on each other until the 10 s busy timeouts end the wait: the claim is refused as busy or the edit fails with a locked-database error. Starts only read the board, so they are not part of it. (4) A paths edit alone does not create the runner database; one that also claims the card on an orchestrated workspace can, because the claim does. The no-database decision is made before the board transaction, so a first-ever runner database created within that window can make an add re-check miss that job. (5) The SQL read of the running cards' contracts happens before the budget and is not billed: 0.17 s at 220 000 rows with contracts and 0.04 s without, measured. Matching is conservative: names compare case-insensitively, `**` matches any depth, a glob that ends where the other goes deeper owns that directory, and a stored value that is not canonical (written outside `task_update`) is read defensively: only NULL is no contract; an unreadable value (an empty string included), an unbalanced group, an over-cap expansion or a `..` that cannot collapse count as `**`, and the card shows such a value as `**`. A wildcard segment is matched by a matcher that means what `fnmatch` means (classes included) but never backtracks the way a regex does: at most one pass over the pattern per character of the name, plus one (one pass when the pattern has no `*`), so its cost is bounded before it runs. One start spends a budget of 2 200 000 work units, calibrated on the development host at about 0.3 microseconds per unit. It bills each running card 16 units for handling it (parse, prepare, loop) and its stored size before that card's contract is parsed or expanded (1 unit, plus 10 per glob, 1 per `/` and 1 per 32 characters); building each wildcard segment's matcher by what it costs (10 units, plus 1 per character and 8 per `[`), a rebuild after the shared cache was cleared included; and each comparison as pairs times the shallower side's deepest glob times the dearest segment comparison (equal literals 1 unit plus 1 per 32 characters; a pattern with a `*` against a name of n characters 1 + (n + 2) x w / 6 units and one without 1 + (w + 1) / 6, where w is 1 plus the pattern's length plus 8 per `[`). Each glob's sizes and wildcard flags are computed once. Measured on that host over 36 shapes (three runs each, 1-minute load about 10 on 14 cores), a start took 0.00 to 0.64 s to run into the budget or through its running cards; the slowest were a `?` segment of ~180 characters against literals and 366 running cards of 64 globs of 89 segments (0.55 to 0.64 s). The per-shape table is in docs/orchestrator.md. The measured set is not exhaustive: a shape that costs more per unit than these, or a slower host, takes longer. Past the budget it refuses with `path_contract_too_large` instead of holding the lock. A card with no contract, or running next to cards with none, is never refused, so existing boards behave as before. Triggers record the refusal as a final miss like any other code. Not yet: role-level contracts, queueing or `blocked_by`, gate flagging of out-of-contract edits, checking manual claims, in-app editing, contracts across boards, recording the paths on the job at start, releasing contracts when a job ends (depends on #384).

- Owner/local void permanently retires a runner-start key while preserving its request, link, state, evidence and history.

- Only queued/leased jobs are cancelled, in the void transaction; starting/uncertain/started and finished jobs are untouched.

- A new key goes through ordinary admission, including non-person rules for owner orchestrator calls.

- Repeat void preserves the first actor/time and writes nothing; one journal event may produce repeated successful-call feed notifications.

- Checkpoint jobs retain linked statuses.

- Void cannot stop or prove completion of a launched thread.

- Active jobs block concurrent starts, but the 24-hour non-person rule can later allow a new job on a todo, unassigned card while an earlier thread still runs.

- Caps count cards, not threads: started jobs age out after one hour unless a doing card with a connection assignee still counts; opt-in orchestration retains the todo/doing recent-start hold and ordinary connection slots.

- Uncertainty needs separate owner truth checks and resolution; empty thread search proves nothing.

- Void does not launch or replay triggers, but a pending unswept todo event can start the card on a later sweep after cancellation frees it.

- Trigger keys (`trig:<event>`) and owner `card_start` are independent of step keys; the latter also bypasses the non-person 24-hour rule.

- These limits predate void semantics.

### Rules initialization deadline

- The read deadline lives in the stdlib-only `mempalace/deadline.py`; `commonplace.rules.checks` wraps it with its own clock, and the fact readers import the leaf instead of rules. Behaviour is unchanged. The reason is test selection: a function-level import of rules from the knowledge graph made nearly every change reach most test files, which pushed quick per-PR checks to the full suite.

- Owner summary-edit checks use a fixed injection clock and a forced-expiry regression, retaining verbatim injection and acceptance assertions without changing the production deadline.

- Rules-injection functional tests run on a frozen deadline clock and a slow-host regression re-runs them with a clock that exhausts the budget on every read; the production 80 ms budget and its fail-open empty text are unchanged.
- Onboarding tests that assert startup content opt into the same frozen clock and run the installed startup hook with only its 0.4 s hub allowance lifted; a slow-hub regression re-runs them with every gateway POST slower than that allowance. The production budgets and fail-open notices are unchanged.
- Shared-proposal acceptance is checked with a fixed injection clock; scheduler-delay regression coverage retains exact rule text and spacing for both agents without changing the production fail-open deadline.

- Rules initialization deliberately returns the legacy response when its deadline expires, even after rendering; the 500-rule incomplete marker is checked with a fixed clock, separately from the unchanged informational 25 ms added p95 budget.

### Rules for reviewers and at session start

- `commonplace review-rules` hands a reviewer the effective rules of the token it is run with, and `--verify` checks that a verdict repeats the current rule version. That line shows which rule set the reviewer was given; it does not show that the reviewer applied each rule. Review tools outside this repository have to call both steps themselves.

- The session-start hook adds a notice when its context does not open with a complete rule set. It recognises the server's own first and last rule lines; a context that only quotes those lines as data still gets the notice.

### Operator-blind token slots: primitives with production locked

- B3 is opt-in via the proposed `encryption.key_mode=token-v2` marker. This slice rejects that marker (and unknown key modes) with an encryption error before operator-held B2 keys or content are opened, including backup snapshots. Existing workspaces without the marker retain their existing behavior; no workspace is automatically migrated or modified.

- The isolated token-slot primitive uses HKDF-SHA256 over raw random token bytes and a fresh random salt, then AES-256-GCM with a fresh nonce to wrap a caller's 256-bit workspace DEK. Its authenticated context binds format version, workspace, epoch, authority, token/grant kind, token digest and slot ID. Strict records reject unknown fields, mixed versions and server/recovery wraps. No raw token, DEK or operator wrapping key is persisted by this module. User-only recovery will use a separate envelope in a later slice. Synthetic tests exercise two tokens and two workspaces and exact payload bytes.

- This primitive does not authenticate current control state or establish runtime authority. A caller must authenticate current workspace/epoch/grant state before unwrapping; digest-only metadata cannot unlock a slot. A scoped bearer exposes the whole workspace DEK, so wing scope remains API policy. Active code/TLS/RAM control can capture secrets; sizes, timing and routing remain visible. Slot-only restore must not restore revoked authority; full control-state rollback, deletion and client compromise are outside this threat model.

- Production enrollment, OAuth schema, capabilities, worker leases, job/cache closure and refresh serialization belong to the runtime slice. Fresh-DEK cutover, user-only recovery, exact-data comparison, crash/restore proofs and activation belong to the final slice. Production remains locked pending those slices, complete encrypted-content inventory acceptance and owner decisions. No cutover, pointer publication, archive deletion or B2 wrap reuse is implemented.

- Raw-key `cipher_connect` is a low-level SQLCipher utility used by B2 migration, not a B3 authority boundary. Future B3 callers must use `connect` or a dedicated guarded entry point that authenticates current control state before content opens.

- B2 keyslots have a lifecycle beside the migration: `workspace keyslot-check` proves a credential against its slot and the workspace databases without writing, and `workspace keyslot-rewrap` wraps the unchanged data key for one slot under a new credential (offline, owner-authorized, atomic; the recovery slot can re-create a lost server slot). Re-wrapping is not revocation because the data key does not change; data-key rotation, the recovery ceremony and online backup of encrypted workspaces are open owner decisions in `docs/workspace-encryption.md`. Both commands refuse B3-marked workspaces through the existing v1 guard. Recovery runbook: `docs/recovery/encrypted-workspace.md`.

- Thread-store storage budgets convert SQLCipher's text `page_size` (and `page_count`) to integers, so budgeted writes in encrypted workspaces are checked rather than failing with a type error.


### Compact rules index

- `session_start rules=index` and `rules_get effective=true format=index` list each effective rule as key, section, scope, size of the full text and a short quoted title (the owner summary, else the first sentence), so a large rule set costs a few kilobytes instead of the whole text.
- `rules_get keys=[...]` returns the selected rules in full, one key-labelled line each, and names unknown keys. Both are partial reads: they never record the set as served and give no `rules_seq`, so a checked connection must still read the full set before a write. In warn or enforce mode the index says so.
- The default of both tools is unchanged, and replies stay within the existing reply size bound.

### Chroma session rebuilds

- If a session's cached palace database disappears, invalidate that palace's shared Chroma System before constructing another client.

- Closing only the session owner leaves a System retained by dropped clients; its SQLite pool still targets the renamed database and may fail when it opens a connection.

- The reset uses the existing palace-scoped lifecycle path.

- The collection configuration repair remains unchanged and still adds missing `_type` markers.

### Email session cookie domain migration

- When a shared session cookie domain is configured, successful email verification first expires the same-named host-only cookie at Path=/, then sets the new domain cookie.

- The redirect destination and session security attributes stay the same.

- This prevents an older host-only session from taking precedence in the browser's Cookie header.

- Invalid or used email links still return a visible error without changing cookies; requesting a link returns the same confirmation for every account.

### Release email login readiness

- The disposable release journey considers email login ready only after authenticated identity checks and the signed-in Board region appears; step failure reports retain exception messages with shared credential redaction.

### Restricted-origin release

- Cards created by subagents or restricted agents start inactive in backlog, including create-and-claim requests.

- Activation and claiming require release by the board's live coordinator, or by the signed-in workspace owner when no coordinator is configured or live.

- `task_approve` supports both pending proposals and unreleased restricted submissions, with an audit entry and an `approved_by` release marker.

- Nonproposal releases leave `proposal_status` unset.

- Proposal approval by a parent alone does not activate the card without that release.

- An inactive card's holder may update only its status line without release; this does not activate, claim or approve the card.

- Rejected-proposal handoffs retain the proposal-approval guard before origin authorization.

### Owner manual assignment starts

- **Manual assignments.** An owner assignment to an active agent connection claims the card immediately, including project admission and the busy-agent guard. With orchestration enabled, an assignment to the configured machine's worker requests a fresh start through the owner orchestrator path. A disabled switch, missing runner config, a different worker or a refused start leaves an explicit reason in the card's status line. Assignment never bypasses origin release. The worker can confirm its existing claim with an explicit take without a second claim event or lease renewal.

### Privacy scanner timing checks

- Large-input and delimiter-heavy near-miss correctness checks run in the behaviour suite without measuring time.

- Each input also retains a separate `timing`-marked check of the 20 ms average scan budget over 20 iterations.

- The duration assertion is outside loops so a slow host produces an informational timing failure while incorrect flags remain blocking.

### Rules initialization timing checks

- Rules initialization tests check remaining SQLite wait budgets with a fake clock; wall-clock budgets stay in separate timing-marked tests.

### Orchestrator no-progress wake

- The hub measures progress, not liveness: a runner-configured board with actionable open work and no card done for N minutes (default 20, per board, 0 = off) emits one `board.stalled` wake through the existing event and webhook path, with backoff and owner escalation after three unanswered nudges. Boards whose open cards all wait on a blocker or a person are never nudged.

### Orchestrator trigger sweep never wedges

- A trigger start that cannot be planned is a coded miss that advances the board's cursor; only infrastructure errors leave the cursor. A split start falls back to the `[runner]` role when the config has no `main` role, and an origin release fires the trigger.

- Repeated sweep failures are visible: the second consecutive failure of a board alerts the owner once (`orch.trigger.failing`, `needs_me.orchestrator_failing`) until a sweep succeeds.

- A todo start refused for a transient reason (`at_cap`, `project_limit`, `busy`, `settings_unavailable`, `config_changed`, `worker_unavailable`, `started_recently`) is retried by the sweep with bounded backoff (1, 5, 15, 30, 60 minutes, then `retry_exhausted`); every refusal stays a miss and the attempts are kept in `orch_start_retries`. A permanent refusal is never retried.

### Board store opens take no write lock (#428)

- `BoardStore(...)` migrates a file once per process and then only reads to decide that it is current, so the trigger sweep, the stall check and gateway reads never queue for the boards write lock. All boards connections use a 10 s busy timeout; a short one is an explicit caller choice (deploy migration). SQLite failures log their error name and code, plus the message only when it is a fixed SQLite text.

### Card context in a start

- A runner lease carries `card_context`: the started card as the machine's worker connection may read it. The runner appends it to the launch message, so an agent starts from the card itself and not only from its number.
- Fields, title and body are always present and verbatim. Asks, decisions and handoff notes, comments, blockers and related cards, linked evidence and linked memory text follow in that order while the block stays inside `COMMONPLACE_START_CONTEXT_TOKENS` (default 6000; 0 sends none).
- Nothing is shortened or summarised: an item is kept or cut whole, and a cut section ends with a marker that counts the cut items and names the call that returns them. User text is JSON-quoted.
- A card that cannot be read at lease time never fails the lease. `commonplace/runner/start_context.py` holds the assembly in one function with an ordered section list for later additions. A started card's own goal is never cut, like its title; the goals above it are a section after decisions and are cut from the top level down.

### Runner wings and the owner runner block

- A runner machine's worker PAT reads and writes every wing in the machine grant and no other; its first wing is the default write wing. `startable()` accepts a card whose board wing is granted and otherwise refuses with `outside_worker_wing`, naming the board wing and the granted wings. Machines enrolled before this keep a first-wing-only worker policy until their wings are saved once (same wings do not reset the binding).
- The Admin page's runner block lists, enrolls, attests and edits wings through the existing owner-only `/api/runners` routes. Enrollment tokens are shown once in a panel and never stored, logged or redrawn.
- The runner block reads its wing picker from `GET /api/runners/wings`, never from the connections listing. For an owner who reads every wing, the wing names come from the palace's Chroma metadata index in one seek per wing, skipping wings that hold only miner bookkeeping rows, under a two-second deadline; other principals, compact stores and a failed index read use the memory listing. Grant validation, connection read wings and `GET /api/connections` share that read (#451: the drawer-by-drawer listing did not answer on a 2.6 million drawer palace). When the wing read still fails, the editor keeps the granted wings ticked and offers a typed wing field; the server validates every name.

### Workspace invites

- An owner invites a person with a single-use link that expires (7 days, or 1 to 30) and can be revoked. The secret is shown once and stored only as a SHA-256. Joining needs a signed-in account and one confirming click, and grants the existing `member` role with no connection, token or scope. `docs/invites.md` is the contract.
- Invites change no access rule. A member still reads no private wing, their own notes included; who may read a private wing in a shared workspace stays an open product decision.
- A person in several workspaces switches with `/api/workspaces/switch`, which only ever accepts one of their own memberships.

### Run ledger

- A run is one runner start. Its ledger row (`run_ledger` in `runner.sqlite3`) is written in the start's own transaction and follows its state; the table is additive and older starts get a row when the store opens.

- The hub never measures, prices or estimates usage. Tokens, cost (millionths of a currency unit plus the currency), the log pointer and the run's end are only what the run's own enabled machine reported through `POST /api/runner/usage`; an unreported value stays NULL and reads "not reported".

- A reported end changes neither the start job nor machine capacity nor the card (D3 stays open).

### Raw test runs on a shared test host

- A host that caps concurrent test runs (its test runner holds a slot per run) opts in with `~/.config/commonplace/require-test-runner`, naming the variable the runner sets; there `tests/conftest.py` stops a pytest run without it, so agents cannot overload the host by bypassing the cap. Machines without the marker run unchanged; `COMMONPLACE_ALLOW_RAW_PYTEST=1` is the one-run override.

### Mirror and search-index test waits

- Tests that wait for background mirror or search-index work drain on the actual completion condition (no sweep active, no mirror job pending) through one shared test helper, with a 120 s stuck-job limit, rather than one fixed `wait_for_mirrors` budget that returns silently when it runs out.

- A slow-host regression re-runs the converted selections with late-starting jobs and a wait that returns at once; the production sweep, its 20-thread and unit bounds and `wait_for_mirrors` are unchanged.

### Console activity query checks

- Console activity schema and query checks freeze the store clock, keeping both dated rows within the seven-day retention window while asserting ordering, latest timestamps and redaction.

### Agent questions

Agent `ask` calls and `task_update status=review` questions target the board's
appointed coordinator by default when that connection is live, unrestricted,
can read and write the board wing, and can use `space_read`, `space_post`,
`needs_me` and `decide`. They appear in the coordinator's queue instead of the
owner's. Supply a nonempty `person_reason` (at most 500 characters) to explain
why a person is required; the reason is preserved in context and opening history.
Without an authorized coordinator the question reaches the owner. Completion
reviews, person callers and explicit person handoffs keep their owner/person route.
A status line never hides a person decision or an item needing person release.

Only the addressed, still-authorized coordinator or a signed-in workspace owner
can answer, plus an owner's own connection holding the owner-granted
`cards:decide` scope for asks addressed to that owner; agents never answer their
own asks. The coordinator may instead use
`decide(then="escalate", reply=<reason>)` to send the unchanged question, options,
context and return recipient to the owner; an empty reason is refused. An appointed
coordinator may withdraw only an agent-directed decision it requested itself,
using `task_update status=todo|doing` with optional `reason`. Current appointment,
recipient and requester are rechecked. Other agents, member people and coordinators
of other boards have no withdrawal authority. Withdrawal keeps the original ask
in append-only history and does not imply approval. Person decisions cannot be
withdrawn by agents. Existing asks are never rewritten by this routing change.

Subagents still need an existing approved and released card before asking. If a
routed coordinator becomes unavailable or is replaced, the pending ask appears
in the owner's queue without changing its stored words or audit log.

The shipped agent skill teaches the same routing and withdrawal boundaries. The
pinned MCP contract adds only optional `person_reason` to `ask` and `task_update`,
with matching descriptions for `ask`, `task_update` and `decide`; tool names and
visibility for slim, standard and full profiles remain unchanged.

### Completion reviewer

A card may name one reviewer of its completion, a current workspace member
(`user:<id>`). Only a signed-in workspace owner chooses, changes or clears it
(`reviewer` on `task_add` and `task_update`, and on the card REST routes); agents
and members are refused. Without a reviewer nothing changes: the owner is asked
and only an owner answers. With a reviewer, a move to Done by anyone but that
member (an agent, the assignee or the owner) opens a completion review addressed
to them even on a board that does not gate completions, and `decide` answers it
only for that member. An assignee therefore reviews their own work only when the
owner explicitly chose them. A reviewer who has left the workspace counts as no
reviewer. Changing the reviewer re-addresses an open completion review, and the
chosen reviewer sees it in `needs_me`. The pinned MCP contract adds only the
optional `reviewer` argument to `task_add` and `task_update`.


### Project seat wake recommendations

- Project Connect writes every HTTP 409 conflict to its persistent status. Shared-memory
  and replacement conflicts retain their explanation and next step after dialog dismissal;
  shared-memory consent and own-section conversion still require the existing explicit choices.
  Other conflicts include the server's reason in the Orchestrator section of project settings.

- Project settings render the orchestrator status and bind Connect as soon as the
  project view arrives, before chat settings, connections, cards or chat status
  settle. Chat and agent sections show loading and their own unavailable states;
  their initial reads have ten-second client deadlines.

- `GET /api/connections` spends at most one second waiting for its optional memory
  wing listing, including worker transport. Timeout, `ToolError` or an already
  busy lookup returns the existing connections and `wings: []`. One daemon lookup
  per gateway bounds outstanding work; a timed-out call may finish later, without
  caching its result or delaying later requests. Until it finishes, other requests
  use the same empty-wings fallback, including requests from other workspaces.

- Connect recommends a board-scoped webhook for `space_message` and `card.moved`, so messages and worker completion moves can wake the orchestrator.

- Setup is advisory: Connect and reconnect neither register nor modify subscriptions.

- Existing users must explicitly update their webhooks to receive card moves.

- Message coordinator filtering and reply-loop suppression still apply.

- With orchestration enabled, the list appends `card.created` and `project.report`.

### Card-lifecycle rule replay

- `tests/test_statechart_card_rules.py` keeps one proof per rule of the card-lifecycle statechart replay: release of restricted and subagent cards by the live orchestrator, subagent questions to the orchestrator, approvals offered to the owner only from Needs me, claimed manual assignments, starts by an owner-trusted seat, the machine limit past an hour, and completion reaching the recommended seat webhook. It changes no behavior; a seat without the owner's trust confirmation still cannot request starts.

### Restricted-origin card content

- Trusted agents and authorized board coordinators may update only the title and body of an unassigned todo card marked as restricted-origin.

- Board write access and proposal approval remain required.

- Each edit records its actor in the existing audit log; it neither releases the origin nor changes claiming, activation, reservation or other update permissions.

### Token-slot refusal verification

- Marked-workspace connection, snapshot and database classification regressions use real synthetic B2 keyslots and exact-data controls before setting the marker. Direct provider tests likewise prove valid unmarked wraps and workspace-key reads. Malformed direct metadata raises an encryption error, and marked-workspace errors retain the locked diagnostic rather than implying settings corruption.


### Browser GitHub connection (experimental, default off)

- One GitHub worker owns a control-database lease shared by every serve on the same volume, including containers with private temporary directories or different mount paths. Claim and recovery occur in one short write transaction; no lifetime lock file is held in the data tree. The lease lasts 30 seconds and renews every 5 seconds. A second serve fails closed for GitHub writes and retries acquisition every 5 seconds after the owner closes or its lease expires. Restored backups may retain the lease until expiry. The Node lifecycle harness exercises connected rendering, stale reads, card switches and logout without a browser installation.

- Browser consent requires a person session, JSON, exact Origin and session-derived CSRF; agents and MCP receive no GitHub capability. Connecting asks for an exactly empty scope; inherited sign-in scopes require confirmation, every repeated scope header is cross-checked, and refresh material is discarded before encryption. A connection alone never carries merge rights.

- Expired grants purge locally, increment generation and permit a new Connect without acknowledgement or a GitHub request. The expired view links to GitHub app access. Only ambiguous revocation requires acknowledgement.

- The ten-minute flow binds PKCE, browser, session, workspace, visible card and generation. Callbacks reopen the selected Needs item. Login and grants share subject ownership without provisioning a new login identity during connect.

- One person owns at most one grant. Another workspace reports the existing workspace explicitly and offers Disconnect; it does not expose Open PR or reuse that grant for work. The same person's browser consent may revoke the grant from either workspace.

- `COMMONPLACE_GITHUB_CONNECT` defaults off and prevents new start/callback/confirm work. Existing grants stay visible and browser-authenticated DELETE stays available while disabled; missing app credentials make revocation unconfirmed and purge locally.

- Confirmation expiry cleanup runs on the person's next Connect, Confirm or Disconnect write. GET status remains local-only and never revokes remotely. Failed cleanup marks revocation unconfirmed and settles exchange counters in finally blocks.

- Serve closes its control database on normal return and signal unwind, stopping registered workers and releasing only its own GitHub lease in a short transaction. HTTP server close also stops email admission before GitHub joins its heartbeat and releases its lease; database callbacks use the same order so GitHub cannot delay the email stop signal while waiting for SQLite. Repeated GitHub closes are serialized and perform no SQL after successful cleanup, including after SQLite closes. Never-owners retry claim and recovery; an expired former owner stays fenced because remote work may still be in flight.

- Generation, remote-work and exchange counters fence disconnect, confirmation and late callbacks until all cleanup settles. Lease ownership and expiry are checked in each state-changing transaction, including late remote completions. An expired owner cannot renew itself or alter a successor’s counters. A new owner recovers after lease expiry without replaying remote DELETE; orderly close releases only its own lease. No writable temporary directory or platform-specific file locking is required.

- Disconnect purges local grants and confirmations even if stored ciphertext is tampered or the wrapping key is unavailable. It retains the reconnect acknowledgement requirement for unconfirmed remote revocation and never retries unavailable credentials.

- AES-256-GCM encrypts tokens/verifiers before SQLite/WAL using per-user DEKs and user/provider/schema AAD. `COMMONPLACE_GITHUB_KEY_FILE` is a private, owner-held 32-byte key; code rejects it within the control database's parent directory. Operators must also exclude it from separately located workspace and backup trees, which this store does not inventory. Install the `commonplace` extra for cryptography.

- Back up the wrapping key separately. Rewrap DEKs offline with unchanged AAD/version while workers are stopped; replacing only the key file is unsupported. Retain the matching old key/database backup until a migrated copy is verified. Online rotation and migration tooling remain deferred.

- Needs disables loading controls, contains GitHub-only errors without hiding card actions, drops stale reads and clears rendered connection details on teardown. Ordinary decision details retain their existing teardown contract. PR links require HTTPS, exact github.com, a default port and a positive pull-request number; reports stay unverified and a connection alone proves no merge rights.

- Full account-deletion integration, composite multi-worker HTTP acceptance and a live browser workspace-switch flow remain deferred. The GitHub deletion helper fences its own cleanup only, not other stores or the user record.

- Absent token expiry requires dated `COMMONPLACE_GITHUB_NONEXPIRING_EVIDENCE` in `YYYY-MM-DD|OAuth App|client-id|expiration disabled` form. Before enabling, record fresh-account/prior-login scope, header and expiry outcomes plus ingress/callback/backend no-secret logging canaries. Live GitHub OAuth behavior remains unproven. Refresh and reconciliation remain deferred.

- **Connect GitHub with repo access (separate, explicit per-person opt-in).** Owner decision (11 Nov 2026, option B): merging uses GitHub's full `repo` scope, granted per person and never server-wide. A connected person may add this second consent (`POST /api/github/merge-consent`, offered as the button "Connect GitHub with repo access to merge"), which runs one more PKCE flow (`purpose=merge`) requesting exactly `repo`. OAuth Apps cannot narrow it: it reads and writes every repository, private and organisation repositories included, that the person's GitHub account can reach, and the Needs copy says so and that it is optional. The callback accepts `repo` only in a merge flow, requires it, rejects any other extra scope (revoking the issued token) and requires the same GitHub subject as the existing grant. The new token waits in the usual encrypted confirmation; only after the person accepts does it replace the stored token (stored with its scopes) and the old token is revoked. Declining or any failure revokes the new token and leaves the empty-scope connection. A revoke GitHub refuses (any answer but 204) is not retried: revocation is flagged unconfirmed, the record is dropped, and the person is asked to review GitHub app access before connecting again; only a revoke that raises keeps its record for a retry. An unanswered confirmation is not forgotten: the worker heartbeat revokes one lapsed candidate per tick outside the database lock (an unreadable record is purged and marks revocation unconfirmed), and startup recovery keeps each pending record, lapsed, so the next sweep revokes it instead of dropping the only handle. The encrypted record outlives every revoke it guards: a connect accept deletes it in the swap's own transaction and a merge accept turns it into a settlement record (below), while a decline, lapse, failed commit or Disconnect deletes it only after the revoke has an outcome, so a crash or lease loss in between leaves it for the next sweep instead of an unrecorded token. The callback records a candidate and releases its own in-flight exchange in one transaction, so no moment exists where a record is visible while its exchange still counts as in flight with no owner left to revoke it (a settled record without an owner is the sweep's). The issued token counts as owned only once that transaction has committed: a failed COMMIT leaves it to the callback's cleanup, which revokes it or flags it. A token-exchange answer that is lost or unreadable flags revocation unconfirmed, because GitHub may have issued a token that cannot be revoked without it. Disconnect revokes a pending candidate before the grant and marks the record it takes as in flight for the length of that revoke, releasing the marker in its closing transaction, which deletes only that record. Any failure before that transaction commits runs it again as a release, and the original error is still returned: if every revoke had returned, the release is the closing transaction itself (the record goes and nothing is revoked twice); if a revoke raised, the record is lapsed so the next sweep revokes its candidate (a second DELETE when the candidate's own revoke had succeeded and the grant's raised, which then reads as unconfirmed), and the grant is purged unrevoked with revocation flagged unconfirmed, as a restart does, without replaying anything at GitHub. Either way nothing of that Disconnect stays pending, and an account deletion keeps the person's keys while a candidate record remains so the sweep can still decrypt and revoke it. The grant-first order is avoided on the assumption, not verified against live GitHub, that a grant delete also removes the app's tokens and would leave the candidate's own revoke answering 404, read as unconfirmed. A record whose exchange is in flight (`active_exchanges` set) belongs to that owner, who revokes it exactly once, and an exchange releases only a marker its own transaction committed: the sweep, expiry, a decline or second consumer and Disconnect all leave it alone, so each candidate is revoked at most once and the sweep cannot be starved by one busy record. An accepted swap turns the pending record into a lapsed settlement record holding the replaced empty-scope token in the same transaction and drops it only after that token is revoked. A restarted process flags revocation unconfirmed for exchanges that have no record (a token issued but not yet recorded), for users with a pending connect flow, and for users whose Disconnect was pending, had remote work or a disabled grant; a pending record alone does not flag, it is retried by the sweep, and a failed revoke still flags; a candidate equal to the live grant is neither revoked nor flagged. Acceptance counts only after the swap's transaction commits; a failed commit revokes the candidate and keeps the stored token. Remaining windows: a process death or lease loss after GitHub issues a token but before its confirmation record commits leaves a token GitHub still lists under app access, neither revoked nor recorded (a restart flags it; a fenced worker cannot write until then); a revoke retried after a crash, a lease loss or an ordinary failure inside the revoke call that GitHub had already honoured reads as unconfirmed, because GitHub is assumed, not verified against live GitHub, to answer an already revoked token with a non-204 (the cost accepted instead of an orphaned token); an in-flight owner's candidate revoke overtaken by a Disconnect's grant delete likewise gets that answer and flags, which is not closed because a revoke that failed after the grant delete must still flag a token issued later. A Disconnect whose grant revoke never ran because an earlier revoke raised leaves that grant token live at GitHub, flagged unconfirmed; a restart likewise deletes a disabled grant without revoking it. A release transaction that itself fails (for example the database stays locked) is retried by the worker heartbeat, before its sweep, every tick until it commits; until then the sweep skips that record and the person's connect, Disconnect and decline answer `github_pending`, which a release that keeps failing (a bug, not a transient lock) holds until a restart. The heartbeat survives a failed tick (only the error type is logged) and stops only on lease loss, so a record whose revoke keeps raising an unexpected error is retried every tick and holds up the sweep of other lapsed records until that bug is fixed; a lease loss or a process death leaves its markers to the next claim, at restart. A record left by a failed decline or expiry keeps its expiry: declining again revokes it at once, otherwise the sweep does once it lapses, one record per tick. While an exchange owns a pending record, status can still show its confirmation and accepting is refused with `github_pending` until the owner settles. Connecting without this consent keeps today's empty scope; Disconnect revokes the whole authorization, including merging.

- **Merge** (`POST /api/github/merge`, body `csrf`, `card` and `head`) uses the same browser-session, exact-Origin and CSRF guards; agents, Bearer tokens and MCP cannot reach it, and it is blocked while `COMMONPLACE_GITHUB_CONNECT` is off. The server resolves the PR from the visible card: Needs must classify it as Merge for this person (a fresh passing report with a pinned head and no open question or plan), and its `pr_url` must be a plain github.com pull URL in exactly the raw form the panel accepts for Merge (no case folding, no port). Owner, repository and number are never read from the request; `head` is only the commit the person confirmed and must equal the card's reported head, else `github_head_moved` before any GitHub call. Only the caller's own grant in the session's workspace is read, and it fails closed (`github_not_connected`, `github_merge_consent`) when absent, expired, disabled, pending disconnect, key-unreadable or without `repo` (`github_merge_consent`, shown as "Connect GitHub with repo access to merge."); there is no server-token or other-person fallback. Before any merge, GitHub must show the repository to this account (a 404 answers `github_merge_refused`; private repositories are allowed because the person granted `repo`), the PR open, not a draft, not merged, still at the reported head SHA (`github_head_moved`) and `mergeable_state` `clean` (`github_not_mergeable`). The merge call pins `sha` to that head, so a push after the check is refused by GitHub, and uses the first merge method the repository allows (squash, merge commit, rebase), which the Confirm text states. A 401 from GitHub answers `github_not_connected` (Connect GitHub again). Branch protection, required checks and reviews stay with GitHub; its refusals return as generic codes (`github_merge_refused`, `github_not_mergeable`, `github_merge_failed`), never as GitHub bodies. Each attempt that reaches GitHub writes an audit entry with ids and the outcome code only (`allow`, `deny`, or `error` for an unexpected failure); tokens appear in no response, log or audit row. The Needs panel shows Merge (after a second Confirm merge click naming the PR, short head and merge method rule) only for a consented connection and that raw PR URL form, and drops the merge controls once a merge succeeds, including one that finishes after the person has moved to another card, for the current page session only (a reload offers Merge again for a card that still reports merge-ready, and GitHub then refuses the merged PR); otherwise it shows "Connect GitHub with repo access to merge", and every refusal leaves Open PR with a plain reason. GitHub's own rules, including any administrator bypass, stay with GitHub. Live GitHub behaviour of these calls (protected-branch and administrator-bypass cases, `mergeable_state` timing) is unproven until run against a protected test repository.

### Generic agent skills

- `skills/untangle-codebase` is a repository-independent skill: it names no host, path, person or product of this system, and its helper only reads files. A test keeps its front matter, required sections and absence of local paths checked, and the Claude marketplace copy must match the canonical files byte for byte. It changes no stored data and is never installed or enabled by the system itself.

### Optional agent orchestration

- **Default off.**

  - The workspace owner explicitly enables orchestration; absent settings mean off.

  - A settings read error refuses admission.

  - Off retains the existing claim/start behavior and board JSON shape.

- **One project count.**

  - Claims, queued starts, leases and configured starts share a runner transaction.

  - Ordinary agents count by connection, workers by card; card/job overlaps count once.

  - Default cap is 3; project rows override it, configured boards use the smaller config/row cap.

  - Board `used` shows active slots, excluding the queued reservations counted at start creation.

- **Authority.**

  - Unconfigured starts require the active board coordinator PAT or a signed-in owner.

  - Owners separately trust seats and grant machines atomically; switching off demotes all seats and removes only their requester ids.

  - Trusted seats cannot create boards.

  - Connect alone never grants this trust.

  - A trusted project seat may hold the orchestrator lease of its own board and seat role while the board has no `[runner]` config; no config or requester edit is needed. Its direct starts are unchanged. Demotion, switch-off or revocation ends that authority at the next lease call.

  - A project seat never claims another board's lease, also when that board's runner machine lists it as a requester.

- **Lock order.**

  - Control snapshots precede waits.

  - Claims hold boards RESERVED then runner admission until board commit; runner admissions read boards SHARED.

- **Residual.**

  - Without a runner or subscribed `card.created` webhook, a new card waits for the orchestrator's next session.

  - An accepted live `[runner]` with `session = true` enables a persistent session through the existing machine runner loop, without requiring a `card.todo` trigger; configs without the key are unchanged and no service is activated automatically.

  - Splitting and inbox behavior are not introduced by this switch.

### Optional project completion and stuck reports

Owner-enabled orchestration adds a durable `project.report` route for transitions
from a non-done state to done and for observed stuck episodes. It is off by
default and carries only board, card number, report id, reason, generation and
captured coordinator. No card words, exception contents or credentials are sent.
Reports grant no start, split, claim or other authority.

Connect the project seat again after enabling, then remove and re-add its webhook
using Connect's returned event list
(`["space_message", "card.moved", "card.created", "project.report"]`).
Enabling alone never edits subscriptions. Setup gaps do not replay old reports.
Raw subscriptions retain their behavior: a legacy completion hook still gets its
raw wake; subscribing to both channels can deliver raw plus report. Cross-channel
deduplication is deferred.

Done capture shares the source transaction. An unavailable settings/coordinator
snapshot rejects the transition with `settings_unavailable` (503), before a lesson
is saved. Ordinary edits remain available. A board created by the same transaction
has no coordinator and stays history only when the snapshot is available; an
unavailable snapshot still rejects a relevant done transition. This is fail-closed,
not a guarantee to undo arbitrary external memory side effects after a lesson.
Existing raw event emission can still fail after the board commit.

Report installation registers recovery without resolving workspace directories.
Recovery installs the report reset trigger only when all watched task columns
exist and removes an existing incomplete trigger otherwise. For sources missing
only `heartbeat_at`, this keeps raw status writes usable until BoardStore adds
that column and installs the trigger. The other watched columns belong to the
base schema; BoardStore does not add them during this migration. Until migration,
no episodes are recorded, while reporting is enabled, a pass that reaches a coordinated task logs an error, and watched writes do
not bump `report_reset_rev`. Product writers open BoardStore before writing.
The first dispatcher scan recovers and migrates existing board databases without creating
workspace directories, followed by monotonic 30-second scan deadlines independent
of requests and webhook traffic. Source initialization is cached by database path,
inode and SQLite schema version. Zero-byte files are deferred without opening a
connection; a read-only probe defers nonempty files without both base tables
(`boards` and `tasks`). Neither case opens a write transaction or runs report
migrations; a later pass picks up the source after its creator initializes it.
Ready sources have their schema version read through the same read-only probe.
Ordinary data writes preserve the cache; a new inode or a changed schema version
triggers initialization again, including repair of dropped report tables.
Failures are isolated per workspace and logged with exception type and stack
locations, excluding exception text and local values. Each workspace pass reads
at most 100 cards, publishes at most 100 source rows separately and cleans at most
100 deleted episode keys. Persisted pagination provides fair continuation; a cycle can
take longer than 30 seconds. The first observation in a generation baselines
existing staleness without sending it. Heartbeat and leave/reclaim revisions
separate later observed episodes, even at equal timestamps across restart.
Episodes beginning and ending entirely between observations are lost. There is
no historical-done replay. A workspace failure leaves other workspaces progressing.

Source access closes before control publication. Event and receipt commit together;
source acknowledgement happens later. Receipts are retained without age pruning
because a publisher may still retry an old source row. Report reconcile saves an
immutable per-event routing envelope atomically with cursor advancement. Unknown
routing facts defer only that report with persisted 1/2/4/8/16/30-second capped
backoff, while later raw events and due HTTP deliveries keep progressing. Enqueue
and retry-work deletion are atomic. Inability to persist control work, cursor or
retry metadata uses the existing infrastructure-error path and can delay dispatch.

Send rechecks the captured coordinator, current connection/wing authority, board
and activation/binding generations across separate control/source/control phases;
HTTP holds no database lock. Positive invalidation cancels only reports. Busy,
locked, wrong-key and unknown failures retry indefinitely without using HTTP
attempts or producing dead-letter failures. Existing delivery inspection shows
`error=report_authorization_retry`, `count` and stable `retry_since`; ALLOW and
cancellation clear them. All unresolved auth rows remain visible beyond the latest
100 ordinary deliveries. This unresolved set is unbounded by design; inspection
reads all rows for that hook. Operators should repair authority/storage or cancel
the affected hook. Exception contents never enter inspection or payloads.

A receiver deduplicates `report_id`, fetches the current card and version, and
reports progress through existing authorized tools. For done, inspect the current
result and continue only work already authorized. For stuck, inspect the current
holder and person-wait state before an authorized follow-up. HTTP is at least
once: duplicate requests must not repeat the receiver's action. Broader receiver
playbooks and the original wider orchestration acceptance remain separate work.


### Orchestrator config as code

- **The store is the source of truth.**

  - Each board has versioned orchestrator configs (`v<n>`, with parent version, channel, source ref and sha256) in the workspace's `runner.sqlite3`.

  - `.commonplace/orchestrator.toml` in a repo is only a proposal; `orch submit` proposes it and the owner approves its sha256.

- **The owner approves every change.**

  - Wing writers propose; only `orch_owner` accepts, rejects, retires an experiment, forces a lease, voids a step or acknowledges a restore.

  - `orch_owner` is a signed-in person with role owner, or the local CLI (data-dir access).

  - Agent tokens with an owner role are refused.

- **The app shows who coordinates.**

  - Project settings > Orchestrator reads the read-only checkpoint for role `main` and says in words who holds the lease, when it runs out and how much work is open, in doubt or unstarted. A signed-in owner can End lease (a confirm first); it calls the same `force-release` as the CLI and MCP, so members, agent tokens and other workspaces are refused. Ending a lease changes no step: unfinished work stays listed and becomes in doubt when the next coordinator claims.

- **A project can show its own panel.**

  - A project panel (`commonplace.panel/1`, `docs/project-panels.md`) is a few text highlight lines and http(s) action links, set with `PUT /api/projects/<slug>/panel` by the owner or an agent with board-write authority and shown at the top of the project's board. It is untrusted: unknown fields, markup, control characters, over-limit sizes (4096 bytes) and non-http(s) links are refused, and the page draws only escaped text. Its rules are adopted as a constructed style sheet, never an inline style element, so the app's content security policy applies them. A project without a panel is unchanged.

- **Channels.**

  - `live` and `exp:<name>`.

  - An experiment may change only `prompt`, `workflow`/`workflows`, `topic`, `model_role` and `skills`.

  - Triggers always use live.

  - Only accepted versions run.

- **What is enforced.**

  - For the runner role: the launch message (`SPAWN_MESSAGE` + prompt + workflow steps), `harness = "t3"`, the topic (grant checks), `model_role` (resolved once at insert and pinned on the job), `runner.machine`/`project`/`runtime_mode` (grant checks), `card.todo` triggers, `limits.max_agents` and planner `connections`.

  - Every other role, `skills` and `mcps` are advisory: rendered for sessions to read.

- **Boards without an accepted live `[runner]` config behave exactly as before.**

  - No new columns change their digest, no sweep scans them, no lease exists for them.

  - The tool list does change: the `orch_*` tools are listed on every workspace, so a config can be proposed before one exists (see Residuals).

### Foreground refresh rendering

- A background workspace refresh that supersedes a pending foreground render inherits that render on the same screen.

- Outside the bots screen every refresh also reads the bots roster, next to its three shared reads, to fill the Bots badge; a failed roster read leaves the badge as it was and never fails the refresh. The refresh harness answers that read and checks the badge total.

- A background refresh leaves an edit, hand-off or ask form in the card panel alone, including a form opened after the refresh already requested the card; `state.card` stays at the version the person started from, and Cancel or Save redraws the panel from a fresh read. On Needs me the item's detail is paused while a hand-off form is open, so a changed queue redraws the list but not the form; Cancel, a saved hand-off, choosing another item or a filter that drops the item resumes it. Known limits: a hand-off form keeps the version it started from, so after a conflict Save keeps failing until the form is cancelled and opened again; on a card route a conflict redraws the panel and drops the typed note.

- Navigation epochs reject stale reads and Settings completions; ordinary events leave an already rendered Settings view intact.

- Settings bounds each of its six section reads and three prerequisite reads to ten seconds, including response decoding. Failures name the section or request and offer Retry; successful reads show saved values. Orchestration settings lock contention returns retryable HTTP 503 after a one-second lock wait.

- A current request failure clears the pending render; a later background refresh retains its usual form-preservation policy.

- On Settings, a background prerequisite failure preserves already-rendered sections and mounted webhook drafts, reporting the named failure through the separate screen error with Retry. Initial and foreground failures may replace content with the error.

### Starts on a configured board

- Every config-driven start runs one fenced transaction (`BEGIN IMMEDIATE` on `runner.sqlite3`): resolve and hash, refuse under the restore flag or for a `plan`-labelled card (`plan_pending`), look up the existing step (or, for a person, the runner's own request key), check the cap, insert.

- A same-step retry returns the stored job; any mismatch is a 409 with the stored step.

- `card_start` on a configured board is person-owner only; every other caller gets `no: use orch_card_start`.

- Cancelling a job that belongs to a step is person-owner only.

- The person owner may send `card_start` the card alone on a configured board: machine, project and topic come from the config, and values that are sent must still equal it. `GET /api/card-starts/options` (person owner only, read-only) tells the card panel what it may offer and why nothing can be started; it creates no job.

- The cap counts distinct cards: queued, leased, starting or uncertain jobs; jobs started within `STARTED_HOLD_SECONDS`; and `doing` cards with any connection assignee.

- The limit is `min(config.max_agents, project_limits.max_agents)` when both are set. Both accept 1–100. Per machine, `max_in_flight` and `max_starts_per_hour` still apply at lease time, and the runner refuses a leased start with the pre-spawn code `low_memory` when its available memory is under `min_free_memory_mb` (the job ends `failed`, shown by `card_start_status`; the guard never blocks when the probe is unreadable).

- A `plan`-labelled card never starts from config until the plan is approved; the plan-approval row fires the trigger once.

- The plan is also re-checked when the runner reports `starting`; a refused job ends `cancelled` with outcome `plan_pending`.

### Bots from a person's own app (`bots:chat`, `bots:confirm`)

- A person's own native or mobile app can use `/api/bots*` as that person through two scopes the person grants themselves. Unlike `cards:decide` (owner-only), any signed-in member grants them, and only for their own top-level PAT or OAuth connection in that workspace; the shared classifier `decide_grant.machinery` refuses child agents, runner connections, orchestrator seats and agent-started connections, and board coordinators are refused by the web layer and again at use.
- At use, `bots.auth.person` re-checks everything on every request (scope, live top-level app owned by that person, not a coordinator, the person still a member) and then returns the person's own `sessions.person_principal`, so every downstream check is the person's and an app reaches only that person's bots. Anything else stays 404; a revoked or expired connection is 401.
- `bots:confirm` is separate and useless without `bots:chat`; `/confirm` through an app without it is 403 `scope_required` and runs nothing. Buttons on a question remain the existing `cards:decide` route.
- `bots:learned` is a third, read-only scope with the same rules (needs chat, granted and withdrawn separately): it lists what the person's bots learned and can change nothing.
- OAuth apps get either scope only through a ticked consent; PAT grants and withdrawals go through `PATCH /api/connections/<id>` (`bots_chat`, `bots_confirm`) or Settings > Agents, and a member may use that PATCH for their own connection only. The command line and session policies never carry them. Becoming a coordinator or expiring clears the grants.
- Every grant and withdrawal is audited per scope, and every write made through an app adds one `bots.write` row with the connection id, route and the status actually sent as `ok`, `refused` or `error` (never text; a refused confirm and a request that dies before answering are recorded too); person-session writes add none.

### Owner-granted answers from the owner's own apps (`cards:decide`)

- Answering a Needs-me ask stays a signed-in owner action by default. A separate,
  unticked `cards:decide` consent lets one of the owner's own connections answer
  asks addressed to that owner, over MCP `decide` or REST
  `POST /api/cards/<n>/decide`.
- Granting: the owner's browser session only. PATs get it at creation
  (`"decide": true`) or through `PATCH /api/connections/<id>`; OAuth clients get it
  only when the owner ticks the consent box, and the request must also carry
  `memory:read` and `memory:write`. Members see an owner-only notice and a forged
  tick grants nothing. Authorization-server metadata advertises the scope; OAuth
  defaults never include it. The control layer takes the signed-in owner's
  principal, never a bare user id: `create_token` needs it as `grantor` for its
  own top-level, non-seat connection; `set_connection_decide` needs it as actor
  and refuses runner (its runner message), inactive, child, agent-started and
  seat connections with specific 409 messages; OAuth code minting rechecks a
  person owner. The CLI refuses it; person policies, profile defaults and seat or
  runner policies never include it. The grant route also refuses any current
  board coordinator (checked in the board store). Settings → Agents offers the
  grant only on the viewing owner's own eligible, unexpired, non-coordinator
  PATs, with the consent text served from one source. Withdrawing works on any
  connection, also expired or revoked ones; another member's row gets its own
  message.
- Answering: the connection must be a live PAT or OAuth connection of a current
  owner, top-level, not a runner, hold the scope, and the ask's recipient must be
  exactly that owner. Project seats and board coordinators are refused. The app
  needs `memory:write` and read access to the board's wing; write access is the
  owner's **person-level** access (`_owner_writes`: the owner's current person
  policy can write the board's wing), so it answers wherever the owner could in
  the browser, regardless of the app's own `inbox` write wing, and is refused
  where the owner cannot write. It cannot escalate or answer its own ask. An untrusted PAT with the scope sees `decide`; without it,
  tool lists stay unchanged.
- Audit: inside the answer's board transaction, before it commits, every
  delegated answer writes `audit_log` action `card.decide.delegated` with user,
  connection id, auth kind, board and card number. A failed audit write fails the
  answer. The audit log is append-only and a separate database, so if the board
  transaction rolls back after the row was written, a `card.decide.rolled_back`
  row with `reason=audit_seq=<seq>` cancels it. Window: an audit row without an answer is possible after a kill between the two commits, or when the cancel write fails. No answer commits without its audit row; no
  reconcile exists yet. Grants and withdrawals write
  `connection.decide.grant` and `connection.decide.revoke`.
- Revocation: `PATCH {"decide": false}` (PAT or OAuth) or revoking the connection
  takes effect on the next request; policies load on every request and lose the
  scope when the holder stops being owner. Losing eligibility removes the stored
  grant, each with a `connection.decide.revoke` audit row, so regaining it needs
  fresh consent:
  - demotion: a membership trigger fires on an `UPDATE` of `memberships.role`
    from `owner` to anything else (reason "ownership lost"); deleting a
    membership is refused by the foreign key while the user has connections;
  - coordinator assignment: `board_set` clears the grant of the connection it
    assigns (reason "coordinator") inside its transaction and again after the
    commit; the grant route re-reads coordinators after writing a grant and
    withdraws it (409) if the app became one meanwhile. Unassigning does not
    restore it, because every path that removes or replaces a coordinator
    (`board_set` replace or unassign, and the auto-unbind when the owner runs the
    own-wing action and the app can no longer coordinate the board, whether or not
    the wing changes) clears that connection's grant before the board commit. The
    clear is its own control-database commit, ordered before the board COMMIT, not
    one atomic transaction with it: a grant left by a failed clear or a crash is
    gone before the app stops coordinating; a failed clear rolls the removal back;
    a crash after the clear leaves the coordinator in place without a grant (fail
    closed);
  - expiry: rotating an expired PAT clears the grant (reason "expired"); rotating
    a live PAT keeps it. Expired tokens can still be rotated, as the connection
    docs describe.
- OAuth token and refresh responses (and the stored token rows) carry only the
  granted scopes the connection still holds after policy and role intersection,
  so a withdrawn `cards:decide` never reappears in a refresh.

- Test collection: the consent and Agents browser test skips without Playwright
  (collecting `tests/` must never error); `CP_BROWSER_E2E=1` makes a missing
  Playwright fail instead.

- Compatibility baseline: the pinned handoff "off" baseline test supplies
  `commonplace/decide_grant.py` and `mempalace/deadline.py` (leaf modules the current
  board and rules code import);
  its gateway, policy and handoff comparison stays pinned.

### Explicit imported-history reader consent

  - Imported view proofs share the response access checker with normalized citation and private-source authorization; thread-bound mutation and source-delete checks remain enforced.
  - Ordinary recall checks imported identity fields only for imported threads; the release journey verifies owner read-back and the write-only bearer’s metadata-free not-found response.
  - Legacy import compatibility: Existing three-part import identities retain ordinary filer-bound read access without data migration; consented reader views require complete four-part identities.

- `memory:import` stays write-only.

- New PAT and OAuth readers need a separate, unticked consent and `memory:read` plus `memory:import:read`.

- No defaults, person policies, migration, parent relationship, wing PATCH, refresh or CLI issuer adds this scope.

- PAT trust remains untrusted; OAuth trust remains standard.

- Exact filing account, workspace, private-import identity and current personal ownership authorize the read-only view.

- Import views bypass read-wing grants; explicit filters still select results.

- Full-ACL imports, other accounts, captures and unrelated private data remain outside the exception.

- REST and Memory reads share Recall authorization; MCP thread tools retain threads:read.

- A holder's complete archive ranges must fit within the import offset.

- Forks contribute only the bounded imported suffix.

- Unknown or inconsistent bounds fail closed, without read-time adoption.

- Mirror words require stored source, complete range and reconstructed unit proof before previews or CLI rendering; room authority and caller labels are insufficient.

- Indexed candidates may fall back to exact-view matches.

- Startup preserves the live eligibility callback and its deadline without opening new stores or scanning history.

- Revoking the connection stops all credentials; rotation preserves consent.

- Refresh narrowing affects descendants, while earlier access tokens and the connection policy retain their original authority.

- Revoke and reconnect with the reader box unticked to withdraw all authority.

- Stored imports and text already returned remain.

- No authority cache survives a response.


### Triggers

Moved to [`docs/system/triggers.md`](docs/system/triggers.md); edit it there.

### Durable orchestrator

Moved to [`docs/system/durable-orchestrator.md`](docs/system/durable-orchestrator.md); edit it there.

### Thread-search ranking

Moved to [`docs/system/thread-search-ranking.md`](docs/system/thread-search-ranking.md); edit it there.

### Owner-configured task types on cards (slice 1)

Moved to [`docs/system/owner-configured-task-types-on-cards-slice-1.md`](docs/system/owner-configured-task-types-on-cards-slice-1.md); edit it there.

### Goals tree in card context

Moved to [`docs/system/goals-tree-in-card-context.md`](docs/system/goals-tree-in-card-context.md); edit it there.

### Opt-in blockers, waiting reasons and split delivery

Moved to [`docs/system/opt-in-blockers-waiting-reasons-and-split-delivery.md`](docs/system/opt-in-blockers-waiting-reasons-and-split-delivery.md); edit it there.

### Foreground refresh rendering

Moved to [`docs/system/foreground-refresh-rendering-2.md`](docs/system/foreground-refresh-rendering-2.md); edit it there.

### Routines create cards on a schedule

Moved to [`docs/system/routines-create-cards-on-a-schedule.md`](docs/system/routines-create-cards-on-a-schedule.md); edit it there.

### Tool policy: Allowed, Ask first or Off per action

Moved to [`docs/system/tool-policy-allowed-ask-first-or-off-per-action.md`](docs/system/tool-policy-allowed-ask-first-or-off-per-action.md); edit it there.

### Webhook dispatcher recovery

Moved to [`docs/system/webhook-dispatcher-recovery.md`](docs/system/webhook-dispatcher-recovery.md); edit it there.

### Revoked-token socket guard

Moved to [`docs/system/revoked-token-socket-guard.md`](docs/system/revoked-token-socket-guard.md); edit it there.

### Design before code

Moved to [`docs/system/design-before-code.md`](docs/system/design-before-code.md); edit it there.

### Defaults chosen for slice 1

Moved to [`docs/system/defaults-chosen-for-slice-1.md`](docs/system/defaults-chosen-for-slice-1.md); edit it there.

### Live chat delivery state

Moved to [`docs/system/live-chat-delivery-state.md`](docs/system/live-chat-delivery-state.md); edit it there.

### Board coordinator settings

Moved to [`docs/system/board-coordinator-settings.md`](docs/system/board-coordinator-settings.md); edit it there.


### Thread-derived mutations and citations

Moved to [`docs/system/thread-derived-mutations-and-citations.md`](docs/system/thread-derived-mutations-and-citations.md); edit it there.

### Coordinator release authority

Moved to [`docs/system/coordinator-release-authority.md`](docs/system/coordinator-release-authority.md); edit it there.

### Eval archives (slice 1)

Moved to [`docs/system/eval-archives-slice-1.md`](docs/system/eval-archives-slice-1.md); edit it there.

## Numbers

| Label | Number | Source |
|---|---|---|
| M | `max_agents` range 1–100 (`MAX_AGENTS`; older tables are widened on the next write), counting distinct connection assignees | `commonplace/projects/limits.py` |
| M | Runner `min_free_memory_mb` default 256 (0 disables): below it the runner fails the lease `low_memory` before any launch | `commonplace/runner/agent.py` |
| M | One worker PAT per machine | `commonplace/runner/registry.py:61` |
| M | Runner store writes use `BEGIN IMMEDIATE`; busy timeout 10 s | `commonplace/runner/jobs.py:68-95` |
| M | One active start per card | `commonplace/runner/jobs.py:47-50` |
| M | A non-person start within 24 h of the last start is refused | `commonplace/runner/jobs.py:236-243` |
| M | Started jobs hold machine in-flight capacity and per-machine project exclusion while their card remains doing with that machine's worker as assignee; failed/cancelled jobs and cards no longer doing or reassigned release capacity, irrespective of elapsed time | `commonplace/runner/jobs.py:lease` |
| M | Hourly start-rate limits, the 24 h started_recently refusal and the separate 3600 s orchestrator admission hold remain unchanged | `commonplace/runner/jobs.py:_hourly,create_in`, `commonplace/orch/runner_bridge.py:_cap_count` |
| M | T3 launch timeout 2–60 s; a timed-out launch is `uncertain` and never re-queued | `commonplace/runner/agent.py:127,333-336` |
| M | Runner hub timeout 15 s | `commonplace/runner/agent.py:247` |
| M | Machine queue limit 8 queued jobs | `commonplace/runner/jobs.py:16` |
| M | `request_key` at most 128 characters | `commonplace/runner/tools.py:92,157` |
| M | T3 launch message at most 120,000 characters | T3 MCP launch schema |
| M | Eval run at most 16 MiB (rows, artifacts, manifest); 256 KiB chunks and read pages; 1 GiB of active runs per workspace | `commonplace/evals/__init__.py` |
| M | Eval key retention 30 days after purge; eval lock waits 5 s | `commonplace/evals/__init__.py` |
| T | A resume is one call; zero duplicate jobs after a lost acknowledgement | acceptance tests |
| T | Config at most 128 KiB, at most 16 roles, a prompt at most 32 KiB | `commonplace/orch/schema.py` |
| T | Per board at most 8 experiments and 20 open proposals, at most 5 per principal | `commonplace/orch/store.py` |
| T | `step_key` matches `[A-Za-z0-9._:-]{1,80}`, so `orch:<role>:<step_key>` fits 128 | `commonplace/orch/runner_bridge.py` |
| G | Lease TTL 1800 s; waits in slices of at most 10 min with heartbeats | rollout |
| G | Wait budgets: start 10 min, gate 90 min, merge 10 min, thread 4 h | rollout |
| G | The owner reviews at most 5 config changes a week | rollout |

## Stores

| Store | Scope | Holds | Backed up |
|---|---|---|---|
| `control.sqlite3` | hub | users, workspaces, memberships, connections, model settings, audit, sign-up waitlist (`signup_waitlist`, additive) | yes |
| `boards.sqlite3` | workspace | boards, cards, `task_events` (append-only) | yes |
| `runner.sqlite3` | workspace | runner jobs and their run ledger; orchestrator configs, cursors, leases, steps, journal, restore flag | yes |
| `rules.sqlite3` | workspace | rules, append-only `rule_versions`, suggestions, served state, and rule evidence: `rule_events` (capped at 50 per writer and 200 per rule) with `rule_stats` counters that keep the full history | yes |
| `topology.sqlite3` | workspace | Compute map facts: nodes, links, site routes, their observations (observer, verbatim evidence, expiry) and conflict records; proposals and applied changes with each fact's value before and after (`topology_proposals`); never secrets | yes |
| `topology.sqlite3` | workspace | Compute map facts: nodes, links, site routes, their observations (observer, verbatim evidence, expiry) and conflict records; never secrets | yes |
| `evals/runs.sqlite3` | workspace | eval runs, their chunks and payload-free purge tombstones | yes |
| palace and other workspace SQLite files | workspace | verbatim memory, indexes, threads | yes |

- Backups copy every SQLite file in the data directory (`commonplace/backup.py`).

- Online writer scans resolve the gateway PID's ancestor chain in the scanned `/proc`, so inherited data settings on supervisors do not falsely block a backup started separately from the gateway. Unrelated writers, including identical wrapper commands, remain refused; worker PID exemptions still end after pause. Offline scans retain only the backup's own ancestry exemption.

- Online enumeration permits only SQLite sidecars (`-wal`, `-shm`, `-journal`) to disappear between listing and metadata lookup: the live gateway may remove them even while file creation is frozen, and snapshots omit them.

- Missing user files, symlinks and special files still abort the backup; offline enumeration remains strict.

- The gateway names the workspace path when entering the creation gate: an existing directory lookup can finish inside a writer transaction while frozen, so it cannot deadlock against the backup's all-database barrier.

- Creating a new workspace directory still waits for thaw.

- `restore()` publishes a verified copy into a new directory and leaves a restore marker there.

- Deploy (`deploy/commonplace/deploy.sh`) requires a new successful backup invocation, a fresh snapshot and backup verification before any Compose call.

- A transient sleep service with `After=<backup>.service` keeps the backup unit referenced from before invocation capture through validation, independently of an enabled timer.

- The ordering dependency does not trigger a backup; it prevents garbage collection from discarding even a fast oneshot's invocation and execution time.

- The holder uses `Type=exec`, is stopped before Compose, and is released by EXIT and signal traps on failure or timeout.

- Missing proof always refuses deployment.

- SIGKILL or a shell crash can leave a holder that the operator must stop before retrying; see the deployment runbook.

## Restore runbook

1. Stop the service that uses the old data directory. Never run two services
   on copies of the same data.
2. Restore into a new directory (`commonplace restore BACKUP NEW_DIR`); the
   destination must not exist. Keep the old directory unchanged and read-only.
3. Start the service on the restored directory. On the first explicit open of
   the orchestrator store (a surface, the CLI, a lease call or a start; never
   the trigger sweep, which skips the workspace while a marker is pending), each
   workspace's restore marker becomes a durable flag in `runner.sqlite3` (new
   incarnation, all orchestrator leases dropped); the marker file is removed
   only after that commit.
4. While the flag is set, lease claims, every config-driven start and the
   trigger sweep are refused. Boards without a `[runner]` config are unaffected.
5. Check the truth sources for anything that may have happened after the
   backup time T (merged PRs, gate verdicts, launched threads, card states).
6. Acknowledge as the owner, typing the exact text:
   `commonplace orch restore-ack --text "effects after <T> may be unknown"`.
   This clears the flag and re-arms the trigger cursor of every configured
   board, so events from before the acknowledgement never fire.
7. Resume orchestrators with `orch_resume`; its checkpoint lists unstarted
   `todo` cards so nothing that was skipped is silently lost.

### Authenticated owner progress scope, State lifetime and quota causality

Moved to [`docs/system/authenticated-owner-progress-scope-state-lifetime-and.md`](docs/system/authenticated-owner-progress-scope-state-lifetime-and.md); edit it there.

## Residuals

- **Eval guard A4 is a lint:** the scan that no code rewrites thread
  identity or deletes thread-store content rows (the seven content tables
  `threads`, `messages`, `line`, `message`, `block`, `blob`, `dict`) reads SQL
  string literals, f-strings, `+` chains and `%`-formatted literals. It never
  catches less than the original per-piece scan, allowlist included (entries
  are scoped to a file and a statement kind). Known limits include `str.join`
  or `+=` building, `%(name)s` formatting, comments inside or before keywords,
  `INDEXED BY` before `SET`, tuple `SET`, quoted schemas, dynamic `SET`
  columns, lower-case dynamic statements with an alias tail, `TRUNCATE`,
  `ALTER TABLE … DROP COLUMN` and Postgres `DELETE FROM ONLY`. Prose with a
  placeholder followed by the end, `;`, `)`, `WHERE` or `RETURNING` is flagged
  as an accepted false positive and is never allowlisted (`docs/evals.md`).

- **Eval archives:** slice 1 covers handoff transcripts and ACL imports only:
  0% of live captures and grant imports, the automatic high-volume trace path;
  the share by count is unmeasured. Purge does not erase retired store
  generations, backups, WAL or free pages, and restoring an older generation can
  bring runs back. Anyone who can read `runs.sqlite3` or its backup can confirm
  a guessed key. A run whose source disappears stays unlisted until purged by
  its retained ID. Single-part imports, palace writers and handoff filing do not
  measure plan storage under the thread-store lock, so eval's plan-storage bound
  holds only against writers that do. Privacy flags miss doubly escaped JSON and
  health hints split across strings; they are signals, not gates.

- **Admin observations:** All seven sections are implemented; operational sections
  request data only with instance eligibility. Health validates the existing public
  `healthz` response independently from installed versions and packaged build SHA.
  Token activity indicates recorded contact, not a live process. Machine and worker
  contact remain separate; owner attestation is always unverified. There is no
  workspace concurrency cap. Off uses distinct doing agents (including unlimited);
  on reuses accepted-only slot accounting, preserving actual use above the cap and
  stored worker assignments on disabled machines. The reused needs-me route has a read-first limitation: `/api/needs-me` can initialize legacy stores and bind
  rule-store metadata; the no-write promise here covers only the new Admin GETs.
  SQLite may create WAL/SHM sidecars during reads, so no-write tests compare logical
  tables, schema and non-sidecar files rather than byte equality of WAL files.
  Section requests are independent observations and can race ongoing activity.
  Runtime/limits/stuck query unarchived boards through the generation-aware supervisor
  resolver, opening existing encrypted board/runner stores with mode=ro, query_only,
  a shared 2 second request budget, at most 250 ms connect/busy waits and a progress
  handler. Missing coordinator and heartbeat columns read as unconfigured/unknown without migration.
  Cursor slugs accept the creation limit of 100 characters. Only enabled, unrevoked
  machines contribute active grants; historical assignments still count toward usage.
  Runtime tables label connection state and token-use activity explicitly; next-page
  source errors preserve earlier rows and retry the same cursor. Typed runtime
  identifiers and enums reject malformed stored values.
  Missing optional files/tables remain absent; corrupt sources fail safely. Only Admin opts into missing runner input; normal admission retains its strict input contract.
  No application/writer locks, store constructors, seat creation, project GET,
  lock-taking settings/registry methods or webhook/coordinator wrappers run on these
  paths. Parameterized workspace-scoped joins verify current membership, disabled
  users, lifecycle, runner kind and subscription policy/wing eligibility. Wakeability
  is eligibility only, never delivery. The injected Admin UTC is captured once for
  lifecycle, stale/lease predicates and recent-start accounting; monotonic time alone
  bounds reads. Independent runtime subresults preserve completed sources and mark
  unattempted sources as errors on budget exhaustion. Client loads including JSON
  decoding retain the 5,000 ms deadline and generation/epoch disposal guards.
  Needs-me keeps its exact server count and queue semantics in a separate panel
  from the deduplicated union of expired leases, active blockers, waiting-person
  facts and raw stale-agent facts, including children. Admin actions are GET reads
  or navigation to existing details/settings; no new admission or mutation API.


- **Backup receipt limits:** Opt-in CLI receipts record the last completed online or
  offline instance snapshot attempt, outside every data/staging/snapshot tree.
  Final-mode reporting suppresses a rejected offline probe when online fallback is
  taken. Check, cleanup and restore do not report. A same-directory 0600 temporary
  file is fsynced, atomically replaced, then its parent fsynced; reporting faults
  emit only a fixed diagnostic and never alter backup outcomes or retries. SIGKILL
  before reporting leaves the previous attempt; no historical backfill, running
  indicator or remote-upload/retention verification is claimed. The gateway only
  reads canonical UTC timestamps with mandatory date/time seconds and optional
  one-to-six fractional digits. Parent receipt refreshes advance child generations
  and cancel older per-mode retries before applying results. The reader
  reads fixed names through no-follow/nonblocking descriptors, fstat regular-file
  checks, strict schema/enums/timestamps and a 16 KiB cap; corrupt modes fail independently.
  The hosting operator provisions one UID/GID 10001:10001, 0700 directory and 0600
  files. The optional overlay mounts it at `/run/commonplace-backup-status-ro` RO
  for the gateway's configuration and `/run/commonplace-backup-status-rw` RW for a
  snapshot-only CLI environment override. Compose must not create the host path.
  Both aliases are visible in the shared container namespace: the configured gateway
  path is RO and its code only reads, but this is not OS isolation from the RW alias.
  This accepted residual requires no alternate container or host reconfiguration by
  the application. Build identity is a validated 40/64 hex SHA installed as package
  data; absent/invalid markers remain unknown, never runtime git/environment identity.


- Accepted, known gaps.

- D9.2 = A is LOCKED: hidden storage may cause `deferred_storage` only for
  actually quota-blocked readable pending work; incomplete progress proof stays
  unavailable without partial counters or a global fallback.

- Each is a reason a check can pass while the system is still wrong.

### Scoped wing counts and search fan-out

Moved to [`docs/system/scoped-wing-counts-and-search-fan-out.md`](docs/system/scoped-wing-counts-and-search-fan-out.md); edit it there.

### Visible logical drawer pagination

Moved to [`docs/system/visible-logical-drawer-pagination.md`](docs/system/visible-logical-drawer-pagination.md); edit it there.

### Hallway read eligibility and visible pagination

Moved to [`docs/system/hallway-read-eligibility-and-visible-pagination.md`](docs/system/hallway-read-eligibility-and-visible-pagination.md); edit it there.

### Authenticated room and status counts

Moved to [`docs/system/authenticated-room-and-status-counts.md`](docs/system/authenticated-room-and-status-counts.md); edit it there.

### Thread-boundary residuals

Moved to [`docs/system/thread-boundary-residuals.md`](docs/system/thread-boundary-residuals.md); edit it there.

## Open dependencies (D1–D3)

- These are owner decisions or captures tracked in `docs/runner.md` ("Owner checklists").

- Until each is closed, the residual above stands.

- **D1 worker identity.**

  - "Attested, not verified" or a dedicated provider instance per worker; stronger identity needs a per-thread credential.

- **D2 launch shape.**

  - One real launch capture settles the result fields, the workspace strategy and `modelSelection`.

- **D3 recovery.**

  - The thread-list keys must be captured before any automatic reconcile; until then `uncertain` and unproven launches go to a person.

## Keeping this file current

- Add a NEW bullet next to the related decision, number, store or residual; do
  not extend a shared paragraph or collect unrelated additions at one section end.
  Keep each topic on its own lines, separated by blank lines. Independent
  insertion positions merge cleanly; edits at the same position can still conflict.
  In the module's file in `docs/modules/`, add a NEW indented sub-bullet to each
  touched module block instead of extending its **Promises** or **Tests** line.

- Parallel changes must not edit the same file. Module blocks live one per file
  in `docs/modules/<module>.md`; `MODULES.md` holds only the format. A new
  decision, number, store or residual can be added as its own file
  `docs/system/<slug>.md`, starting with a `### <title>` heading and written like
  a section of this file; it counts as the change to `SYSTEM.md` that `docs-check`
  asks for, and its place in this file is given by the heading (name the section
  it belongs to in its first sentence). Prefer a new `docs/system/` file over an
  insertion into `## Decisions` here. `commonplace orch docs-check --print system`
  and `--print modules` show the assembled documents.

- `commonplace orch docs-check --base <ref> --head <ref>` exits non-zero on a changed path no `MODULES.md` block owns, a touched module whose block did not change, no change to this file or `docs/system/` (a whitespace-only edit is no change), an empty or missing block, a bad ref or any error.

- The test suite runs it against the merge base with the development branch and fails closed when the base is missing.

- The only exception is a commit trailer `Docs-waiver: W<n>` whose id is listed below **in the base commit**; an id added in the same change does not count.

## Waivers

- Owner-approved waiver ids, one per line as `W<n>: <scope>`.

- None are approved yet.

### Topology is one store of structured facts

Moved to [`docs/system/topology-is-one-store-of-structured-facts.md`](docs/system/topology-is-one-store-of-structured-facts.md); edit it there.

### Bots keep conversations in the workspace store

Moved to [`docs/system/bots-keep-conversations-in-the-workspace-store.md`](docs/system/bots-keep-conversations-in-the-workspace-store.md); edit it there.

### Bots hear about their work without being asked

Moved to [`docs/system/bots-hear-about-their-work-without-being-asked.md`](docs/system/bots-hear-about-their-work-without-being-asked.md); edit it there.

### Bots use machines only through the runner's own checks

Moved to [`docs/system/bots-use-machines-only-through-the-runner-s-own-checks.md`](docs/system/bots-use-machines-only-through-the-runner-s-own-checks.md); edit it there.

### After Done: archive hides, never deletes

Moved to [`docs/system/after-done-archive-hides-never-deletes.md`](docs/system/after-done-archive-hides-never-deletes.md); edit it there.

### Reassigning active work

Moved to [`docs/system/reassigning-active-work.md`](docs/system/reassigning-active-work.md); edit it there.
### Legacy surface comparison and agent guidance

Moved to [`docs/system/legacy-surface-comparison-and-agent-guidance.md`](docs/system/legacy-surface-comparison-and-agent-guidance.md); edit it there.

### Compatibility fixture coverage boundary

Moved to [`docs/system/compatibility-fixture-coverage-boundary.md`](docs/system/compatibility-fixture-coverage-boundary.md); edit it there.

### Refused Done moves and handoffs

Moved to [`docs/system/refused-done-moves-and-handoffs.md`](docs/system/refused-done-moves-and-handoffs.md); edit it there.

### Canonical Space REST foundation

Moved to [`docs/system/canonical-space-rest-foundation.md`](docs/system/canonical-space-rest-foundation.md); edit it there.


### Authorization-only provenance spelling recognition

Moved to [`docs/system/authorization-only-provenance-spelling-recognition.md`](docs/system/authorization-only-provenance-spelling-recognition.md); edit it there.


### Current typed provenance association proof

Moved to [`docs/system/current-typed-provenance-association-proof.md`](docs/system/current-typed-provenance-association-proof.md); edit it there.


### Memory map citation omission

Moved to [`docs/system/memory-map-citation-omission.md`](docs/system/memory-map-citation-omission.md); edit it there.
### Explicit tunnel read proof

Moved to [`docs/system/explicit-tunnel-read-proof.md`](docs/system/explicit-tunnel-read-proof.md); edit it there.


### Explicit tunnel concurrency and import qualification

Moved to [`docs/system/explicit-tunnel-concurrency-and-import-qualification.md`](docs/system/explicit-tunnel-concurrency-and-import-qualification.md); edit it there.


### Graph projection before traversal and ranking

Moved to [`docs/system/graph-projection-before-traversal-and-ranking.md`](docs/system/graph-projection-before-traversal-and-ranking.md); edit it there.


### Readable legacy graph parity

Moved to [`docs/system/readable-legacy-graph-parity.md`](docs/system/readable-legacy-graph-parity.md); edit it there.


### Graph compatibility residuals

Moved to [`docs/system/graph-compatibility-residuals.md`](docs/system/graph-compatibility-residuals.md); edit it there.


### Local worker allocation observation

Moved to [`docs/system/local-worker-allocation-observation.md`](docs/system/local-worker-allocation-observation.md); edit it there.


### D1-visible KG pages and aggregates

Moved to [`docs/system/d1-visible-kg-pages-and-aggregates.md`](docs/system/d1-visible-kg-pages-and-aggregates.md); edit it there.


### Scoped KG search cost

Moved to [`docs/system/scoped-kg-search-cost.md`](docs/system/scoped-kg-search-cost.md); edit it there.

### Search enrichment compatibility note

Moved to [`docs/system/search-enrichment-compatibility-note.md`](docs/system/search-enrichment-compatibility-note.md); edit it there.
### Bounded memory for overview reads

Moved to [`docs/system/bounded-memory-for-overview-reads.md`](docs/system/bounded-memory-for-overview-reads.md); edit it there.
### Outgoing provenance aliases and coherent ranges

Moved to [`docs/system/outgoing-provenance-aliases-and-coherent-ranges.md`](docs/system/outgoing-provenance-aliases-and-coherent-ranges.md); edit it there.

### Authorized room and status count responses

Moved to [`docs/system/authorized-room-and-status-count-responses.md`](docs/system/authorized-room-and-status-count-responses.md); edit it there.

## Person private-read containment

Person sessions derive private read authority only from the exact current
workspace owner role. A missing, omitted or unexpected role grants no private
read scope. Membership alone is insufficient. The admin callback eligibility
reader uses that same role-aware policy. No stored membership or data migration
is involved; owner policies retain their existing scopes and behavior.

Members temporarily cannot read any private wing, including their own private
notes, private chats, thread mirrors and diaries. Workstation wing aliases that
require every wing, private-sensitive facts, secret/unwinged facts and the map's
people overlay are also unavailable. Existing input-authorized projections retain
shared overview, graph, KG, tunnel, hallway and drawer listings without hidden
labels, ranks or counts. Wildcard policies can reach those projections without
private scope; literal-wing policies keep their existing tool visibility.

A room, or a diary topic, names a thread when any copy of its value contains a
thread ID or a T3 source ID. The copies are the raw value and every result of
percent-decoding followed by NFKC normalisation, repeated until the value stops
changing (at most four rounds). The thread ID may appear as a bare ID, in an
`/app/threads/` path or URL, in a `t3-thread://v1/` link, after `mcp:`, or next
to letters, digits or underscores (`notes_thr_…`, `thr_…_notes`); fullwidth
spellings count after NFKC, and an upper-case `THR_` or hex spelling names the
stored lower-case thread, so its readers keep the room. A T3 source ID may contain
any character (`mcp:github.com/o/r`, spaces, quotes), so each `mcp:` occurrence
(any case) names the whole remaining value, which is resolved through the
workspace thread store: every stored source ID, or hash-variant base, that is a
prefix of that remainder counts, including `mcp:<source>_notes`. Source IDs are
compared after NFKC; only the namespace case-folds. A stored source is keyed as
NFKC first, then its variant base (cut at `#sha256:` in any case), then the
namespace folded; the room's whole value is NFKC-normalised with its namespace
folded and never cut at a hash marker. The tested stored spellings that match
`mcp:x` are `MCP:x`, `Mcp:x`, a fullwidth `ｍｃｐ:x`, and the mixed-width
`mｃｐ:x`, `mcｐ:x` and `mｃｐ：x`; any other ASCII and fullwidth mix folds the
same way through NFKC. A stored `mcp:x#` matches a room `mcp:x#sha256:...`, a
stored variant marked with a fullwidth number sign or an upper-case marker
matches its base, and a bare stored `mcp:` source is a prefix of every `mcp:`
value (it fails closed). Apart from the namespace, case is exact: `mcp:Probe`
and `mcp:probe` are different sources (thread IDs, by contrast, match in any
case).

Filing a thread or naming a successor thread accepts only canonical thread IDs:
the ID equals its NFKC form, has no control or format characters, no leading or
trailing whitespace and no percent-encoding, names `mcp:` (any case, after NFKC)
only as its leading namespace, and writes any `#sha256:` marker in lower case.
Other IDs are refused with a `thread_id ...` error before any side effect. T3
thread IDs (`[A-Za-z0-9][A-Za-z0-9._:@-]*`) and their `mcp:` forms pass. Each
stored thread is keyed in an indexed side table (`thread_source_keys`: NFKC
source, match key, legacy flag), written with the thread and backfilled for
older stores; a room check probes the room's candidate prefixes and its whole
NFKC value with indexed lookups, cached for the response. Sources filed before
this check that are not canonical are legacy rows: they form a small index built
once per response from those rows only, keyed by every `mcp:` remainder the room
resolver could extract after percent-decoding and NFKC rounds with control and
format characters removed and trailing whitespace stripped, cut at a variant
marker in any case; rooms are matched against it by prefix. A room value equal
to any stored source ID after NFKC is also hidden from readers who cannot read
that thread. If a source lookup fails, every value that is not a bare thread ID
is hidden. Write-side room checks share one reader per gateway call, final
tunnel pages share one reader, and a checkpoint takes at most 500 items.

One definition of a thread ID (`provenance_refs.THREAD_ID`) and one room
check (`provenance_refs.room_references`, used through
`mirror_access.private_thread_reference`) cover every route that returns or
writes a room or topic. Reads: drawer reads, search (structured and
CLI-compatible), drawer listings, room lists and status (overview projection),
taxonomy, tunnels, graph traversal and statistics, hallways, duplicate checks and
diary reads, including drawer `metadata.topic`. Writes: add, update, delete,
checkpoint items and diary, tunnel creation and diary writes. A reader who cannot
read every named thread, including every thread filed under a named source ID,
receives the drawer or entry as not found, and the room is omitted from listings,
exactly as for a bare thread-ID room. Readers of all those threads, including
owners, keep the room verbatim. A source ID naming no stored thread names
nothing; a source lookup failure hides the value.

Counts and flags describe only what the reader receives. Diary reads probe the
entry topics first, and the worker excludes entries whose topic names an
unreadable thread before it counts or windows them, so `total` and `showing`
cover visible entries; the final gate recounts any entry it still drops. The
probe reports which store served it (the SQLite fast path or the collection),
and the read is pinned to that store; a store change between the two refuses the
read in either direction. The probe reports at most 1000 distinct topics; beyond
that every read of that diary fails closed with an error saying the agent has too
many diary topics to check. An empty hidden-topic list leaves the read's SQLite
page path unchanged. A diary read never falls
back to an unscoped count: a probe validation error is returned as the read's
answer; paused or unavailable memory is reported exactly as on other routes (an
error, with `retry_after_s` during an online-backup pause); any other probe fault
is logged by class only and answered with an explicit error, for owners and
everyone else. The diary `thread_scope` is server-only: the gateway drops any
client value before every route and always sets its own, and the worker accepts
it without listing it. A diary entry hidden by its topic is hidden from diary
reads, drawer reads and listings, structured search (including unified media and
code search), CLI search text and `check_duplicate` matches: search rows and
duplicate matches carry the entry's `topic`, so the gate drops them.
`check_duplicate` recomputes `is_duplicate` from the matches that pass the gate. Search acquisition counters
and truncation flags (`total_before_filter`, `filtered_by_max_distance`,
`date_filter_pool_truncated`, `candidates_truncated`,
`distinct_results_truncated`) precede authorization and are omitted for every
principal.

Residual costs of failing closed. A hex run shorter than 16 digits directly
followed by an ASCII letter is an ordinary word (`thr_deadline`, `thr_notes`).
Any other `thr_<hex>` run counts as an ID, so hex-looking ordinary names such
as `thr_cafe` or `notes thr_add` resolve to no thread and are hidden from every
reader, owners included. Room-name validation refuses `/`, `:`, `%` and control
characters in rooms written through the tools, so link, `mcp:` and undecodable
forms reach storage through older or imported data and through owner
configuration: the project miner files drawers under room names taken from the
project's `mempalace.yml` without that validation. Values that cannot be decoded
(control or format characters, invalid percent-encoded UTF-8, more than four
percent levels in one round, still changing after four decode/NFKC rounds, or
over the citation length limit) are also hidden from every reader. Wing names
are outside this check: a wing named like a thread ID is filtered by wing policy
alone.

Known residuals. A legacy stored source whose `mcp:` is not the leading
namespace (for example `xmcp:x`, filed before thread IDs were checked) still
counts as naming `mcp:x`, so a room `mcp:x` is hidden from readers who can read
a canonical `mcp:x` thread but not that legacy twin; no new such twin can be
filed. The diary probe and the read are two worker calls: a hidden
entry filed between them with a `filed_at` older than the newest `last_n`
entries is neither excluded nor in the window, so `total` can count it once.
Its words and topic are never returned.

`mcp:` source IDs outside rooms. The room rule (whole value, prefix-matched,
every matching thread readable) also covers the other places a thread can be
named: provenance fields that name a thread (`source_thread`, `source_url`,
`source_link`, `origin_id`, `origin_link`, `approval_link`) on knowledge-graph
facts and their sources and on drawer metadata, `source_kept.thread` and the
`source` bundle of an add-drawer replay, and tunnel labels. A reader who cannot
read every matching thread loses that field (the fact, drawer or tunnel stays;
a tunnel whose label names such a thread is hidden, as for `thr_` references);
a value naming no stored thread is unchanged. `source_file`, `source_path`,
`source_drawer_id` and `source_closet` keep their existing checks.

CLI rendering rule (replaces the earlier rule that
every-wing policies keep worker-rendered CLI output and search diagnostics):
CLI-compatible search, for every principal, acquires structured hits and renders
only after the same drawer gate as structured search: room, topic and provenance
checks; body text is not scanned. A see-all reader may still lack a thread that a
room names, so no principal receives worker-rendered CLI text. This applies with
a literal wing and with no wing. Both CLI paths use the same pure formatter. The
gateway explicitly renders flat rows, so import-authorization metadata cannot
replace a readable row's wing, room or source. CLI responses contain only query,
CLI text, an empty results array, optional searched wings and non-empty facts.

Owner-visible changes from this rule set. Search acquisition counters and
truncation flags are no longer returned to owners. Owner CLI search uses the
gateway's flat rendering, so an empty result reads `No results found for:
"<query>"`, and owner CLI searches with `source_file`, `min_similarity`,
`max_distance`, `context`, `include_media` or a non-search `query_task` are
refused with the same errors as for other principals. Structured search rows and
`check_duplicate` matches for diary entries carry their `topic`. Diary reads cost
one extra metadata-only worker call (the topic probe). Rooms and topics naming a
thread the owner cannot read (another person's capture thread, a hex-looking
name, an undecodable value) are hidden from owners too.

Content placed in an ordinary shared room is visible on every route. Body text
and arbitrary provenance labels are not scanned for private words or fingerprinted;
recognized protected references in source_file, source_path and media path/title
aliases are still checked.

Provenance containing control or format characters (for example a tab or zero-width
space) is omitted even for ordinary paths. This remains fail-closed because hidden
or encoded characters can make reference interpretation ambiguous; readable drawer
text is retained without rewriting the path to guess its meaning.

Unprojected whole-palace operations remain unavailable to members: mesh estate,
duplicate detection, checkpoint, source-wide deletion, tunnel/hallway deletion,
memories-filed-away, and low-level coordination event/artifact/patch operations.
They have the same unknown-operation refusal as unavailable operations. Explicit
private reads remain independent of private data presence. This containment does
not define a restricted estate response or restore filer-specific private reads.

### Event, write and durable-token boundaries

Moved to [`docs/system/event-write-and-durable-token-boundaries.md`](docs/system/event-write-and-durable-token-boundaries.md); edit it there.
