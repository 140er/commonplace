- **Owns:**

  - `account/tables.py` classifies `coworker_model_attempts` (a per-person, per-workspace model quota table) as the person's and the workspace's, like `bot_model_calls`, so export and deletion no longer stop on an unclassified table.
