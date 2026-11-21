### Conditional machine consent

**M:** `commonplace/compute/consent.py` owns the `compute_consents` control-SQLite table. Each canonical machine id has a single `allowed` flag and integer `revision`, initially false and zero. Installation adds records for existing machines and inserts records for new enrollments; repeated installation preserves every existing value. GET does not write or grant consent.

`GET /api/compute/consent/<machine_id>` returns `{"consent":{"machine_id":"mach_…","allowed":false,"revision":0}}`. POST accepts exactly `allowed` (a boolean) and `expected_revision` (a nonnegative SQLite-range integer). It returns the updated state on success. An authorized stale POST returns 409, `error: stale_revision`, and the current consent. It never refreshes a revision and retries the requested value. Even an explicit mutation that retains the same flag advances the revision.

Both operations use the canonical control database transaction and recheck the exact current person session, workspace owner membership, actual enrolling owner and both current machine/worker connections. Coowners cannot read or change another owner's consent; members, agents and anonymous callers cannot act as the owner. A machine label cannot redirect an old request to a replacement machine. Disabled/revoked machines or inactive connections fail closed.

Relevant grant, binding, machine-connection, connection-security, owner-membership and disabled-account changes revoke consent and advance the existing revision in the same SQLite transaction. They never delete or reset it. Conditional writes use `UPDATE … WHERE revision=?` inside `BEGIN IMMEDIATE`, so independent server connections cannot both commit the same revision. A failed write or audit rolls back its state and revision together.

Account export and deletion classify the table by user and workspace. Workspace exports restrict its rows to the exporting owner; a shared workspace's other owner receives no private consent. Account purge removes the leaving owner's rows and keeps other owners' rows; deleting the workspace includes its consent records. Startup backfill excludes disabled enrolling accounts, so retained machine tombstones cannot recreate purged consent at revision zero.

**Scope:** This storage/API core enables no collector, transport or Activity UI. The pinned base has no consent endpoint (404). Real Activity stop/navigation/remount, visible conflict and authoritative reload acceptance remains required when Activity is integrated; these server tests do not satisfy that UI acceptance.
