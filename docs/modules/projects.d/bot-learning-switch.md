- **Owns:**

  - The router hands `GET` and `PATCH /api/projects/<slug>/learning` to `bots/learned_routes.py` (the project's shared-learning switch for bots, a workspace-owner action; see the `bots` module block), and `PATCH` is now a registered method of the `/api/projects/` prefix; any other `PATCH` there is still 404.
