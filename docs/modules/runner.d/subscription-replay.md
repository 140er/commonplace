- **Owns:**

  - `replay_cli.py`: fixed Claude Code, Codex and Grok Build subscription adapters; isolated CLI settings, auth status checks, CLI JSON token projection, bounded pipe capture and no-output stall detection. The replay worker reuses the runner's machine memory probe.

- **Promises:**

  - Child environments omit provider API keys and routing overrides. Only existing CLI-managed subscription auth is linked, never read or copied by the adapter. No arbitrary flags, API fallback or automatic retries are offered. Tools are disabled, raw output is discarded, and absent usage remains unknown.

  - Grok requires an explicit positive `plan.subscription` boolean from its plan query. Non-empty usage JSON, missing fields and truthy non-booleans do not establish subscription auth.
