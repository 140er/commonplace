# Compact workspace store

The default remains thread store v1 with Chroma. A workspace opts in through
an offline administrative transition:

```sh
commonplace workspace store <workspace> compact
commonplace doctor --workspace <workspace>
commonplace workspace store <workspace> v1
```

This administrative workflow requires Linux for offline process verification.
Stop the gateway first. The transition takes the exclusive data-directory lock,
checks for orphan workers and holds the palace writer lease throughout. Writers
are refused while it runs. Restart the gateway after success or failure; a
process crash releases the locks automatically. This implementation uses the
RFC's lock-held path, so no concurrent write-log replay is needed.

Configure `COMMONPLACE_BACKUP_CMD` as an executable command with arguments. It
runs without a shell. The command receives `COMMONPLACE_WORKSPACE_DIR` (the
resolved active generation), `COMMONPLACE_WORKSPACE_ID` and a fresh
`COMMONPLACE_BACKUP_NONCE`. Back up `COMMONPLACE_WORKSPACE_DIR`; the stable
workspace container can hold a retired store. `commonplace backup` acquires
the same exclusive lock, so it cannot run inside this command. The command
must complete an off-host backup and print exactly one JSON object:

```json
{"completed_at":"2026-01-01T12:00:00.123456+00:00","off_host":true,"nonce":"<COMMONPLACE_BACKUP_NONCE>"}
```

The command must exit zero and echo the nonce from its environment. Its completion
timestamp must include a timezone, be at least the command start time and be
newer than every workspace data write. A failed command, stale timestamp, missing
receipt, local-only receipt or changed source refuses the transition. The receipt
and command stderr are recorded locally; stderr is kept in the private
`.store-backup.stderr.log` file. The backup command owns the off-host destination;
the transition never creates a backup on the workspace host. Migration staging
directories are temporary working stores, not backup destinations.

`workspace.json` in the active generation holds the settings:

```json
{"store":"compact","compact_max_collection_records":100000}
```

The administrative command publishes `store` only after verification. Do not
change it directly on an existing workspace. The worker writes `backend` into
its own configuration, which takes precedence over `MEMPALACE_BACKEND`, and
handoff uses `threads.v2.sqlite3`. The collection limit defaults to 100,000;
raise it only after the compact hot-path prerequisites have been validated.

Before backup, the transition checks source format, collection size, rollback
representability, re-embedding count and available disk space. The space estimate
allows four generations/snapshots plus SQLite overhead and accounts for expansion
when rolling compact data back. Insufficient space refuses the operation.

The transition builds a complete sibling generation, copying threads row by
row with their original ids, append-time chunk boundaries, parent chains,
handoff documents and extraction progress. Every message field, chunk text and
digest is checked. Every palace collection is copied without embedding its
existing vectors, then checked by id, metadata, document text and vector error
bound. Knowledge graph, logstream, replica state and sidecars are carried over.
SQLite sidecars include their committed WAL contents. Boards, rules, settings,
worker configuration and home files are also verified against the live source
using read-only SQLite comparisons or file hashes. Chroma is opened only on
a staging snapshot so its recovery cannot change the live palace.

Before publishing compact, a disposable staging generation receives a new
thread, a uniquely named probe collection and a keyword-only drawer in the real
drawers collection. It rolls back and verifies all records and threads. The
keyword probe exercises the workspace embedder at the stored vector dimension;
a model or dimension mismatch refuses the transition. An atomic,
durable generation pointer then publishes palace, threads, boards and settings
together. Interruptions before that pointer leave the previous generation
active. Interruptions afterwards leave the fully verified generation active.
A failed operation removes its unpublished staging copies immediately. After a
process crash, rerunning either store command or pruning removes orphan staging
copies and resets the interrupted backfill state. Cleanup validates the generation
pointer first. If it is missing or invalid, only the state file's provably
unpublished staging copy can be removed; other generations are preserved and
the operation refuses until the pointer is repaired. Workers, the gateway and
doctor also refuse to resolve an absent or invalid pointer when sibling
generations exist, so they cannot resume writing into the original root.
Completed retained generations are preserved. With a valid pointer, cleanup
preserves and reports unlisted generations carrying `.store-comparison.json`;
named retained generations follow the fourteen-day and fingerprint checks.

Every administrative pointer update also atomically records a recovery copy in
`.store-pointer.prev.json` at the stable workspace root. To repair a lost pointer,
stop the gateway, preserve all generation directories and inspect that copy or
the verified off-host backup. Confirm the candidate's active generation exists
and contains the latest writes before atomically restoring `.store-pointer.json`.
A crash between the two writes can leave the recovery copy one update behind;
it is a recovery candidate, never an automatic fallback. Restore the full retained
list rather than writing a minimal pointer by hand. Unlisted published generations
are preserved until their retirement evidence is recovered and checked.

If `.store-backfill.json` is corrupt, switching refuses with a repair hint.
Pruning instead returns a read-only generation report and changes no workspace
files. Repair the state from verified evidence before retrying destructive cleanup.

A sample of live thread reads, palace reads and searches is compared with the
retained store. Comparison logs contain only match status and fixed diagnostic
labels. Queries,
content, reusable content hashes and exception messages are never logged.
Palace comparison is skipped when the retained backend is Chroma, whose client
can mutate files during reads. Thread comparisons remain enabled. Ranking
differences and new writes can produce mismatches without interrupting the live read.

Rollback copies the current compact generation back, including writes made
since the switch. Keyword-only records need the workspace's configured embedder:
the command logs their count and a rough estimate before embedding them, removes
the keyword-only marker and preserves document text. An unavailable embedder
refuses rollback. The estimate assumes 50 milliseconds per record and excludes
model startup. Threads or handoff documents containing text v1 cannot represent,
including lone surrogates, refuse rollback with counts. Compact stays active
with every byte intact.

Retired generations have their data files made read-only and are retained for at
least fourteen days. An older binary can restore write permissions, so pruning
also checks for writes newer than retirement and compares a retirement content
fingerprint, detecting changes even within the filesystem timestamp resolution.
A changed generation or missing retirement evidence refuses pruning. If pruning
refuses, keep that generation intact, stop the gateway and compare it with the
active generation and the off-host backup. Recover any missing records before
discarding anything. Restoring files can reset their timestamps: a quiet timestamp
check in `doctor` does not prove that restored content matches retirement. Do not
reset the retirement timestamp or fingerprint to bypass refusal. Preserve the
recovered generation in a verified off-host backup before explicitly removing it;
a later prune drops entries for generations already removed. Run the offline
retention command periodically:

```sh
commonplace workspace prune-stores <workspace>
```

Pruning removes abandoned staging directories and never removes the active
generation. Existing off-host backups follow their own retention policy. `doctor` reports the selected format, thread index version,
palace schema version, backfill state, active path and retained generations older
than fourteen days for each workspace. Doctor uses timestamps without hashing
retained content; pruning checks fingerprints only after the retention period.

To restore from an off-host backup, stop the gateway and prepare a fresh data
directory with the matching control database and workspace id. Restore the
backed-up active generation's contents into `workspaces/<id>` there, preserving
`workspace.json`, palace files, threads and sidecars. A single-generation restore
uses that directory directly and needs no generation pointer; a full data-tree
restore must preserve both the pointer and every generation it names. Verify
the backup manifest and run `commonplace --data <restored-data> doctor` before
starting the gateway against the restored tree. Keep the previous data tree
intact for recovery and its normal retention period.
