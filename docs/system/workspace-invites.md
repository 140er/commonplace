### Workspace invites

- An owner invites a person with a single-use link that expires (7 days, or 1 to 30) and can be revoked. The secret is shown once and stored only as a SHA-256. Joining needs a signed-in account and one confirming click, and grants the existing `member` role with no connection, token or scope. `docs/invites.md` is the contract.
- Invites change no access rule. A member still reads no private wing, their own notes included; who may read a private wing in a shared workspace stays an open product decision.
- A person in several workspaces switches with `/api/workspaces/switch`, which only ever accepts one of their own memberships.
