### Memory import destination rules

- One table in `commonplace/import_sources.py` (`DESTINATIONS`, `EXTENSIONS`) says which destination takes which source: workspace conversations take the conversation sources, your private imported history also takes `native-memory` (`.md` and `.txt`). `prepare` in `import_files.py` and `POST /api/import` check every import against it, so an incompatible pair is still refused with 403.
- `GET /api/memory/import/rules` (signed-in person session) returns `destinations` (`scope`, `sources`, `available`, and a `reason` when private history is unavailable to this person or workspace) and `extensions` per source. The web form uses it to choose a compatible destination before Preview.
