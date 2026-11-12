## account-data

- **Paths:** `commonplace/account/`
- **Owns:**

  - `GET /api/account`, `GET /api/account/export`, `POST` and `DELETE /api/account/deletion`; the `account_deletions` control table; the classification of every control table (`tables.py`) that export and deletion share; the purge sweeper.

- **Promises:**

  - Only a signed-in person reaches these routes. An agent token, also the account's own, gets 403.

  - Export is one streamed ZIP of NDJSON: the account's control rows, and for each workspace the person owns its control rows, every table of every workspace database and every other file, as stored. Credentials and their digests are replaced in control rows. A workspace the person is only a member of is listed without content. Memory use does not grow with the account, and `manifest.json` plus the ZIP directory come last, so a broken download is not a valid archive.

  - Deletion is off unless `COMMONPLACE_ACCOUNT_DELETION=on`. A request needs a session started in the last 10 minutes and the typed account address, and only records a date `COMMONPLACE_ACCOUNT_DELETION_GRACE_DAYS` (default 14, 0 to 90) ahead; until then the account works and the person can withdraw it.

  - A purge deletes workspaces without another live member (directory, store generations, control rows), rechecking each one right before its files go. A shared workspace keeps its directory and every row keyed to it or to one of its connections; the account's membership and connections stay as tombstones (member role, revoked, no credential). Rows keyed to the account itself are deleted wherever they are, also when they name a shared workspace: sessions, identities, plan, usage, push subscriptions, its sync streams, chat jobs, held answers, agent schedules and routines. The `users` row stays without name or address, disabled; the audit log is kept.

  - A workspace the account owns alone, with other members left, is handed on, not deleted: when the purge starts, the longest-standing active member (plain member, account not disabled, no deletion request of their own; join date, then user id) becomes an owner in the same transaction. That writes a `workspace.owner_promote` audit entry, a `workspace.owner_promoted` event the new owner sees in the workspace feed, and a mail when SMTP is configured. A failed mail does not undo it. Cancelling a request promotes nobody, and repeating or resuming a purge promotes nobody twice. A workspace with no other member goes with the account.

  - A purge never runs while every other member of a workspace the account owns alone is disabled or leaving (nobody can take it over), the account is the last instance administrator or holds a GitHub grant, or while a control table is unclassified.

- **How it fails:**

  - Export refuses with 409 before sending anything when a workspace database is unreadable (an encrypted store) or a store pointer needs repair; a second export of the same account gets 429. An error after the first byte closes the connection without the archive's directory. A control table newer than `tables.py` is exported on a best reading and the manifest says `complete: false`.

  - A blocked purge records its blockers on the request, audits the change once and is retried by the next sweep; the account stays usable. A purge interrupted after it started leaves the account disabled in state `purging` and is resumed by the next sweep, also with the switch off again. Backups made before a purge still hold the data until they rotate out.

- **Tests:**

  - The `tests/test_commonplace_account_` files.
