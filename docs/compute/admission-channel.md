# Authenticated helper admission round-trip implementation

Implemented in source9ea079d908005e3cd65ce448d87f68fd322b30bb, integrated2641cc18.
Trusted WorkspaceExecutionConfig.admission_mode must explicitly equal
`controller-ssh-permit-v1`; the helper must independently select the same mode.
The mode enters Store._binding and therefore the exact approval digest. Local
leases still cover only LOCAL_DOMAINS; actual remote ordering uses the separate
SSH exchange, never an added lease flag. Unconfigured execution stays disabled.
Initial guarded64checks passed after the parent corrected a fixture control-frame
injector that misclassified subsequent encrypted SSH packets. This is component
verification, not independent review, provisioning or a release verdict.

Controller gateway_admissions and the helper replay journal use additive schema
migrations preserving earlier consumed identities. The controller records the
unique helper channel, nonce and exact permit before returning to the transport;
the helper validates and consumes its record before opening a route. Issued
permits remain in flight: uncertainty reserves capacity and does not auto-retry.
Passive cache collection needs its own purpose-bound admission (#325) and remains
unavailable with a local-only lease.

The frozen implementation brief follows for traceability.

Next builder owns compute/protocol.py, transport.py, executor.py and narrowly
needed fence.py / __init__.py lifecycle configuration, plus new
 tests/test_compute_admission.py and necessary isolated SSH fixture updates.
Do not edit discovery.py or the active discovery_store/service/routes/transport
builder files. Parent owns shared docs, UI and grouped checks.

Close gateway_admission_unintegrated using the existing authenticated controller
SSH helper channel. No new HTTP listener, token/identity registry, private-key
sharing or generic network proxy. The forced helper/controller SSH peer binding
already defines the exact workspace/gateway. Add a versioned explicitly selected
admission handshake, with no silent legacy fallback for configured live work.
Default unconfigured execution must remain off. A declared domain or constructor
boolean alone cannot establish remote admission participation.

Concrete sequence: request -> helper ready (fresh nonce/channel/digest/deadline)
-> controller fresh fenced dispatch -> go -> helper admission challenge ->
controller fresh local participating fence + exact attempt/epoch/approval/session/
binding checks + atomic durable one-use permit consumption -> nonce/channel-bound
permit -> helper independent verification/journal consumption -> enrolled socket
open -> opened -> independent target SSH pin/auth/fixed operation. No lock spans
network, including challenge/permit send or SSH negotiation. The current
ExecutionService._gateway_admission provides local checks/journaling but must be
bound to actual wire challenge identity, not merely callable in a fake helper.
Adapt it precisely as needed. A revocation committed before permit issuance wins;
an already-issued bounded permit is in flight, not instantaneously revocable.

Bind protocol versions, request digest, execution/attempt, gateway/node/credential
versions and epochs, fresh helper nonce/channel and short expiry to the action and
permit. Strict fields/frame sizes/byte budgets still apply. Reject forged, stale,
reordered, mismatched, duplicate or ambiguous exchanges; any uncertain post-permit
failure must never retry effects or free reservations without evidence. Both
controller and helper enforce one-use durable records. No route may open before
its exact permit. Preserve independent SSH pins before authentication and exact
compiled operation. Discovery is not automatically granted an execution permit.

Only remove the local-fence execution hard stop when actual explicitly configured
new-channel wiring participates, with source-supported proof. Keep local-only
leases distinguishable from a complete dispatch path. Read current trusted
ExecutionConfig/WorkspaceExecutionConfig and update their opt-in configuration
without body-supplied trust or mandatory key regeneration. Wire actual production
adapter/lifecycle, not only a test callback. Existing default-off and legacy inert
component tests remain valid; configured new-mode tests exercise the real path.

Author multiprocess tests of writer exclusion and pre/post-issuance revocation
ordering, and actual Paramiko socketpair tests that run the current guarded
ControlDB/boards/Compute/custody stores, signed enrollment receipt, approved
proposal, real two-hop SSH and fixed output. All fixture keys are synthetic and
private directories explicit. No LAN/device/operator-secret access. Prove wrong
pins before auth, unknown modes deny, missing challenge/permit, replay/tamper/
expired/timeouts, no network under authority lock, no duplicate admission/effect
on restart/lost ack, and that default/unintegrated deployments remain disabled.
Parent runs test suites; child quick lint/syntax/diff + normal hooks only. Return
actual checks/limitations and commit. No review/gate/deployment claim.
