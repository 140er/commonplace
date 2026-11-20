### OAuth client approval belongs to one workspace

Public registration creates an identity and exact redirect list, never permission
to use a workspace. On the existing OAuth consent page, an unapproved client's
signed-in workspace owner sees an unticked **Approve this client for workspace**
checkbox. Ticking it and choosing **Allow** approves that exact client ID for the
current workspace and authorizes the owner's requested, consented scopes. A
member sees an owner-approval notice; forging the checkbox cannot approve it.
After approval, each member still signs in and consents independently, using
only their own workspace permissions. Approving one client or workspace grants
nothing to another, even with the same name or callback.

`oauth_client_approvals` records workspace, client, approving person and time.
The live person, membership and owner role are checked at code creation inside
the same transaction as approval, request consumption and connection/code
creation. Session validity is rechecked inside the authorization transaction.
Any failure rolls everything back; requests and codes retain their existing
single-use fences. Code exchange checks approval for the connection's actual
workspace before issuing tokens. PKCE S256, exact redirects, separate optional
scope consents and existing token revocation rules remain in force.

Migration creates an empty approval table. No registered name, callback,
registration date or existing connection is automatically approved. Existing
registrations need the same explicit owner action before a new authorization
can succeed. An outstanding code cannot exchange while its workspace/client
pair is unapproved. Already issued access and refresh tokens remain usable
under their original scopes, expiry, rotation and revocation rules, to preserve
established connections; refreshing such a token does not approve its client
or permit a new authorization. Owners can revoke these individual connections
under **Settings → Agents**. Removing the approving member or purging their
account clears their approvals; other workspace owners can approve again.

Approval records are classified for account export and deletion. They contain
no credentials and cannot grant private read, owner decisions or person-only
phone capabilities by themselves.
