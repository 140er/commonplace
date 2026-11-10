# Eval archives (slice 1)

`commonplace eval` stores immutable, owner-only observations of stored threads
so that later evaluation work can read exactly what an agent saw and wrote.
Slice 1 archives and reads; it scores, judges and replays nothing. Everything
runs locally in the CLI: no network, model, subprocess, hook, daemon, REST,
MCP or UI surface.

A **run** is one eligible thread's committed rows at one read snapshot, plus
optional caller artifacts and a manifest. It is not evidence of a verified job,
input, completion or billing.

## Commands

Every command needs `--workspace W` (a workspace ID). The actor is `--by USER`
or the first admin, as for other CLI commands, and must be a live owner of W.

| Command | Does |
|---|---|
| `eval archive --thread STORED_ID --key K [--artifacts-stdin]` | Stores one run. Prints `{run_id, created_at, reused}`. |
| `eval list` | Authorized runs (IDs, source stored ID, times, byte lengths, digests, flags) and `limits`: quota, bytes used by the listed runs only, run cap, chunk size. |
| `eval show RUN` | The run's manifest and its flags. |
| `eval read RUN --object OBJ [--offset N]` | At most 256 KiB of exact bytes of `manifest`, `rows` or `artifact/<i>` as `{object, offset, next, total, sha256, data_base64, flags}`. |
| `eval purge RUN --confirm RUN` | Deletes one run by ID (see Purge). |
| `eval preflight` | Stat-only report of the eval paths, modes, owners and plain/encrypted classification. Opens no SQLite file. |

Refusals print `commonplace: <code>` with exit 2, never payload, key or source
metadata. Unknown flags refuse before anything is read or created.

## Eligibility

A thread is eligible only if it is in W, in the `threads` wing, its source ID
starts with none of `capture:`, `capture-fork:` or `import-private:`, and its
first row is not a continuity marker. That covers handoff transcripts and ACL
imports (single-part and multipart). Live captures, grant imports, private
chats and every continuity thread refuse with
`eval_source_gap: unsupported_in_slice1`. The rule, the read policy and the
continuity probe run again inside every archive commit and every list, show
and read response; a run whose source is missing, unreadable or now related to
continuity is hidden, and its quota stays used.

## The `rows` object

Strict UTF-8 JSON `[[seq, role, kind, name, call_id, at, text, raw], …]`
(compact separators, no ASCII escaping) of every row. Before commit the archive
proves the rows are dense and match the store's digest, then re-reads the
inserted chunks and compares all eight fields. A lone surrogate refuses with
`eval_source_gap: unencodable`. Artifacts are JSON
`[{"name": str, "text": str}, …]` on stdin with unique, printable, non-path
names; text is stored as given.

## Keys

Use a random key, for example
`python3 -c 'import secrets;print(secrets.token_urlsafe(32))'`, never a title,
path, credential or task text. `K` must match `[A-Za-z0-9_-]{16,64}`. Only a
domain-separated, length-prefixed SHA-256 of W and K is stored; anyone who can
read `runs.sqlite3` or a backup of it can confirm a guessed key. The same key
with the same content returns the existing run; different content, including a
renamed artifact, refuses with `eval_conflict`. A purged run's key stays retired
for 30 days (`eval_key_retired`), then becomes reusable.

## Limits

16 MiB per run (rows, artifacts and manifest), 256 KiB chunks and read pages,
1 GiB of active runs per workspace. With plans enabled the eval DB counts
against the account's plan storage: the archive holds the thread store's writer
lock, measures the workspace and refuses (`eval_storage`) past the limit, so an
archive needs roughly twice the eval DB's size in headroom. Refusals carry no
totals.

## Privacy flags

Archive scans every string of every row, the decoded JSON string leaves of
`text` and `raw`, the artifacts and the manifest with `commonplace.privacy`,
and stores the sorted codes with the scanner's version (the first 16 hex
characters of the SHA-256 of the loaded `privacy.py`). Flags are signals: bytes
are always kept, never redacted. After a scanner change, list, show and read
report the union with a rescan and write nothing. A scanner that cannot load or
run makes archive, list, show and read refuse with `eval_scan_failed`; purge and
preflight never load it.

## Purge

`eval purge RUN --confirm RUN` needs only live ownership of W and the exact run
ID; it works even when the source is gone, so keep run IDs. The
acknowledgment is the same for a present, absent or already purged ID. Before
confirming, purge lists the workspace's retired store generations (prune-eligible
after 14 days): restoring one can bring purged runs back. Purge does not erase
free pages, WAL, retired generations or backups.

## Files and permissions

Runs live in `<workspace>/evals/runs.sqlite3` (WAL), created through the
workspace's SQLite opener, so an encrypted workspace encrypts it before the file
exists. `evals/` must be 0700 and `runs.sqlite3*` 0600, owned by the CLI user,
with no symlinks and no group- or other-writable ancestor up to the data
directory. Eval never changes modes; it refuses with `eval_permissions` and
names each fix, for example `chmod 0700 <workspace>/evals` and
`chmod 0600 <workspace>/evals/runs.sqlite3*`. Linux only (`eval_platform`
elsewhere). Backups, encryption conversion and store switches carry the eval DB
like every other workspace SQLite file; online backups refuse encrypted ones.
Artifacts redirected from a plaintext file stay outside encryption and purge.
