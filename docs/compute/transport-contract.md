# Compute implementation boundaries

2026-11-09. This document describes isolated transport and credential custody.
Design rationale is in decisions.md; synthetic checks do not certify live enrollment.

## Components

Credential custody is implemented in `commonplace/compute/credentials.py` and
covered by `tests/test_compute_credentials.py`. The transport components are
`commonplace/compute/protocol.py` and `transport.py`, covered by
`tests/test_compute_transport.py`. Changes must preserve public integration,
schema/authority coordination, dependency declarations, SYSTEM.md and MODULES.md.
Use no live remote endpoints, secrets or operator key files in development or testing.

## Custody seam

Implement a concrete encrypted credential store, independent of HTTP. Constructor
receives explicit database path, external wrapping-key path, protected data root
and an authority callback. No environment/ambient credentials. Explicit local
initialization is separate from opening existing custody; never regenerate lost
keys over ciphertext. Authority callback must approve exact binding/action using
current trusted service authority, not a caller-provided owner boolean.

Immutable CredentialBinding fields: workspace, owner, gateway, node (empty only
for gateway credentials), account, purpose (`gateway` or `target`), version
positive integer. Validate exact bounded identifiers/account and deny unexpected
fields. Generation creates Ed25519 material internally, returns opaque credential
ID, binding, expiry and public key only. Store AES-256-GCM ciphertext and wrapped
per-workspace data key; versioned AAD binds credential identity and every binding
field plus expiry/keyset. Revocation and expiry deny acquisition. Handles must
hide private material in repr/serialization; only an explicitly named internal
transport accessor may produce short-lived private bytes. Nothing prints them.
No claim that Python guarantees zeroization or local revocation removes remote
keys. Rotation may issue distinct pending versions; activation must remain
explicit rather than silently replacing an enrolled binding.

Constructor/operation signatures may follow source conventions; document actual
public symbols precisely in module docstrings for integration. Tests must
exercise real crypto with synthetic generated keys, binding/ciphertext tamper,
wrong/missing/unsafe wrapping keys, expiry/revocation/reopen and no plaintext on
disk. No provider may auto-initialize production key material during requests.

## Transport seam

Build a versioned bounded JSON helper protocol and real optional SSH adapter.
Public service calls use immutable exact gateway/target enrollment IDs and pins;
no generic connect(host,port) or user-controlled command/URL. Gateway policy maps
an enrolled node ID to an owner-installed numeric address/port. Validate all
limits, fields, identities and frame lengths before effect; active discovery and
scripts remain unsupported. Only typed `system.info.v1` is initially compiled.
A fixed command compiler must reject free-form commands and unknown arguments.

Outer SSH must verify the complete pinned host key before authenticating. Inner
SSH on the helper-provided channel independently verifies the target key before
its separate authentication. No TOFU, agent forwarding, SSH config, ambient
keys, passwords, keyboard-interactive or direct-target fallback. Lazily import a
maintained optional SSH library (Paramiko is acceptable), declared in the compute extra.
Transport receives short-lived explicit key material/handle; no credential store
or privileged HTTP integration belongs in the transport adapter.

Use a narrow helper handshake: describe/collect_passive/open_enrolled_ssh,
8KiB control frames, fresh challenge nonce and exact request/digest/binding-epoch
admission. A service authorization callback supplies fresh dispatch authority;
local replay journal consumes one permit before route opening. Replayed or
ambiguous attempts never auto-reexecute. Protected gateway configuration is
trusted installation input, not a request body. No generic shell execution.
Enforce finite timeout/output/tunnel budgets and truthful cancel/disconnect
states; missing exit evidence is uncertain. Helper/channel abstraction tests
must prove gateway and target key rejection before authentication, enrolled-only
routing, replay denial, unknown/oversize field rejection and no silent fallback.

Enable these modules only with authenticated persistent enrollment,
approvals, current-authority fencing and deployment prerequisites. A domain
binding, callback or passing synthetic fixture grants no live execution right.

## Integrated dependency evidence

The optional compute extra resolves Paramiko 5.1.0, checked against the primary
[changelog](https://www.paramiko.org/changelog.html) (5.1.0 dated2026-10-17).
The dependency is lazy and never enables transports on installation. Synthetic
socketpair fixtures exercise the actual library and encrypted synthetic keys,
not just an injected fake driver. No live network/device acceptance is claimed.

## Tunnel concurrency and settlement

The helper's tunnels limit bounds concurrent tunnels. A failed route open creates
no tunnel and records closed. Once target.close succeeds, any relay ending is
closed, including timeouts, byte-budget failures and disconnections. Closed rows
release concurrency and remain denied on replay; they prove no process kill.
Close failures and crash-orphaned consumed rows stay counted. A trusted operator
can reconcile a named row using the same CLI's --kind tunnel on the helper host,
with its private journal, workspace/gateway/execution id and external evidence
note. BEGIN IMMEDIATE serializes this action with consume; operator_closed and
its append-only audit release capacity while preserving replay identity. Passive
helper rows have the analogous --kind passive action. See RUNNING.md.

In wire mode go cannot open a route: transport uncertainty starts immediately
before permit send. A denied issuance sends no permit and remains not_started.
The controller commits stage 4 before either exec channel opens; earlier stages
settle known non-run, while legacy stage-4 unknowns cannot infer a non-run from a
missing wire permit.

In-flight execution/discovery authorization re-reads exact current authority and
custody under the fence instead of comparing the global epoch with the claim.
Permit inserts and lookups use the request's epoch. Discovery validates its final
response against the immutable attempt and durable permit independently of current
publication authority. A verified final exchange settles its slot even after
scope/session revocation, withholding observations. No durable permit means a
known failed non-run. Uncertain controller discovery slots have fenced operator
recovery through --kind discovery, with a note and discovery_operator_settled
terminal reason. Scope quotas remain charged and attempts are never retried.
