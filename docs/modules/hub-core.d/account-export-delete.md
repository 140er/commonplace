- **Promises:**

  - `WorkerSupervisor.stop(workspace_id)` stops one workspace's worker without touching the others; a later call starts a new one. The account extension is listed in `extensions.MODULES` next to the session module, not at the end, so new extensions can be appended without a merge conflict.
