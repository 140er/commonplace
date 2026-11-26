## workspace-templates

- **Paths:** `commonplace/workspace_template.py`, `commonplace/workspace_template_cli.py`, `docs/workspace-templates.md`, `tests/test_workspace_template.py`
- **Owns:**

  - Exportable workspace templates: the versioned package, the section readers (orchestrator roles and skill references, seats, routines, goals), secret stripping and the read-only import preview. `commonplace template export|preview`.

- **Promises:**

  - Sections come from allow-lists, so tokens, connection names, machine bindings, runner and trigger grants and row ids never enter a package; credential-shaped free text is replaced by its marker with the path listed, and the export fails closed if a secret shape remains.

  - Verbatim memory is out unless its section is named and `include_memory` is set, and it is never rewritten (a secret-shaped drawer refuses the export).

  - `preview` writes nothing: it lists collisions (resolution `skip`) and dropped fields, treats an orchestrator config only as a proposal and refuses a tampered, foreign-format, newer-version or secret-bearing package without echoing the secret.

- **How it fails:**

  - Refusals carry a stable code (`memory_needs_opt_in`, `memory_contains_secret`, `memory_unavailable`, `no_live_config`, `package_too_large`, `package_invalid`, `store_unreadable`) and never contain package or workspace text. A locked or unreadable store is an error (`store_unreadable`), never an empty section; a missing table reads as empty. A malformed package (wrong section shapes, missing keys) is refused with `package_invalid`, not a crash.

- **Tests:**

  - `tests/test_workspace_template.py` covers export without tokens, collision and dropped-field listing, a planted token refused, memory opt-in, real-store reading with a byte-identical target and the CLI.
