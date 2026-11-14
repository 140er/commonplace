### Deck Memory presentation and query restoration

Deck Memory uses the existing server-provided readable board inventory as its
room filter and retains the existing scoped search, source actions and import
endpoints. Rows display exact quote characters with readable author and room
names; unresolved author identifiers become an unknown-author label. Source
identifiers appear only inside a closed details disclosure. Presentation never
changes the stored words or grants read or write access.

The current query is kept in browser session storage under both the signed-in
person and workspace. A restored nonblank query alone shows “Kept from last
time”; editing or submitting clears that note. Refused storage leaves search
usable. Query persistence does not persist results or change server authority.

The Deck importer is a dialog around the existing choose, preview, confirm and
report stages. Confirmation additionally requires the explicit ownership
checkbox after preview; selecting a new file clears that consent. The upload
bytes, supported formats, destination permissions, preview binding and retry
semantics remain unchanged. Classic rendering and import behavior are retained.
