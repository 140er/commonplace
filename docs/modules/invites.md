## invites

- **Paths:** `commonplace/invites.py`, `commonplace/web/invites.py`, `commonplace/web/static/invites.js`, `docs/invites.md`
- **Owns:**

  - Workspace invites: the `workspace_invites` table, making, listing, revoking and accepting an invite, and the list of a person's own memberships (`commonplace/invites.py`).

  - The invite routes (`commonplace/web/invites.py`): owner-only `/api/invites`, the `/app/join` page behind an invite link, and `/api/workspaces` with its switch.

  - The Admin invite block and the palette's workspace switcher (`static/invites.js`), and the contract in `docs/invites.md`.

- **Promises:**

  - An invite secret is returned once and stored only as a SHA-256. Accepting marks the invite used and adds a `member` membership in one transaction, so an invite admits at most one person. Unknown, used, revoked and expired secrets are refused alike, and an invite dies when its maker stops being an owner.

  - Accepting grants the existing `member` role and no connection, token or scope. Invites change no access rule: a member still reads no private wing.

  - `/api/invites` answers only a signed-in owner of the active workspace; members and owner tokens get 403 and another workspace's invite id answers 404.

  - `/app/join` moves the secret from the address into an HttpOnly cookie, joins only on a same-origin confirming POST from a signed-in person, then replaces the session. It counts against the per-address credential rate limit, like `/app/login`.

  - `/api/workspaces/switch` accepts only the caller's own memberships and answers 404 for anything else.

  - The invite block shows a new link once and never stores, logs or redraws it; leaving Admin, sign-out and session expiry clear it.

- **How it fails:**

  - A refused invite shows one page for unknown, used, revoked and expired alike; a wrong account for an email-bound invite is told so and the invite stays usable; an existing member does not use an invite up.

  - Invites create no account and send no mail: with sign-up closed, a person without an account cannot join.

- **Tests:**

  - `tests/test_commonplace_invites.py` covers invite, join, expiry, reuse, the two-person race, revoke, email binding, malformed secrets, every cross-user refusal and the workspace list and switch over public HTTP.

  - `tests/test_commonplace_invite_canary.py` is the second-user canary suite: after a real invite and join, the member's own session calls every MemPalace tool and every `/api/` GET route and must neither see nor change canaries in private wings, a private captured chat thread and a second workspace.

  - `tests/test_invites_ui.py` with `tests/invites_ui.mjs` covers the Admin invite block and the workspace switcher; `tests/test_commonplace_public_surface.py` holds the route inventory and the `/app/join` rate limit.
