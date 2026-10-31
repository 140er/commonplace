# Compact workspace store

The default remains thread store v1 with Chroma. A workspace opts in through
an offline administrative transition:

```sh
commonplace workspace store <workspace> compact
commonplace doctor --workspace <workspace>
commonplace workspace store <workspace> v1
```

Stop the gateway first. The transition takes the exclusive data-directory lock,
checks for orphan workers and holds the palace writer lease throughout. Writers
are refused while it runs. Restart the gateway after success or failure; a
process crash releases the locks automatically. This implementation uses the
RFC's lock-held path, so no concurrent write-log replay is needed.

Configure `COMMONPLACE_BACKUP_CMD` as an executable command with arguments. It
runs without a shell. The command receives `COMMONPLACE_WORKSPACE_DIR` (the
resolved active generation) and `COMMONPLACE_WORKSPACE_ID`. It must complete
an off-host backup and print exactly one JSON object:

```json
{"completed_at":"2026-01-01T12:00:00.123456+00:00","off_host":true}
```

The command must exit zero. The timestamp must include a timezone and be newer
than every workspace data write. A failed command, stale timestamp, missing
receipt, local-only receipt or changed source refuses the transition. Only the
receipt is recorded locally. The backup command owns the off-host destination;
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

The transition builds a complete sibling generation, copying threads row by
row with their original ids, append-time chunk boundaries, parent chains,
handoff documents and extraction progress. Every message field, chunk text and
digest is checked. Every palace collection is copied without embedding its
existing vectors, then checked by id, metadata, document text and vector error
bound. Knowledge graph, logstream, replica state and sidecars are carried over.
SQLite sidecars include their committed WAL contents. Chroma is opened only on
a staging snapshot so its recovery cannot change the live palace.

Before publishing compact, a disposable staging generation receives a new
thread and drawer, rolls back and verifies all records and threads. An atomic,
durable generation pointer then publishes palace, threads, boards and settings
together. Interruptions before that pointer leave the previous generation
active. Interruptions afterwards leave the fully verified generation active.
Rerunning the command discards incomplete staging work and retries safely.

A sample of live thread reads, palace reads and searches is compared with the
retained store. Comparison logs contain hashes and fixed diagnostic labels;
queries, content and exception messages are never logged. Ranking differences
and new writes can produce different hashes without interrupting the live read.

Rollback copies the current compact generation back, including writes made
since the switch. Keyword-only records need the workspace's configured embedder:
the command logs their count and a rough estimate before embedding them, removes
the keyword-only marker and preserves document text. An unavailable embedder
refuses rollback. The estimate assumes 50 milliseconds per record and excludes
model startup. Threads or handoff documents containing text v1 cannot represent,
including lone surrogates, refuse rollback with counts. Compact stays active
with every byte intact.

Old generations are retained for at least fourteen days. Run the offline
retention command periodically:

```sh
commonplace workspace prune-stores <workspace>
```

It never removes the active generation. Existing off-host backups follow their
own retention policy. `doctor` reports the selected format, thread index version,
palace schema version and backfill state for each workspace.
