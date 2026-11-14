### Explicit tunnel read proof

Authenticated list_tunnels and follow_tunnels require both stored endpoint pairs
and every optional drawer's current metadata association to pass wing policy and
the existing reader in the authenticated workspace, including for owners. A
readable endpoint hint cannot authorize a moved drawer. Missing, malformed or
unresolved optional drawers drop the entire tunnel; room-only readable tunnels
remain eligible. Local unscoped calls keep their behavior.

Private probes page at most 250 tunnels and resolve at most 500 drawer metadata
rows per batch without loading document columns or calling get_drawers. Concrete
allow descriptors are request-local. The final read rechecks stored endpoints
and metadata descriptors, excluding changed or newly arrived associations before
follow previews or traversal recording. Reader authority is refreshed after
probing. Incomplete acquisition, malformed protocol responses and deadline errors
produce an empty public list without private markers. Read-only follow retains
record=False behavior; create/delete authorization and delete snapshot checks
are unchanged. Passive graph reads use the input projection described below.
