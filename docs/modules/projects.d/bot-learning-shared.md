- **Owns:**

  - The router also hands `GET /api/projects/<slug>/learned` (the shared learned items of a project, for members who can read it; see the `bots` module block) to `bots/learned_routes.py`; no other method or path is added there.
