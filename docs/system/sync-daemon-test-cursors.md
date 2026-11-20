### Sync daemon test cursors use the production key and journal

The daemon test double keeps its own SQLite database and cursor values for each test.
Its cursor table uses the same `(source, path, native_id)` primary key as the production
state table, so repeated lookups do not scan every synthetic source. Tests still drive
the same source counts and daemon cycles and assert the same cursor, restart and write
budget behavior. A regression test counts SQLite instructions for lookups without
interrupting execution or depending on the host's speed. Every fixture closes its
connection; the lookup regression also removes its progress handler before closing.

The fixture also matches production's WAL journal and `synchronous=FULL` setting.
Rollback-journal reads added repeated filesystem locking to the synthetic backfill
workload. WAL retains real disk-backed persistence while allowing a cursor update
to commit behind a concurrent reader. The regression holds that reader's snapshot
open, verifies the committed update through an independent connection, then ends
the snapshot and verifies the reader sees the update. Every connection is closed.
The source counts, daemon cycles, cursor queries and durability requirements stay
unchanged; production synchronization and check selection are unaffected.
