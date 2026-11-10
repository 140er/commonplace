# Passive gateway admission

Source implementation ef246b21b8a7b5a8fd3145317efe51203874e034, integrated50a5e798.
This is reversible feature development, not provisioning or a release verdict.

The existing pinned outer SSH subsystem carries a separate passive-purpose
exchange. PassiveDiscoveryRequest and PassiveAdmissionChallenge bind the exact
scope/context, sources, bounds, request digest, immutable attempt, authority and
gateway epochs, helper channel/nonce and short deadline. The controller commits
collection intent and a fresh authority-checked one-use permit in separate stages.
No lock spans network work. The helper validates its own protected ceiling,
current scope and exact permit, and durably consumes replay identity before any
cache supplier call. No target SSH tunnel, shell command, DNS or active probe is
created by collection.

DiscoveryStore preserves immutable discovery_attempts/discovery_permits. Helper
replay uses a separate passive namespace, preserving existing execution records.
Controller and helper both reject replay, tampering, expired/reordered exchanges
and unknown modes. Partial or lost responses after durable permit issuance
preserve uncertainty and capacity. Without that permit, the controller records a
failed non-run with slot=0. A valid permit-bound final response settles the slot
even after publication authority is lost, withholding observations in that case.
After claim, global epoch changes do not replace exact fresh authority checks.
Session, membership, agent connection, scope/task/origin release, gateway receipt
and custody are re-read before dispatch and publication; permit records retain
the request epoch. The pre-pin connect check opens no custody. Active credentials
are checked after the independent gateway pin, before authentication, collection,
permit issuance and publication; a bad pin never accesses keys.
Restart never automatically recollects. Scope ceilings remain permanently
charged, including unused/cancelled allocations.

Default jobs reserve up to32 hosts/results over10 seconds and admit up to32
observations per second. Intake still clamps those limits to the remaining scope
and configured workspace/gateway ceilings. Response validation allows the full
configured result count, bounded by actual source lines and the output budget.

Trusted startup explicitly sets discovery_enabled, discovery_sources and
admission_mode=controller-ssh-permit-v1. A protected helper independently selects
its passive admission mode and supplies its own scope resolver/ceiling/caches.
The controller also requires existing custody and a separately signed gateway
discovery receipt. Discovery-only startup installs the local authority fence
without enabling fixed-operation execution. LOCAL_DOMAINS still exclude remote
admission; a lease alone grants no network effect.

The initial58 tests passed against synthetic custody, receipts, scopes, caches
and actual Paramiko socketpairs, including multiprocess revocation ordering.
No live cache, LAN, operator key or remote device was accessed. Issued short
permits remain in flight; no instantaneous remote kill or filesystem rollback
guarantee is claimed. External helper enrollment, protected persistent journals
and independent reconciliation before restore remain operator responsibilities.

An uncertain controller discovery can be reconciled after worker restart using
python -m commonplace.compute.operator --kind discovery with the existing
controller paths, discovery id and external-evidence note (RUNNING.md). The local
authority fence and discovery transaction guard this operator action. It requires
uncertain/slot=1, appends the note to discovery_operator_audit, records terminal
reason discovery_operator_settled and releases only concurrency. Quotas remain
charged; attempts/permits stay immutable; observations are not published. Counted
consumed/uncertain rows on the helper host have separate --kind passive recovery,
serialized with consume by the helper journal's BEGIN IMMEDIATE fence. Reconciled
helper rows stay denied on replay and late completion cannot re-reserve them.
