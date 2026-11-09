### Event, write and durable-token boundaries

Non-owner person event feeds use the same current-principal visibility as
webhooks: boardless events require every-wing access, and board events require
a readable active board (space messages retain coordinator checks). Their feed
and cursor are projected onto visible events; hidden events do not change the
member feed response. Members consequently lose boardless
live Memory refresh signals. Card and board linked-memory state can remain stale
until the next board event or an explicit refresh. Owner feeds retain their
original ordered log.

Each member poll snapshots readable active boards once and filters a bounded
SQL page against that set. The opaque epoch hashes the member role and readable
board identities, never hidden activity. A missing or changed epoch, or a cursor
above the projected latest, returns `reset: true` without replaying events. The
app refetches the open screen, including an open card, before adopting the reset
cursor and epoch. Ordinary pages advance only through returned events. An owner
request carrying a non-empty member-era epoch also returns a reset with no
events, adopting the latest raw cursor before resuming the owner feed.

Deploy note: open member tabs from before this deploy stop receiving live events
until they reload. Reload those tabs after upgrading to resume live updates.

Webhook subscriptions whose effective principal lacks `policy.sees_all_wings`
receive no numbers: payload `seq` and delivery-list `event_seq` are omitted.
Their payload id is `evt_` followed by the first 32 hexadecimal characters of
HMAC-SHA256 of the raw event sequence, keyed by an independent server-only
per-subscription key. The disclosed signing secret cannot derive those ids.
A restricted retry keeps the same opaque id for the same event.
The internal `(webhook_id, event_seq)` key still deduplicates enqueue attempts.
There is no subscription counter, counter column or counter backfill. One
nullable key column is initialized atomically on first restricted use, persists
across restarts and is never returned by any API or logged. No operator setting
or manual data setup is required. Subscriptions with `sees_all_wings` retain the
raw sequence and id. Enqueue, send and listing resolve the subscription's current
effective authority, including owner-held restricted tokens; an unresolvable
principal is restricted. Send rechecks queued payloads after authority changes.
Subscription lookup exceptions log one diagnostic with exception type and stack
locations, excluding exception text and key material; admission still fails closed.

Writes into unreadable private wings remain allowed, but duplicate drawer
retries return the same successful shape as fresh writes. Replay fields and
retained source provenance are hidden; returned structural fields depend only
on the supplied request and current chunk configuration. Readable-owner retries
still report already_exists. Tunnel creation additionally requires both endpoint
wings to be readable for principals without every-wing access, and outgoing
write results pass the wing scrubber. Denial happens before any private upsert.

Token issuance refuses private read scope for a holder who is not a workspace
owner. PAT, durable subscription connection and OAuth authentication strip both
private read and admin scopes unless current membership is exactly owner;
missing membership rejects authentication. Stored policies are not rewritten.
Member PATs minted earlier with private:read silently lose it when loaded.

Health-flagged member notes are always saved privately when private writing is
allowed; they are never redirected to shared storage. The successful HTTP save
response includes a notice, and the MCP save text states: "Saved privately. You
cannot read this note for now." This also applies to other saves into unreadable
private wings. The notice uses the actual filed destination after write-wing
pinning; readable shared or inbox saves do not carry that notice. A save that
succeeds privately but fails to link to a card includes the same notice in its
saved-but-not-linked error.
