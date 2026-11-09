### Orchestrator config as code

- **The store is the source of truth.**

  - Each board has versioned orchestrator configs (`v<n>`, with parent version, channel, source ref and sha256) in the workspace's `runner.sqlite3`.

  - `.commonplace/orchestrator.toml` in a repo is only a proposal; `orch submit` proposes it and the owner approves its sha256.

- **The owner approves every change.**

  - Wing writers propose; only `orch_owner` accepts, rejects, retires an experiment, forces a lease, voids a step or acknowledges a restore.

  - `orch_owner` is a signed-in person with role owner, or the local CLI (data-dir access).

  - Agent tokens with an owner role are refused.

- **Channels.**

  - `live` and `exp:<name>`.

  - An experiment may change only `prompt`, `workflow`/`workflows`, `topic`, `model_role` and `skills`.

  - Triggers always use live.

  - Only accepted versions run.

- **What is enforced.**

  - For the runner role: the launch message (`SPAWN_MESSAGE` + prompt + workflow steps), `harness = "t3"`, the topic (grant checks), `model_role` (resolved once at insert and pinned on the job), `runner.machine`/`project`/`runtime_mode` (grant checks), `card.todo` triggers, `limits.max_agents` and planner `connections`.

  - Every other role, `skills` and `mcps` are advisory: rendered for sessions to read.

- **Boards without an accepted live `[runner]` config behave exactly as before.**

  - No new columns change their digest, no sweep scans them, no lease exists for them.

  - The tool list does change: the `orch_*` tools are listed on every workspace, so a config can be proposed before one exists (see Residuals).

