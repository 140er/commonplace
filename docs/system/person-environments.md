### Environments registered to a person

A signed-in person can keep a list of their environments on the account, so a phone or another device can show the same list after sign-in. Each device keeps its own pairing secret. The hub stores only the stable id, the name, how to reach it (a URL or host, with no user, password or query secret) and an optional machine id, plus created, updated and last-seen times.

The app asks for `environments:person` in the native sign-in request, next to an ordinary scope such as `memory:read`. The consent page shows a separate checkbox, unticked until the person chooses it. The token's scope string does not gain `environments:person`; that name is not an OAuth policy scope. `memory:read` on its own cannot list or change the rows.

`GET /api/environments` returns `{"environments": [{"id", "name", "endpoint", "machineId", "updatedAt"}]}` for the caller. `machineId` is null when none was saved. `PUT /api/environments/<id>` with `{"name", "endpoint", "machineId"?}` inserts or updates that person's row and returns it. The same body returns the same public row, including `updatedAt`. `DELETE /api/environments/<id>` removes that person's row only. Another member, a session, or a token without this consent cannot read or change the rows. A person can register at most 100 environments.

No pairing secret, token or other secret field is accepted or stored. Account export and deletion treat the rows as that person's data.
