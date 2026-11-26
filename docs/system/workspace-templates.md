### Workspace templates

An owner can export a chosen subset of a workspace (orchestrator roles and skill references, seats,
routines, goals, optionally an orchestrator config proposal and, only on explicit request, verbatim
memory) as a versioned JSON package (`docs/workspace-templates.md`). Sections are built from
allow-lists, so tokens, connection names, machine bindings, runner and trigger grants and row ids never
leave; free text is redacted with `commonplace.redact` and the export fails closed on a remaining secret
shape. `commonplace template preview` reads a target workspace and lists collisions (never overwritten)
and dropped fields, and refuses a tampered or secret-bearing package; an imported orchestrator config can
only ever be a proposal that an owner accepts separately. Slice 1 writes nothing to any workspace.
