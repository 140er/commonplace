### Account export and deletion

A person exports their account as one streamed
archive: control rows about them and the full content of workspaces they own; a
workspace they are only a member of is listed, not exported. Deletion is an instance
setting, off by default (`COMMONPLACE_ACCOUNT_DELETION`). It is a request with a recent
sign-in and a typed confirmation, a grace period in which the account keeps working,
then a purge. The purge deletes workspaces with no other live member and every row
keyed to the account itself, and leaves tombstones (user id, membership, revoked connections)
where other people's work refers to it. A sole owner of a shared workspace does not
take it with them: the longest-standing active member becomes an owner when the purge
starts (audit entry, event, mail if configured), and only a workspace with no other
member is deleted. It refuses rather than choose for the owner when nobody can take
over, for the last instance administrator and for an account with a GitHub grant. One table (`commonplace/account/tables.py`) classifies
every control table for both paths, and a test fails when a new table is missing.

The control database store row in the Stores table also holds account deletion requests (`account_deletions`).
