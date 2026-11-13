## chat-bridge

- **Paths:** `commonplace/chat_bridge/`, `tests/test_chat_bridge_foundation.py`, `tests/test_chat_bridge_identity.py`
- **Owns:**

  - The foundation of the chat-platform bridge (`docs/design/chat-platform-bots.md`, card #455, slice 1): the workspace file `chat_bridge.sqlite3` (the whole schema of the note; opened like `bots.sqlite3`, created only by creating an install, so a read, an unknown id or an intake request never makes it), the install rows, sealed credentials, the owner install routes, the adapter's `test` seam, the read-only `bridge_status` tool and the kill switch (`commonplace bridge status|disable`). The package is `commonplace/chat_bridge/` because `commonplace/bridge.py` is the MCP client bridge.

  - `adapter.py` is the interface a platform implements (`platform`, `secret_fields`, `caps`, `test`; later slices add verify, normalise and post) with a registry. Production ships no adapter yet, so creating an install answers `unsupported_platform`; tests register a fake in memory. `run_test` reduces any adapter answer or exception to one of a few codes with fixed sentences, so a platform's own error text never reaches a person, a log or an audit row.

  - `sealing.py` seals `secrets` as one AES-256-GCM blob with the install id as associated data. The key is derived per workspace (HKDF-SHA256) from the operator-provisioned wrapping key named by `COMMONPLACE_WRAPPING_KEY_FILE`, which must be a private regular file outside the data directory; without one, creating or rotating is refused (`sealing_unavailable`) and nothing is stored. `store.BridgeStore.secrets` is the only reader of the blob and no view, route, tool, log line or audit row carries it.

  - `index.py` is the hub-wide index in the control database (`bridge_install_index`: install id, workspace id, platform, team id; no secret, no text). It resolves an intake URL to a workspace later and makes a platform team installable once per hub, across workspaces. `service.py` holds the rules shared by the routes and the command line, and writes one `bridge.install` audit row per change (the action in the tool column, install id, a fixed reason code).

  - `routes.py`: `GET`/`POST /api/bridge/installs`, `PATCH`/`DELETE /api/bridge/installs/<id>` (`enabled`, `label`, `daily_cap`, new `secrets`) and `POST .../<id>/test`, for the signed-in workspace owner only. The public intake paths under `/api/bridge/in/` are routed so they are reachable without a session, and answer 404 for every install until the intake slice.

  - `links.py` is the identity slice (card #455, slice 2). `issue` gives a signed-in member a link code for an enabled install: 12 characters of a 31-letter alphabet, valid 10 minutes, one live code per person and install, returned once and stored only as a salted SHA-256 hash (`bridge_link_codes`). `redeem` is what intake will call with a platform user's text and the kind of message: only a direct message links, the same text in a channel burns the code, and wrong codes are counted per sender in `bridge_link_tries` (five, then a 15-minute lock; a code presented five times that could not finish is dead). `resolve` re-checks on every event that the install is on, the link is live and the person is still a member (`sessions.person_principal`); a link whose person left is ended there, so adding them back does not revive it. Links are one person and one platform id per install (unique in the file); unlinking sets `revoked_at` at once and drops the open prompts of that platform id; a revoked or stale row is replaced when the id or person links again.

  - Routes added: `POST /api/bridge/installs/<id>/link-code`, `GET /api/bridge/links` (own links, plus the enabled installs a member can link to), `DELETE /api/bridge/links/<id>` (own only), and for the owner `GET /api/bridge/installs/<id>/links` (platform id, person, email, still a member, time; no text) and `DELETE /api/bridge/installs/<id>/links/<link>`. Audit rows (`bridge.install`, tool `link_code`, `link`, `unlink`, `unlink_owner`, `link_end`, `code_burned`, `code_locked`) carry ids and fixed codes only, never a code or a platform id.

- **Promises:**

  - Off by default at every level: no install exists until the owner creates one, a new install is disabled, and enabling needs a test that passed (`409 not_tested`). New credentials switch the install off and clear its test. Disabling (route or kill switch) cancels queued posts and open prompts of that install.

  - Anyone who is not the signed-in owner of the workspace (members, agent tokens, other workspaces) gets 404 on every owner route, and an unknown or foreign install id is 404 for the owner too; anonymous callers get 401. A workspace without installs has no `chat_bridge.sqlite3`, and the empty list is the owner's only answer. Deleting an install removes its rows in every bridge table and its index entry.

  - Credentials are write-only: validated against the adapter's `secret_fields`, sealed before they reach the store, never returned (a test greps responses, tool output, logs, audit rows and the raw files for a sentinel token). Platform ids are opaque text; the label is owner-typed (at most 80 characters) and never trusted.

  - Person routes are 404 for agents and tokens and for a workspace with no install, and for an install that is off; another person's link is a 404 like a missing one. A code is never repeated after issue: no response, log line, audit row or file holds it, and the account export writes the code hashes as `[redacted]`. Nothing here adds an agent tool: the tool lists are unchanged.

  - `bridge_status` (owner agent, trusted token, read only) is hidden, like an unknown tool, until the workspace has an install, so a workspace that never used the bridge sees the tool list it always had; once it has one it lists installs with the platform, label, on or off, tested time, mapped channel count and last intake time. No tool installs, maps, links or reads credentials. The command line never accepts credentials and `disable` creates nothing on a hub with no installs.

- **How it fails:**

  - Refusals carry a code and a fixed sentence: `invalid` (400, unknown fields included), `unsupported_platform` (400), `team_taken` (409, the team has an install in this or any workspace of the hub), `not_tested` (409), `sealing_unavailable` (503), `not_found` (404), `busy` (429). A failed test answers 200 with `ok: false` and one of `credentials`, `team_mismatch`, `missing_permission`, `unreachable` or `failed`.

- **Tests:**

  - `tests/test_chat_bridge_identity.py` covers the clean workspace and the off install, a code working once and only in a direct message, expiry, wrong tries and the lock, the sentinel check on a code across responses, logs, audit rows and files, one person and one install per link, unlinking at once, person and owner isolation, membership removal and the install switched off on the next event, deletion and the account export.

  - `tests/test_chat_bridge_foundation.py` covers the clean workspace (every route and the intake, nothing created), the unsupported platform, the sentinel leak check across responses, logs, audit rows and files, sealing (binding to the install, per-workspace keys, a missing, loose or misplaced key), enabling only after a test, rotation, the team unique across workspaces, owner-only access, input validation, delete, the audit rows, the kill switch and status (store, command and tool).
