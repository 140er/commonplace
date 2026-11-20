### Sync daemon test cursors use the production composite key

The daemon test double keeps its own SQLite database and cursor values for each test.
Its cursor table uses the same `(source, path, native_id)` primary key as the production
state table, so repeated lookups do not scan every synthetic source. Tests still drive
the same source counts and daemon cycles and assert the same cursor, restart and write
budget behavior. A regression test counts SQLite instructions for lookups without
interrupting execution or depending on the host's speed. Every fixture closes its
connection; the lookup regression also removes its progress handler before closing.
