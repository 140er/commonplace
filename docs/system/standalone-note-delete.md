### Owner deletion of standalone notes

The Memory source panel offers Delete this note only for newly saved standalone
notes from Add a note, with hub_origin=standalone-note-v1 stamped through the
trusted worker identity. Older drawers are not migrated or inferred from their
words. Imported threads, transcripts, mined files and source-backed drawers stay
outside this action. Duplicate saves retain the first record's provenance and
cannot turn an old source into a deletable note. Physical chunk ids cannot be
used to remove part of a logical note.

POST /api/memory/<id>/delete requires the current workspace owner in a person
browser session, a session cookie, JSON, an explicit same-origin Origin and a
memory_delete_csrf proof bound to that session and drawer. GET /api/memory/<id>
and search responses offer delete_note only to that authorized browser owner for
a writable wing. Members and agent, connection, OAuth and pipeline credentials
receive no offer and cannot use the route, even if a token holds memory:delete.
The owner does not gain write access to an otherwise forbidden wing.

Deletion calls the existing mempalace_delete_drawer policy and worker path. The
worker rechecks standalone-note eligibility immediately before removal. It uses
the existing delete_drawer WAL line, physical and logical drawer removal, search
index updates and derived-index cleanup. There is no parallel storage deletion
implementation. The app additionally checks the deployed free-space floor before
the delete write; low storage returns retryable 507 and a backup pause returns
retryable 503. Unknown ids and second deletes return 404 with a plain message.

The audit_log contract is an authenticated user id, timestamp, workspace id,
drawer id, wing and room for memory.note.deleted. It contains no note text or
preview. The memory.deleted event is the content-free tombstone with the same
location and actor. The existing content-redacted WAL remains intact. Audit and
event history remain after deletion; unrelated notes, imported source records,
workspace membership and permission policy remain unchanged. This action removes
the active note from Memory, not copies in previously created backups.

The native confirmation says Delete this note? This cannot be undone. It displays
the first three lines (bounded to 600 characters) as escaped text, with Cancel
and Delete. Cancel keeps the note. Success closes the panel, removes any current
result and saved-note link, and displays Note deleted. A failed write keeps the
note and dialog for an explicit retry; the app never automatically retries a
delete.
