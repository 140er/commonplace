## meetings

- **Paths:** `commonplace/meetings/`, `tests/test_meetings_foundation.py`
- **Owns:**

  - The foundation of meeting-join bots (`docs/design/meeting-join-bots.md`, slice 1): the workspace file `meetings.sqlite3` (the whole data model of the design note: owner switches, provider connections, people, meetings, participants, segments, notes, chat journal, outbox, events), sealed provider credentials, the owner's switches and connections, each person's opt-in, the kill switch and the read-only `meeting_status` tool. No worker, join, capture, transcription or notes exist yet; later slices add them.

  - `adapter.py` is the provider seam: `Adapter`, `Caps`, the event and frame types, a registry that is empty by default, and `FakeAdapter` (in memory, scripted, records what its `test` saw). `parse_link` and `test` are in use; the lifecycle methods are declared and raise `NotImplementedError` until the lifecycle slice. `seal.py` seals credentials and meeting links with AES-GCM under a per-workspace key derived from the operator's wrapping key file (`COMMONPLACE_MEETINGS_KEY_FILE`, else the file the per-person grants use) with the workspace, the kind and the row id as associated data. `store.py` is the SQLite store, `routes.py` serves `/api/meetings*`, `tools.py` registers `meeting_status`, `cli.py` adds `commonplace meetings status` and `disable (--workspace ID | --all)`.

- **Promises:**

  - Off until configured at every level: with the master switch off (the default, and the state of a workspace that never used meetings) every `/api/meetings*` route except the owner's own settings answers a member 404 and the owner 409 `meetings_disabled`, `meeting_status` is hidden, and reading creates no file. A person who did not opt in cannot invite (403 `not_opted_in`); an invite also needs an enabled connection. The invite route stops there (501 `not_available`) until the lifecycle slice.

  - Credentials and meeting links are write-only: sealed before they are stored, never returned by any route or tool, never in a log line, audit row or error text (adapter error text is replaced by a fixed reason code). If the key file is missing, unreadable, not the service user's or inside the data directory, the write is refused (503 `key_store_unavailable`) and nothing is stored. A new connection is off; it can only be switched on after a passing test (`not_tested` 409); new credentials or a failing test switch it off again; a provider without chat send cannot pass.

  - The owner sees settings, connections without credentials and counts per state, never titles or text; a member sees only whether meetings are on and the limits, and only their own opt-in. Another person, agent token or workspace is 404. `meeting_status` returns the caller's own meetings with state, times and counts, no text.

  - The kill switch (`PUT enabled:false`, or `commonplace meetings disable`) ends every active meeting as cancelled, wipes its join link, deletes its working segments and cancels its queued chat posts; generated notes stay. A workspace without a `meetings.sqlite3` is left untouched.

- **How it fails:**

  - Refusals carry a code: `invalid_field`, `invalid_value`, `invalid_limit`, `invalid_label`, `invalid_secrets`, `invalid_language`, `invalid_wing`, `unknown_provider` (400); `not_opted_in` (403); `meetings_disabled`, `not_tested`, `label_in_use`, `in_use`, `no_connection`, `transcript_not_allowed` (409); `key_store_unavailable` (503); `not_available` (501).

  - Not built yet (later slices): the invite and worker lifecycle, announcement and consent flow, per-day workspace counters, transcription, notes and filing, in-meeting answers, the intake route for provider events and the first real provider adapter.

- **Tests:**

  - `tests/test_meetings_foundation.py` covers the clean-workspace case (every route, every role, no file, no tool), the owner switch and its bounds, the test-before-enable rule, sealing (binding, tamper, missing or misplaced key), a sentinel-token leak check across responses, logs, audit rows and the database file, owner-only routes, person opt-in and validation, the kill switch over HTTP and CLI, `meeting_status` and the fake adapter.
