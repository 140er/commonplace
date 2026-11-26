# Workspace templates

An owner-chosen subset of one workspace exports to a JSON package that another workspace can
later import. Slice 1 (this document's scope) is the plan, the export and the import preview;
nothing writes to a workspace.

Not account export (`docs/modules/account-data.md`): that is the whole account for its owner;
a template is a small, shareable, secret-free starting point. The idea of portable
organisation setups is the only thing taken from public "company portability" designs; no
code is copied.

## Package

`commonplace-workspace-template`, integer `version` 1 (a reader refuses other versions):

```
{"format", "version", "sections", "memory_included", "stripped", "redactions",
 "content": {<section>: [...]}, "digest": sha256 of the canonical content}
```

## Sections and where they are read

| Section | Read from | Exported fields |
| --- | --- | --- |
| `roles` | accepted live orchestrator config of `--board` (`runner.sqlite3`) | name, kind, parent, harness, topic, model_role, workflow, prompt, area, skills |
| `skills` | the same config's role skill references | id, version (no skill content) |
| `seats` | `project_orchestrators` (control db) | role, name (no connection id) |
| `routines` | `routines` (control db), not deleted | name, template, schedule, timezone, state (no owner, cursor, runs) |
| `goals` | `goals` (`boards.sqlite3`) | ref (renumbered), parent ref, title, detail, archived |
| `orch_config` (opt-in) | the same accepted config | schema, limits, roles, workflows as a **proposal only** |
| `memory` (opt-in) | none wired in slice 1 | wing, room, text, verbatim |

Default sections: roles, skills, routines, goals. `memory` needs the section **and**
`--include-memory`, on export and on preview; slice 1 has no memory reader, so the CLI refuses it.

## Secrets

Sections are built from allow-lists, so connection names, machine bindings, `[runner]`,
triggers, MCP urls and `secret_ref`s, user ids and row ids are absent by construction
(`stripped` names the classes). Free text then goes through `commonplace.redact`
(`find_kinds` for detection of any length, `redact_tail` for replacement): a credential-shaped
value becomes its marker and is listed in `redactions` by path and kind. Keys that look like
credentials (`redact_value`) are treated the same. Verbatim memory is never rewritten: a hit
refuses the export. Key text is scanned too (a credential-shaped key is renamed to its marker on export, refused on preview, and shown as `<key>` in paths). A final scan fails closed. The scanner is deliberately broad, so ordinary
prose such as `auth: OAuth` can be redacted; that errs toward leaving text out.

## Preview and collision rules

`preview(package, target)` reads the target and reports, never writes:

- **refused**: not a package, other format or version, digest mismatch, memory present but not
  declared, any credential-shaped value or key (path and kind only, never the text), board
  sections without a target board, unparseable target config.
- **collisions** (resolution `skip`; nothing is overwritten): roles by name, seats by role,
  routines by name (case-insensitive), goals by title path (case-insensitive), `orch_config`
  when the target board already has a live config.
- **dropped**: fields outside the allow-list (`not_importable`), unknown sections,
  runner/triggers/mcps in a proposal (`needs_owner_acceptance`), export redactions, memory
  without the opt-in.
- **proposals**: an `orch_config` is always `proposal_only`; **references**: skill ids.

Entry points: `commonplace template export|preview` (owner of the workspace only).

## Later slices

1. Import write path: creates goals, routines and seats from a clean preview, renames on
   collision on request, files the orch config as a *proposed* version (owner accepts it
   separately), never a live config or runner grant.
2. App UI: pick sections, show the preview, confirm.
3. Fresh-user two-workspace test: export from one throwaway workspace, preview and import into
   another, check nothing but the chosen sections arrived.
4. Memory reader for the opt-in section.
