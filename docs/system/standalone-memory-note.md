### Standalone notes in Memory

Memory offers Add a note in Deck and classic at desktop and phone widths. The
composer is for a standalone workspace note, separate from bot or conversation
memory. Its textarea is the stored source; Preview exact words displays the same
text. No summary, trimming or Unicode normalization is applied. The length limit
is 100,000 Unicode characters; malformed characters are refused rather than
silently removed.

The default destination follows the existing memory_save policy: a configured
write_wing, otherwise shared in the active workspace. Shared means readable by
people with access to that wing; the UI says this explicitly. This uses the
workspace's own memory rather than inventing a private per-person wing that
members could not read under the existing private-wing rules. The optional picker
offers readable, writable wings and their existing rooms, including empty project
wings, with general as the default room. The actual save path still decides the
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
