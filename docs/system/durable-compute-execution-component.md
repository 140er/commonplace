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
