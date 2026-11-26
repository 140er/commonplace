## workspace-templates

- **Paths:** `commonplace/workspace_template.py`, `commonplace/workspace_template_cli.py`, `commonplace/workspace_template_import.py`, `docs/workspace-templates.md`, `docs/design/workspace-template-import.md`, `tests/test_workspace_template.py`, `tests/test_workspace_template_import.py`
- **Owns:**

  - Exportable workspace templates: the versioned package, the section readers (orchestrator roles and skill references, seats, routines, goals), secret stripping and the read-only import preview and the confirmed import write path. `commonplace template export|preview|import`.

- **Promises:**

  - Sections come from allow-lists, so tokens, connection names, machine bindings, runner and trigger grants and row ids never enter a package; credential-shaped free text is replaced by its marker with the path listed, and the export fails closed if a secret shape remains.

  - Verbatim memory is out unless its section is named and `include_memory` is set, and it is never rewritten (a secret-shaped drawer refuses the export).

  - `preview` writes nothing: it lists collisions (resolution `skip`) and dropped fields, treats an orchestrator config only as a proposal and refuses a tampered, foreign-format, newer-version or secret-bearing package without echoing the secret.

  - `import` writes only after the owner of the target confirms the exact preview (`preview_id`): goals, routines and seats are created, roles, skills and any orchestrator config are filed as a proposed config version (never accepted, never a runner grant), existing rows are never changed or deleted, and a failure part-way removes only what the import created.

  - The package is scanned for credentials again at write time, independently of the preview.

- **How it fails:**

  - Refusals carry a stable code (`memory_needs_opt_in`, `memory_contains_secret`, `memory_unavailable`, `no_live_config`, `package_too_large`, `package_invalid`, `store_unreadable`, `preview_stale`, `preview_refused`, `owner_only`, `secret_found`, `routine_board_missing`, `proposal_invalid`) and never contain package or workspace text. A locked or unreadable store is an error (`store_unreadable`), never an empty section; a missing table reads as empty. A malformed package (wrong section shapes, missing keys) is refused with `package_invalid`, not a crash.

- **Tests:**

  - `tests/test_workspace_template_import.py` exports from one workspace and imports into a fresh second workspace of the same user (same goals, routines, seats and roles, no tokens, config only proposed), and covers stale-preview refusal, non-owner refusal, a planted token refused at write time, rollback on a mid-import failure and the CLI.

  - `tests/test_workspace_template.py` covers export without tokens, collision and dropped-field listing, a planted token refused, memory opt-in, real-store reading with a byte-identical target and the CLI.
