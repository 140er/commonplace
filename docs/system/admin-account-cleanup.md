### Audited removal of membership and disposable accounts

The local administrative CLI provides two deliberately narrow cleanup operations.
It uses the existing instance-administrator identity: an explicit `--by` email or
user id must resolve to an active user whose stored `users.is_admin` is 1. Access
to the service's OS account remains a prerequisite; an email argument is not a
remote authentication mechanism. Neither command grants workspace content access.

`commonplace member remove <workspace> <email|user-id> --by admin@example.invalid`
previews removal of that membership and its credentials. Add `--yes` to execute,
and optionally `--reason TEXT`. The last active owner cannot be removed. Removing
another owner requires `--force-owner` and a second active owner. Workspace-scoped
sessions, login links, pending OAuth requests, OAuth codes, access/refresh tokens,
person-action grants, account-deletion proofs, connections and runner bindings
for that member are deleted in the same transaction as the membership. Credentials
for other workspaces and other users are retained. A dependent connection of
another user prevents removal rather than being detached or deleted.

Cards, memory, workspace files and historical author identifiers stay intact;
the user's account remains. Connection credentials are deleted because the
membership foreign key prevents retaining their rows after membership removal.
Other workspace records and the existing audit history retain their original ids.

`commonplace user delete <email|user-id> --by admin@example.invalid` previews
account cleanup. Add `--yes` to execute and optionally `--reason TEXT`. It accepts
only an account explicitly marked at creation with
`commonplace user add disposable@example.invalid --disposable`, or an address whose
exact, case-insensitive domain is `example.invalid` or `example.test`. Subdomains
and lookalike suffixes are not eligible. The additive, idempotent migration adds
`users.is_disposable` with default 0 and a Boolean constraint: existing real
accounts never become disposable implicitly. A marker is necessary because an
ordinary address does not establish that its account is safe to destroy.

Deletion refuses any remaining membership, ownership of a workspace with other
members, deletion of the last active instance administrator, and foreign-key-bound
records outside the explicit cleanup allowlist. The refusal points to the existing
account-deletion flow, which retains its feature flag, grace period, ownership
transfer, permanent provider reservations and tombstones. This command does not
replace or enable that flow. Remove memberships first with the supported command;
a sole owner must arrange another active owner before leaving.

Eligible deletion removes all sessions, login links, pending OAuth requests,
connection tokens and OAuth/person grants, plan/usage records, sign-in identities,
email challenges and ingest cadence records, then the user row. Empty retained
personal workspaces lose only their `personal_of` pointer; their data stays.
Provider ownership reservations and other related records are never bypassed:
a foreign-key refusal rolls the entire cleanup back. No workspace is deleted.

Dry-run is the default and prints per-table deletion counts. It writes no cleanup
audit row and changes no account, membership or credential. A successful repeated
operation returns `already absent` and writes nothing. Both CLI commands hold the
existing shared data-directory lock and immediately refuse a held backup fence.
User deletion also checks the matching gateway's backup/pause `busy` signal before
opening the database. It makes no backup itself. A backup or pause must finish
before deletion is attempted again, including a dry-run.

The database uses one `BEGIN IMMEDIATE` transaction for authorization, eligibility,
credential removal, membership or user deletion and audit insertion. Foreign keys
stay enabled and `PRAGMA foreign_key_check` must succeed. An exception, including
failure to append the audit row after deleting the user, rolls back every change.

Each executed change appends exactly one `audit_log` row: `action` is `member.remove`
or `user.delete`, `decision` is `ok`, `auth` is `cli`, `user_id` is the acting
administrator, `at` is UTC, and `target_ids` contains the target user id. Membership
removal records `workspace_id`; account deletion is instance-scoped. The optional
`reason` uses the existing 500-character audit bound. Reasons must contain no
content or credentials. No memory, card text, email, credential or database dump
is copied automatically into the log. Existing audit rows are never updated or
deleted; the database's append-only triggers remain in force. Refused and dry-run
operations do not append a successful-change row.

Focused regressions in `tests/test_admin_account_cleanup.py` cover the missing
commands on the base revision, scoped revocation, retained attributed content,
owner guards, explicit administrator checks, disposable eligibility, legacy
migration, refusal during backup/pause, dry-runs, retries, rollback after a
simulated crash and immutable audit history. CLI help and documentation reference
checks deliberately pin the new command names, flags and audit protocol tokens.
