### Workspace templates

An owner can export a chosen subset of a workspace (orchestrator roles and skill references, seats,
routines, goals, optionally an orchestrator config proposal and, only on explicit request, verbatim
memory) as a versioned JSON package (`docs/workspace-templates.md`). Sections are built from
allow-lists, so tokens, connection names, machine bindings, runner and trigger grants and row ids never
leave; free text is redacted with `commonplace.redact` and the export fails closed on a remaining secret
shape. `commonplace template preview` reads a target workspace and lists collisions (never overwritten)
and dropped fields, and refuses a tampered or secret-bearing package; an imported orchestrator config can
only ever be a proposal that an owner accepts separately. `commonplace template import` applies a package only with the `preview_id` of a preview of that exact package against the target as it is now, only for the target's owner, and never overwrites: goals, routines and seats are created, roles, skills and any config are filed as a proposed version, and a failure part-way removes what the import created.
