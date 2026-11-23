# Inert sealed-record store

`commonplace.sealed` provides trusted internal `put`, `list` and `get` for opaque
envelopes. Importing it creates no files, connection, worker, key or registration.
There is no HTTP, MCP, CLI, profile or ordinary-memory integration. A receipt means
that opaque bytes committed; it does not prove encryption or an authenticated tag.
All nine full-card obligations below remain **OPEN**.

## Framing

`parse_envelope(packet)` accepts exactly six fields:

| Field | Version 1 contract |
| --- | --- |
| `format_version` | Integer `1`; booleans and unknown versions refuse. |
| `kind` | `memory` or `printed-recovery`. |
| `record_id` | Exactly 32 lowercase hexadecimal characters; an opaque public locator. |
| `key_id` | Exactly 32 lowercase hexadecimal characters; a public content-key ID. |
| `nonce` | Exactly 12 decoded bytes in strict canonical RFC 4648 base64. |
| `ciphertext` | Strict canonical base64; memory bodies contain 16–1,048,576 decoded bytes; printed wraps contain exactly 48. |

Version 1 framing denotes AES-256-GCM, including the 16-byte authentication tag.
The store implements no cryptography and cannot distinguish well-framed encoded
plaintext from ciphertext. Oversize input refuses; no text is truncated or
normalized. SQLite stores the binary fields as BLOBs. Public envelope and receipt
types are frozen dataclasses. Wire metadata such as workspace, writer, trust or
time, and fields for plaintext, raw keys/codes or embeddings, are refused.

`ServerWriter(user_id, connection_id, trust)` is internal metadata. Trust is
`trusted`, `standard` or `untrusted`. A user ID must be nonempty; an empty connection
ID represents a local caller. These fields supply no authentication. A future
adapter must derive workspace and writer from the authenticated Principal and
authorize every call, including retries. It alone selects the data directory.

## Storage and safe opening

The exact database path is `<data_dir>/sealed/<workspace_id>/records.sqlite3`,
outside the palace and active workspace generations. Workspace IDs follow the
existing `ws_` plus nonempty alphanumeric boundary. Envelope IDs never form paths.
Creation requires `create=True`; a missing store opened otherwise stays missing.
Existing empty, corrupt, foreign or unknown files are never replaced or initialized.

The current implementation supports Linux with descriptor-relative directory
opens, `O_NOFOLLOW`, directory `flock`, and the system `libsqlite3.so.0` Unix VFS
at SQLite 3.37 or later. It uses that library's public C ABI through stdlib
`ctypes`, rather than CPython object layouts. This is a distinct system SQLite
dependency from Python's bundled `sqlite3` driver. Missing capabilities, libraries,
symbols or supported versions refuse with `UNSAFE_PATH`. Windows, macOS and other
platforms have no supported secure-open implementation here. No alternative driver
or provider is selected silently.

The data directory, sealed directory, workspace directory and database must belong
to the effective OS user. Directories are private (`0700`) and files private
(`0600`); existing broader permissions refuse without chmod. Ancestors must belong
to root or that user and prevent other users from replacing children. A sticky
ancestor such as `/tmp` is allowed; the owned private child still supplies its
protection. Symbolic-link traversal, parent traversal, hardlinked files, special
files and changed directory/database identities refuse. The service OS account
and its code are trusted; this is not isolation from hostile code running as that
same OS user or as root.

Directory components are opened relative to held descriptors. A workspace-directory
inode lock covers each owned connection, including its raw database descriptor,
so closing another descriptor cannot release a live SQLite POSIX lock. Lock
contention waits at most one second and reports a bounded unavailable error.
SQLite opens without CREATE and with `SQLITE_OPEN_NOFOLLOW`; it never uses a
`/proc/self/fd` pathname or a check followed by an unprotected Python path open.
Identity and side-file checks run again after the native open, before SQL. The
Unix VFS also opens journal/WAL/shared-memory files without following links.

The format uses rollback journals, not WAL. A pre-existing side file causes refusal
before SQL: this unit does not replay a hot journal before verifying schema and
workspace identity. An interrupted process can therefore leave a store unavailable
while preserving its bytes. There is no recovery, repair, replacement or migration
operation here. Later reviewed recovery work must preserve the original database
and side files. A normal failed transaction closes its connection and rolls back.
These controls do not prove power-loss durability or arbitrary filesystem support.

## Schema, retries and errors

The schema has exactly two STRICT tables and their implicit unique indexes:

| Table | Fields |
| --- | --- |
| `sealed_info` | Singleton `1`, schema version `1`, workspace ID. |
| `sealed_objects` | Sequence primary key, format version, kind, record ID, key ID, nonce BLOB, ciphertext BLOB, writer user ID, writer connection ID, writer trust and UTC RFC 3339 `written_at`. |

All fields are non-null. CHECK constraints enforce kinds, ID/binary lengths,
version and trust. Unique commitments cover `(kind, record_id)` and
`(kind, key_id, nonce)`. Initialization and insertion are transactions. The opener
checks the application marker, exact schema and workspace binding before writes.
There are no plaintext, key, code, FTS, embedding or vector columns.

`put(envelope, writer=...)` returns `PutReceipt(record, inserted)`. A new append
receives a monotonically increasing committed sequence and an injected server-clock
timestamp. A retry with identical envelope bytes and the same user/connection
returns the original sequence, time and trust snapshot with `inserted=False`.
Changed current trust does not restamp the record. Changed bytes, another writer
identity for that ID, or nonce reuse under the same kind/key ID returns `CONFLICT`.
Original rows remain unchanged. No delete or overwrite API exists.

The nonce commitment cannot undo client-side nonce reuse, prove randomness or
authenticate ciphertext. A printed wrap's key ID identifies its content key, not
the wrapping secret; this uniqueness guard therefore does not prove wrapping-key
nonce safety.

`get(kind=..., record_id=...)` returns null only for an absent ID in an accessible,
valid store. Errors are typed `SealedStoreError.code` values:
`INVALID_RECORD`, `NOT_INITIALIZED`, `UNSAFE_PATH`, `WORKSPACE_MISMATCH`,
`UNSUPPORTED_SCHEMA`, `CONFLICT` and `STORAGE_UNAVAILABLE`. Messages contain no
caller body, path, credentials or raw SQLite details. Unavailable errors suggest
retrying after two seconds. Errors never become empty results or ordinary-memory
fallback. Every operation closes its owned native connection on every path;
`close()` and a context manager close the store handle to further operations.

## Finite list cursors

`list(kind=..., after=0, through=None, limit=100)` orders records by sequence.
The first call captures the highest committed sequence across both kinds as
`through`. Retain that bound on later pages. `next_after` is the last returned
sequence only when another matching row remains within that bound; otherwise it
is null. Appends after the bound belong to a later scan. List is not search.

`after` and supplied `through` must be integers in `0..2**63-1`, excluding booleans.
`limit` must be an integer in `1..100`, also excluding booleans. Negative values,
strings, floats, overflow, `after > through`, `after > current high-water`, or a
supplied bound above committed history refuse with `INVALID_RECORD`. Bounds are
never clamped or refreshed. `after == through` yields a finite empty page.
An empty store captures zero. Retaining `through=0` after later writes still yields
that original empty scan; an unmatched kind retains the same global bound.

## Printed-only metadata and deferred client work

Jeffrey selected printed recovery code only; the old SAMEchat99 recovery/design
holds were released. This unit does not reopen that choice. A `printed-recovery`
object contains a public locator, content-key ID, nonce and opaque wrapped-key
ciphertext plus server stamps. Its eventual client body wraps a 32-byte content
key using an independent 32-byte random recovery secret. Neither the secret/code,
a code verifier, unwrapped key, passkey, operator slot nor bearer-derived key is
accepted here. The code grants decryption capability, not account membership or
login. Lost-device recovery also needs the wrap and memory ciphertext.

The later client must use a reviewed AEAD library and exact original UTF-8 bytes.
The version-1 associated-data definitions are compact ASCII JSON arrays:
`["commonplace-sealed-memory",1,workspace_id,key_id,record_id]` and
`["commonplace-sealed-printed-recovery",1,workspace_id,key_id,record_id]`.
The array elements are their actual values; compact separators contain no spaces.
No associated-data builder, key/code generator, crypto implementation or companion
is included in this unit.

The sibling store does not inherit account export, deletion/retention, backups,
metering or workspace lifecycle coverage. Those adapters remain required before
activation. Existing account ZIP and Markdown exports do not cover this store.
Unknown historical SAMEchat99 bytes receive no automatic importer. Existing
SQLCipher, workspace encryption, token slots and ordinary memory remain separate.
Device key custody, volatile local indexing/search, synchronization, both stdio
forwarders, server-image evidence and real distinct-device recovery remain later
work. Synthetic same-host storage tests cannot establish those properties.

## Original nine obligations: all OPEN

| ID | Original requirement | Status and remaining proof |
| --- | --- | --- |
| R01 | Only key-holding devices can read sealed memories; the server never holds the user's sealed key. | OPEN: device custody and server-content exclusion. |
| R02 | The client encrypts verbatim memory text before sending it; encryption must preserve the exact words. | OPEN: actual client encryption and byte-exact recovery. |
| R03 | Sealed ciphertext plus server-stamped metadata is stored in a separate per-workspace store outside the palace; M0 defines sealed put/list/get. | OPEN: internal storage is only one portion; authenticated integration remains. |
| R04 | A local companion holds the key, decrypts memories and keeps/searches a local index. | OPEN: no companion or local index exists in this unit. |
| R05 | Sealed ciphertext is never embedded; client-computed embeddings are not sent to the server. | OPEN: future integration must preserve the storage exclusion. |
| R06 | A sealed memory written on one device is found on a second device. | OPEN: two genuinely distinct devices. |
| R07 | A disk image of the server contains only ciphertext for the sealed memory. | OPEN: bounded server-image capture and sensitive controls. |
| R08 | Sealed companion key recovery is selected and usable; recorded options are printed recovery code, passkey-wrapped key, or both. | OPEN: printed-only is selected; actual recovery remains unproved. |
| R09 | Commonplace export includes sealed records still sealed. | OPEN: explicit ciphertext/wrap inclusion and cold recovery. |

Framing controls are in [test_sealed_records.py](../tests/test_sealed_records.py).
Storage, retry, process, cursor, failure and replacement-race controls are in
[test_sealed_store.py](../tests/test_sealed_store.py). Their execution receipts and
limits belong to the implementation handoff; the presence of tests is not a PASS
or a full-card acceptance claim.
