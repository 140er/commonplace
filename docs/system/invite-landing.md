### Invite landing

- Opening an invite link now shows, before any form, who invited the person to which workspace; an invalid, expired or used invite shows a plain message and reveals nothing about the workspace. The first screen after a normal sign-up carries the three first steps (add a card, connect an app, ask for something). Closes gap 6 of `docs/usability-audit-372.md`.
- `GET /app/join/context` reads the invite cookie and answers 200 `{"workspace_name", "inviter_name"}`; the inviter name is their name, else the part of their email before the `@`. Unknown, malformed, missing, used, revoked and expired invites, and invites whose maker is no longer an owner, all answer one 404 `{"error": "invite_unavailable"}`. It counts against the per-address credential rate limit.
