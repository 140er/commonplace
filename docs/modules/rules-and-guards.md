## rules-and-guards

- **Paths:** `commonplace/rules/`, `commonplace/guards/`, `commonplace/privacy.py`, `commonplace/redact.py`
- **Owns:**

  - Canonical Space create in the checked REST write inventory.

  - The project panel write `PUT /api/projects/<slug>/panel` (`project_panel_set`) in the checked REST write inventory.

  - Owner-reviewed rules and their freshness gate, guard building blocks, sensitive-text flags and redaction.

  - The pre-push quick check (`guards/prepush.py`): changed-file selection, the shared time budget and the report of what it skipped; and the hook installer (`guards/hooks.py`) whose scripts resolve each worktree's own environment.

  - `commonplace.guards.pretool`, a PreToolUse command that refuses git-hook bypasses (`docs/hook-bypass-guard.md`).

- **Promises:**

  - Split completion markers are checked writes on MCP and REST.
  - Canonical create uses header freshness while legacy routes retain body compatibility.

  - Rules take effect only after owner review; writes from a client with stale rules get 428; flags are deterministic.
  - The bypass guard denies hook-skipping shell commands and edits of existing hook files with a reason, passes look-alike text, and never auto-approves.

  - `rules_get` takes `keys=[...]` for selected rules in full (unknown keys named) and, with `effective=true`, `format=index` for the compact index. Both are partial reads: they never record the full set as served and give no `rules_seq`. Defaults are unchanged.

  - The quick check names itself a quick check, reports every skipped or cut-off step, and fails the push only when a check it ran failed. The installed hooks work in a fresh linked worktree or say how to set it up.

- **How it fails:**

  - Missing and stale served sequences retain the 428 refresh payload.

  - Redaction can miss a sensitive detail; flags are hints, not blocks.

  - A test whose name matches no changed module is not selected, so the quick check can pass while a distant test would fail; a cut-off step is reported, not retried; a worktree with no environment blocks the hook with the setup command.

- **Tests:**

  - Route-aware checked REST harness and Space freshness contract tests.

  - `tests/test_rules_index.py` pins index completeness and size, keyed get with unknown keys, partial reads not serving, and unchanged defaults; `tests/spaces_surfaces.json` carries the new `rules_get` and `session_start` arguments.

  - `tests/test_commonplace_rules.py`, `tests/test_commonplace_rules_enforce.py`, `tests/test_commonplace_privacy.py`, the `tests/test_guards_` files.

  - `tests/test_rules_enforcement_points.py` holds one skip-detecting check per enforcement point (injection, session hook, write sequence check, reviewers); `docs/design/RULES.md` maps each point to its tests.

  - `tests/test_guards_prepush.py` covers test selection, the budget (cut-off lint, cut-off tests, skipped steps), docs-check and the per-worktree tool path; `tests/test_guards_hooks.py` installs the hooks into a clone and pushes and commits from fresh linked worktrees.
