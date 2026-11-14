- **Owns:**

  - `purpose=account_deletion` on the existing authorization surface forces a one-time credential check followed by its own confirmation page. No new route, OAuth scope or default permission is added.
- **Promises:**

  - `account:delete:person` has a separate initially unticked checkbox with the exact proposed text in `person_actions.DELETE_TEXT`. An existing logged-in cookie cannot mint a proof. The hub's browser-bound credential form must check the person's unused sign-in token; no provider session is treated as a fresh check.
  - Minting needs a same-browser, same-session confirmation within 300 seconds, exact same-origin POST, email typing and explicit confirmation. Cancel mints nothing. Signout and client disconnect revoke outstanding authority. The one-time callback proof is PKCE-bound and grants no general sign-in authority.
