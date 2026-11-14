### Board store opens take no write lock (#428)

- `BoardStore(...)` migrates a file once per process and then only reads to decide that it is current, so the trigger sweep, the stall check and gateway reads never queue for the boards write lock. All boards connections use a 10 s busy timeout; a short one is an explicit caller choice (deploy migration). SQLite failures log their error name and code, plus the message only when it is a fixed SQLite text.
