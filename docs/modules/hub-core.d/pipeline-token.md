- **Owns:**

  - The `pipeline:write` scope (`policy.PIPELINE_SCOPE`): the deploy script's own token. `ControlDB.create_token` mints it only alone, for a workspace owner, with no parent and at most one live token per workspace; the gateway refuses every request but `POST /api/pipeline/events` to a connection that holds it (403, before any route or rate check), including `/mcp`.
