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
