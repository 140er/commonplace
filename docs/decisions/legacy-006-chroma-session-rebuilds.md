### Chroma session rebuilds

- If a session's cached palace database disappears, invalidate that palace's shared Chroma System before constructing another client.

- Closing only the session owner leaves a System retained by dropped clients; its SQLite pool still targets the renamed database and may fail when it opens a connection.

- The reset uses the existing palace-scoped lifecycle path.

- The collection configuration repair remains unchanged and still adds missing `_type` markers.

