## chat-bridge

- **Paths:** `commonplace/chat_bridge/`, `tests/test_chat_bridge_foundation.py`
- **Owns:**

  - The foundation of the chat-platform bridge (`docs/design/chat-platform-bots.md`, card #455, slice 1): the workspace file `chat_bridge.sqlite3` (the whole schema of the note; opened like `bots.sqlite3`, created only by creating an install, so a read, an unknown id or an intake request never makes it), the install rows, sealed credentials, the owner install routes, the adapter's `test` seam, the read-only `bridge_status` tool and the kill switch (`commonplace bridge status|disable`). The package is `commonplace/chat_bridge/` because `commonplace/bridge.py` is the MCP client bridge.

  - `adapter.py` is the interface a platform implements (`platform`, `secret_fields`, `caps`, `test`; later slices add verify, normalise and post) with a registry. Production ships no adapter yet, so creating an install answers `unsupported_platform`; tests register a fake in memory. `run_test` reduces any adapter answer or exception to one of a few codes with fixed sentences, so a platform's own error text never reaches a person, a log or an audit row.

  - `sealing.py` seals `secrets` as one AES-256-GCM blob with the install id as associated data. The key is derived per workspace (HKDF-SHA256) from the operator-provisioned wrapping key named by `COMMONPLACE_WRAPPING_KEY_FILE`, which must be a private regular file outside the data directory; without one, creating or rotating is refused (`sealing_unavailable`) and nothing is stored. `store.BridgeStore.secrets` is the only reader of the blob and no view, route, tool, log line or audit row carries it.

  - `index.py` is the hub-wide index in the control database (`bridge_install_index`: install id, workspace id, platform, team id; no secret, no text). It resolves an intake URL to a workspace later and makes a platform team installable once per hub, across workspaces. `service.py` holds the rules shared by the routes and the command line, and writes one `bridge.install` audit row per change (the action in the tool column, install id, a fixed reason code).

  - `routes.py`: `GET`/`POST /api/bridge/installs`, `PATCH`/`DELETE /api/bridge/installs/<id>` (`enabled`, `label`, `daily_cap`, new `secrets`) and `POST .../<id>/test`, for the signed-in workspace owner only. The public intake paths under `/api/bridge/in/` are routed so they are reachable without a session, and answer 404 for every install until the intake slice.

- **Promises:**

  - Off by default at every level: no install exists until the owner creates one, a new install is disabled, and enabling needs a test that passed (`409 not_tested`). New credentials switch the install off and clear its test. Disabling (route or kill switch) cancels queued posts and open prompts of that install.

  - Anyone who is not the signed-in owner of the workspace (members, agent tokens, other workspaces) gets 404 on every owner route, and an unknown or foreign install id is 404 for the owner too; anonymous callers get 401. A workspace without installs has no `chat_bridge.sqlite3`, and the empty list is the owner's only answer. Deleting an install removes its rows in every bridge table and its index entry.

  - Credentials are write-only: validated against the adapter's `secret_fields`, sealed before they reach the store, never returned (a test greps responses, tool output, logs, audit rows and the raw files for a sentinel token). Platform ids are opaque text; the label is owner-typed (at most 80 characters) and never trusted.

  - `bridge_status` (owner agent, trusted token, read only) lists installs with the platform, label, on or off, tested time, mapped channel count and last intake time. No tool installs, maps, links or reads credentials. The command line never accepts credentials and `disable` creates nothing on a hub with no installs.

- **How it fails:**

  - Refusals carry a code and a fixed sentence: `invalid` (400, unknown fields included), `unsupported_platform` (400), `team_taken` (409, the team has an install in this or any workspace of the hub), `not_tested` (409), `sealing_unavailable` (503), `not_found` (404), `busy` (429). A failed test answers 200 with `ok: false` and one of `credentials`, `team_mismatch`, `missing_permission`, `unreachable` or `failed`.

- **Tests:**

  - `tests/test_chat_bridge_foundation.py` covers the clean workspace (every route and the intake, nothing created), the unsupported platform, the sentinel leak check across responses, logs, audit rows and files, sealing (binding to the install, per-workspace keys, a missing, loose or misplaced key), enabling only after a test, rotation, the team unique across workspaces, owner-only access, input validation, delete, the audit rows, the kill switch and status (store, command and tool).
