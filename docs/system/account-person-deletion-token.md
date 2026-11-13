### Fresh person confirmation for account deletion

The phone permission `account:delete:person` is separate from OAuth scopes and
from card, chat and event permissions. It is off by default and has its own
unticked consent checkbox. Existing clients and tokens receive no permission.
Both the phone-actions switch and account-deletion switch must be on.

The exact proposed consent text is:

> Allow this app to request deletion of your account only after you sign in again on this website and confirm account deletion here. The app never receives your sign-in credential. The existing waiting period and account protection rules still apply. Each request records your identity and this app's name. Disconnect this app to stop access.

The owner must approve this text before the change merges.

The iOS client opens the existing `/oauth/authorize` surface with the existing
public client id, exact `cmmn://auth/callback` redirect, PKCE S256 challenge,
`memory:read`, `purpose=account_deletion` and its existing connection id. This
opens a credential form, even when a session cookie is already present. A link
cannot confirm or mint anything. The person submits a new unused sign-in token
to the hub page; ordinary session cookies and provider sessions never substitute
for this check. This deliberately supports the hub's existing one-time sign-in
token credential, without treating a provider cookie as a new credential check.
The app receives no sign-in credential.

The credential must belong to the connection's person and workspace. Successful
checking rotates the form nonce and starts a new session in that browser. The
next page shows the person's name and address, the whole-account effect,
workspace deletions or hand-overs, the configured grace period, an email typing
confirmation and visible Cancel. Both forms require the same browser cookie;
the second additionally requires that exact freshly created session. POSTs
require an exact same-origin Origin and reject bearer authentication. CSP blocks
scripts, other form destinations and embedding. Cancel consumes the form and
mints nothing.

Confirmation must occur less than 300 seconds after the credential check.
Changes to membership roles, workspace set or grace period require starting
again. The redirect returns only `deletion_token`, `expires_in`, the original
state and issuer. It contains no general access or refresh token. Delivery is
once; repeating the confirmation refuses.

The client sends its existing OAuth bearer, `X-Account-Deletion-Token` and JSON
`{confirm, code_verifier}` to the existing `POST /api/account/deletion`. The proof
is opaque, hashed at rest, expires after 300 seconds, and is bound to person,
workspace set and roles, connection, public client, browser session, credential
check time, grace period and PKCE challenge. A compare-and-set consumes it before
the existing deletion checks. A policy refusal also spends the proof and retains
the existing web error. A successful request only schedules deletion: the same
grace period, block rules, hand-over and purge code apply. The token cannot
withdraw a deletion, export data, authenticate any other route or invoke tools.

Signout invalidates all outstanding account proofs and forms. Client disconnect,
consent withdrawal and machinery conversion remove their authority. Each use
rechecks the live connection, bearer, grant and session under the control lock.
An observed disabled switch permanently invalidates outstanding forms and
proofs; use is denied whenever either switch is off. The bounded shared limiter
charges both person and client for form starts, credential checks, minting and
proof-use attempts, even when general request limiting is disabled (20 actions
per 300 seconds for each identity).

The control tables `account_delete_checks` and `account_delete_tokens` are keyed
by person, workspace and connection and classified as credentials. Account purge
removes them; exports redact hashes, including session and PKCE hashes. Consent,
credential checks, minting, use, cancellation and refusals are audited without
credentials. The ordinary credential redactor also recognizes deletion tokens.
SQL table names are literal.

New routes: none. The existing authenticated authorization POST gains a distinct
browser-only form flow; the existing account-deletion POST gains only the
dedicated proof branch. Public route inventories and disabled baseline pins
remain unchanged. These account operations continue the existing person-only
rules policy; no connection write tool or checked card/chat alias is added.

`tests/test_commonplace_account_person_token.py` exercises the real HTTP flow,
freshness and expiry boundaries, wrong identity/client/PKCE/workspace/route,
signout, disconnect, disabled switches, consent withdrawal, machinery, replay,
concurrent consumption, CSRF, cancellation, unchanged account blockers and
hand-over, grace timing, export redaction and secret sentinels.
