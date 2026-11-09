### Independent system decisions

Independent decisions live in separate, stable `docs/decisions/card-<number>-<topic>.md`
files, with no shared index insertion. Files are read in filename order. Existing
Decisions-section text is retained verbatim in ordered `legacy-*` files;
`SYSTEM.md` remains the entry point and retains numbers, stores, residuals and
base-approved waivers. Legacy system edits remain accepted during transition.

Docs-check requires a substantive system edit or a direct Markdown decision file
with a `###` title and body. A deletion, empty stub, whitespace edit or unrelated
document cannot satisfy the obligation. Decision files, like the system document,
do not themselves require a module-block update. Changed implementation paths
still require their owning module blocks to change; waiver authorization remains
bound to the merge base. The checker validates structure and changed text, while
review remains responsible for accuracy and relevance to the behavior change.

The hermetic regression fixture creates two branches with separate decisions and
separate owning-module changes, merges them with Git and checks both obligations.
It also covers missing and invalid updates, module obligations, legacy acceptance
and byte-for-byte preservation of the migrated Decisions section.
