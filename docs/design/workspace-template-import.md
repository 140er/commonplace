# Workspace template import (slice 2 of card #368)

The import write path of `docs/workspace-templates.md`. The package, the sections, the secret
rules and the preview are described there; this document covers what the preview adds for the
import and what the import writes.

## Preview additions

- **routine_invalid / routine_board_missing** (refused): a routine whose name, template or
  schedule the routines code would refuse, or with neither its own board nor the target board
  present in the target workspace.
- **proposal_invalid** (refused): the proposed orchestrator config would not parse.
- **preview_id**: sha256 over the package digest, target board, memory flag and the whole
  result (what would be created, collisions, dropped fields, proposals, references). Printed
  by a preview that is `ok`; the import step requires it.

## Import

`commonplace template import FILE --workspace W --board B --preview-id ID` (owner of the target
workspace only; `commonplace/workspace_template_import.py`).

1. The actor must own the target (`owner_only`). The package is scanned for credentials again,
   independently of the preview (`secret_found`, paths only). `--include-memory` is refused
   (`memory_unavailable`: there is no memory writer).
2. The preview is recomputed against the target as it is now. A preview that is not `ok` is
   `preview_refused`; an `ID` that differs is `preview_stale`, so a changed package or a changed
   workspace is never applied blindly.
3. Writes, in order: goals (one `boards.sqlite3` transaction), routines and seats (one control
   database transaction), then one orchestrator config **proposal** (`runner.sqlite3`).
4. The stores are separate files. If a later step fails, the rows this import created in the
   earlier ones are removed again; nothing else is touched. Existing rows are never updated or
   deleted.

What lands where:

| Section | Result |
| --- | --- |
| `goals` | created under their (existing or new) parent; a title path that exists is skipped and new children attach to the existing goal |
| `routines` | created with the exported schedule and state, owned by the importing owner; the card template board stays when the target has that board, else becomes the target board; a name that exists (case-insensitive) is skipped |
| `seats` | created unheld (no connection); a role that exists is skipped |
| `roles`, `skills`, `orch_config` | one **proposed** config version for the target board: the target's live config plus the roles it lacks (or a fresh config, with the exported workflows and limits, when the board has none). Roles the target has are never changed. A role's `workflow` that the merged config lacks is dropped (`workflow_unresolved`). The proposal is never accepted by import; an owner accepts it by its sha256 (`orch_config_accept`). An identical open proposal is skipped. |
| `memory` | never imported |

Runner, trigger, MCP and fleet settings, connection names and machine bindings never come from
a package, and no runner grant or token is created. The result lists `applied`, `skipped`
(the collisions), `dropped` and the `proposal` (board, ref, sha256). Collision resolution is
`skip` only: nothing is renamed or overwritten.

Entry points: `commonplace template export|preview|import`. The app UI is a later slice.

## Later

1. App UI: pick sections, show the preview, confirm with its `preview_id`.
2. Memory reader and writer for the opt-in section.
