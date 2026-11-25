- **Owns:**

  - Cloud workspace rules added in review round 4 (`workspaces.py`, `routes.py`): refusals from the shared write boundary on a workspace route are mapped by their code (a foreign Origin or a non-JSON write is `invalid_request` / `request`, only a non-person caller is `owner_required`), one shared SELECT (`_SELECT`, `_rows`) reads every record with its machine, the uniqueness key is NFKC-casefold (`name_key`, schema version 3) and the migration is one `BEGIN IMMEDIATE` transaction with `user_version` written last.
- **Promises:**

  - A registry login typed into an image address is refused as `user_info`, a public IPv4 address as `registry_ip`, and local resolver names and suffixes (`localhost.localdomain`, `localhost4.localdomain4`, `.localdomain`, `.svc`, `.ts.net`, `.consul`) as `registry_private`, each with its own reason; U+180F is a hidden name character; `ready` is only written with an applied revision that covers the record's revision.
- **How it fails:**

  - A migration that fails part-way changes nothing and runs again in full on the next open; a name that collides with another only after NFKC folding is `name_taken`.
- **Tests:** `tests/test_compute_workspaces.py` (foreign-Origin mapping, name-key collisions and key migration, `ready` without a covering revision, crash between migration steps, statement count of the list).
