### Standalone notes in Memory

Memory offers Add a note in Deck and classic at desktop and phone widths. The
composer is for a standalone workspace note, separate from bot or conversation
memory. Its textarea is the stored source; Preview exact words displays the same
text. No summary, trimming or Unicode normalization is applied. The length limit
is 100,000 Unicode characters; malformed characters are refused rather than
silently removed.

These limits apply to both POST /api/memory and the MCP memory_save tool because
both call the same _save helper. Text must be nonempty and at most 100,000 Unicode
characters. NUL and unpaired surrogates are refused rather than removed; tabs,
newlines and carriage returns remain valid and unchanged. Accepted MCP writes
retain their existing saved-id response and exact stored text.

The default destination follows the existing memory_save policy: a configured
write_wing, otherwise shared in the active workspace. Shared means readable by
people with access to that wing; the UI says this explicitly. This uses the
workspace's own memory rather than inventing a private per-person wing that
members could not read under the existing private-wing rules. The optional picker
offers readable, writable active board wings and the default wing, including empty
project wings, with general as the initial room. The actual save path still decides the
destination, including its established privacy routing.

GET /api/memory/rooms?compose=1 adds destination hints. POST /api/memory remains
the only write route and calls the existing memory_save / mempalace_add_drawer
path with the authenticated caller, scope checks and free-space floor. Its save
response adds the actual theme and topic; it does not issue a second write or a
fallible read after a successful save. A plain Saved to line links to
#/memory?drawer=<id>, which opens the existing authorized source reader. Memory
search includes the default workspace wing without requiring a project, including
the first note after reload. Other wings keep the readable room inventory boundary;
authorized composer destinations join that inventory when the composer opens.

Empty and oversized notes produce plain 400 messages. Refused wings and scopes
retain 403; busy/starting and low-storage failures retain retryable 503/507 hints.
The composer keeps failed drafts and destinations for an explicit Save retry;
it never retries a write automatically. Duplicate submission is locked while
saving, and controller shutdown fences late UI updates. A successful save clears
the draft and says the note will be searchable shortly; API acceptance checks
also establish immediate retrieval through search.

Opening GET /api/memory/rooms?compose=1 reads board metadata and policy only. It
never asks the memory worker for taxonomy: a full-palace scan blocked large
workspaces beyond the client's timeout. Selecting a wing explicitly adds wing to
that query and optionally calls the existing authorized mempalace_list_rooms path.
Both its propagated read deadline and HTTP wait are 0.75 seconds; the client allows
1.2 seconds. A failure or late answer leaves general available. Late answers cannot
populate the cache. Successful hints live for 30 seconds in a bounded 256-entry
cache, isolated by gateway, workspace, user, connection, role and policy. Destination
authority is checked before every cache lookup. Two reader slots per gateway limit
stuck transports, and repeated selection coalesces while a reader is still running.
The form opens without requesting enrichment for the default wing.

Loading failures say The places to save could not be loaded. Try again. and show
an explicit Try again action. Save failures keep their existing save-specific text
and preserve the draft. Optional room failures do not prevent saving to general.
The request-path scan audit also found Memory Map wing enumeration, now inside a
one-second read deadline. Connection wing discovery already has a one-second HTTP
bound and propagated deadline; the plain Memory rooms route uses a fixed inventory.

Deck inserts the escaped composer through a function replacement, so literal
dollar replacement patterns in a draft cannot be interpreted as page markup.
The shared _save result has five values: id, linked task, notice, actual wing and
actual room. Its three production consumers are memory_save, POST /api/memory and
meeting-note filing; each unpacks all five values.
