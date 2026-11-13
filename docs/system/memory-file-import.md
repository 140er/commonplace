### Memory file import

Memory → Import accepts one supported UTF-8 conversation, fact or memory-text
export of up to 4 MiB. It reuses the conversation, fact and private memory-file
parsers; plain memory text uses `.md` or `.txt` and requires the private
destination. Archives, attachments and additional formats are unsupported. The
browser sends the original decoded file to this instance; processing uses no
model or external service. A remote instance receives the uploaded file, so use
an instance on your machine for local-only storage.

The preview and confirmation endpoints require a signed-in person. Both reuse
the existing import admission checks: ordinary imports require read/write access
to workspace conversations, and private imports require current
personal-workspace ownership. Confirmation requires explicit consent and a
preview digest bound to the person, workspace, destination, source, format and
exact file contents. Current permissions are checked again; preview grants no
new authority.

Preview writes no memory, cadence rows or temporary files. File names are never
paths or import identities. Confirmation uses the existing serialized batch
importer and a SHA-256 file identity, preserving exact UTF-8 archive bytes and
message words. Retrying the same file, including after a lost response or a
rename, adds no duplicate within the same source and destination. Different
sources or destinations remain separate. Private reports retain the existing
accepted-only shape. Errors never include source content or parser exception
messages.

No new database tables or temporary files are needed. Existing account export
and deletion rules own the imported rows. The default import API, connection
consent, private-reader rules, storage checks and append-only semantics remain
in force.
