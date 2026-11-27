- **Promises:**

  - `GET /app/join/context` answers a holder of the invite cookie, signed in or not, with the workspace name and the inviter's name (their name, else the email before the `@`) so the first invite screen says who invited them to what before any form. It never takes the secret from the address, and an unknown, malformed, missing, used, revoked or expired invite, or one whose maker stopped being an owner, gets one identical 404 with no workspace or person in it. It counts against the per-address credential rate limit.
