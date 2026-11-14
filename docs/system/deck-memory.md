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

Room counts are derived from the matches already returned by the existing scoped
search. Known counts may be retained while changing rooms within that same query
and source/author/time filter; new queries do not reuse those counts. This adds no
new search or permission path. The viewer sees “you” as their saver name, and
missing room display names are presented as readable words instead of raw slugs.
The import preview retains the label picked in the chooser, independently of the
parser’s canonical source field. Consent and confirmation payloads are unchanged.

Phone room chips and More filters share a horizontal scroll area, with a 16px
right fade and the result count on the next line. The advanced-filter popup stays
outside that area's clipping boundary. Import dialogs use the opaque background
token rather than the adjustable card surface, so underlying quotes cannot show
through either step. On phones the import dialog is a full-width sheet resting on the tab bar rather than a centred dialog: a centred dialog left the actions an arbitrary distance (about 12px in a short viewport) above the tabs and the content 38px in from the screen edge. The sheet gives a 16px gutter and 24px below the actions, which is the spacing the import design asks for, and its title is 24px and visible (the phone shell hides page headings, but not those inside a dialog). Desktop keeps the centred dialog. The sheet title is checked in a real browser at 390x667 as well as 390x844, because the short viewport is where the sheet scrolls and a clipped or hidden title would show first. These presentation changes retain the existing consent,
preview binding and import security behavior.
