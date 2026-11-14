# Compute integration requirements

2026-11-09. Requirements for authority fencing and bounded passive inventory;
implementation checks do not establish release or review approval.

## Participating authority fence and execution lifecycle

The execution and authority-fence components are commonplace/compute/fence.py,
executor.py, control.py and __init__.py, with central mutation integration in
control_db.py / boards/store.py / credentials.py and tests/test_compute_fence.py.
Preserve protocol, transport, scopes, discovery and UI interfaces; keep shared
documentation and focused integration checks consistent.

Replace the abstract-only production integration gap with a concrete, bounded,
cross-process local authority fence and actual central mutation participation.
Follow the accepted single-control-plane POSIX design: one safe service-owned
lock order, consistent workspace authority epoch, fresh current reads and durable
admission inside the fence, no network or model work while held. Cover relevant
users/memberships/sessions/connections, board/task assignment/block/approval,
Compute consent/enrollment and credential revocation/rotation. Inspect actual
central write paths and provide a source-based participation map; a Python mutex,
SQL regex guess, declared domains flag or fake lease is not sufficient. Unknown
or mixed unparticipating deployments must deny, not silently certify themselves.
Gateway admission ordering must be explicit within the existing authenticated
helper callback/nonce/request epoch contract, without claims of instantaneous
remote revocation. Production means local code integration, not live deployment.

Wire bounded service lifecycle into the actual gateway/Compute extension with
explicit trusted per-workspace configuration. Default off, no keys generated or
read during imports/ordinary HTTP. No public caller may supply a fence, wrapping
key, verifier signing key or verified boolean. A missing fence/provisioning/key
must keep execution disabled, with truthful management capability. Reuse existing
stores; preserve management API/browser behavior and actor/task authority. No
public arbitrary dispatch or owner-secret API. Keep independent signed receipts
and exact action approvals, no automatic bypass of plan/review gates.

Implement and author meaningful multiprocess tests proving revocation committed
before permit issuance wins, concurrent role/session/task/binding/credential
changes cannot slip between fresh check and admission, lock timeout/order behavior,
crash/restart epoch consistency, no network lock retention and no duplicate effect.
Do not weaken unrelated production protections or change existing test fixtures
merely to make a false authority guarantee pass. If a true source integration gap
remains, report it precisely and keep affected capability disabled, but continue
other concrete implementation rather than returning an abstract-only scaffold.

## Bounded passive gateway inventory

The passive inventory components are commonplace/compute/discovery.py, typed
protocol.py helper support and tests/test_compute_discovery.py. Changes must
preserve control Store, executor/fence, custody, transport, routes and UI interfaces.
Controller persistence and the public discovery surface must remain consistent
with the tested collector contract.

Implement actual bounded passive-cache parsing and collection with exact existing
DiscoveryContext/NetworkScope/DiscoveryLimits. Trusted injected cache suppliers
supply existing approved interface observations; the implementation must never
probe hosts, resolve names, open outbound sockets or auto-enroll nodes. A default
local helper supplier may read specifically approved OS cache sources with strict
byte/line/time bounds, but tests use synthetic text/files only, never current LAN.
Methods active and unsupported sources deny explicitly. Reject/make inert remote
metadata; preserve gateway/interface/source/timestamp provenance, exact numeric
in-scope addresses, bounded text and output, cautious deduplication. Supplied
observations cannot expand destinations or become tools/instructions.

Extend versioned helper collect_passive only through a typed exact scoped request
and an explicitly configured trusted collector, retaining default-unavailable
behavior. Recheck independent protected helper ceilings and current authenticated
workspace/gateway/user/agent/project/task scope, expiry/revocation at each admitted
observation, cancel/time/rate/host/result/output ceilings across the collection.
No generic proxy, caller file path, shell command, mDNS fetch or active fallback.
A discovery grant never authorizes enrollment/execution; no private key needed
inside parser. Match existing 8KiB framing and exact fields/duplicate-key checks.

Tests must prove IPv4/IPv6 cache edge cases, foreign scope/context, expired/revoked
or active requests, limit/cancellation/unknown source, metadata injection inert,
dedup/provenance and that no sockets/DNS/processes are touched. Use real schema
objects plus deterministic fake clocks/cache readers. Existing protocol tests must
remain valid, especially default unavailable collection and fixed SSH execution.
