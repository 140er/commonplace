### The connection id in /api/me

- `/api/me` shows an OAuth client its own connection id (`connection_id`, null for person sessions). The account-deletion authorize step (`purpose=account_deletion`) takes that id, and no other response the client can read carried it. It is the caller's own identifier: deletion still needs the browser re-check, the one-use proof and the PKCE verifier, so exposing it adds no capability.
