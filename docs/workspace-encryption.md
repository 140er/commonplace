# Experimental workspace SQLite encryption

Encryption is opt-in and requires validation before rollout. It is not enabled
by installing this release. Existing storage stays unchanged until its owner
explicitly runs the offline migration.

The `sqlcipher` extra supplies `cryptography` and a prebuilt `sqlcipher3` driver
on Linux x86-64. Other platforms need a separately installed SQLCipher-linked
`sqlcipher3` driver. Those platforms have not been validated. A missing driver,
unsupported cipher, missing keyslot or incorrect key stops the open; there is
no plaintext fallback.

Each workspace has one random 256-bit data key, used in SQLCipher raw-key mode
for its SQLite files. Connections require 16 KB pages, HMAC-SHA256, a 64 MiB
cache and memory-only temporary storage. The compact palace, threads, boards,
rules and knowledge graph remain separate files sharing that workspace key.
This does not consolidate them into one database.

## Provisioning and migration

Provision two independent, cryptographically random 32-byte wrapping-key files
with private permissions. They are credentials, not passwords. Supply one for
the server and retain the other separately for recovery. Never put either
credential, or the key store, inside the data directory or its backup directory.

Stop the service and all workspace workers. For an existing workspace, first
complete the verified switch to compact storage. Then the ordinary workspace
owner can run:

```sh
commonplace --data DATA workspace encrypt WORKSPACE --by OWNER \
  --backup-dir NEW_BACKUP --key-store EXTERNAL_KEY_STORE \
  --server-key-file SERVER_CREDENTIAL --recovery-key-file RECOVERY_CREDENTIAL
```

The operation takes the offline lock, checks ownership, makes and verifies a
backup, exports SQLite files into a new generation, checks integrity and both
wrapped keyslots, then publishes the generation pointer. A failure before
publication leaves the original generation selected. A verified backup and the
original generation remain available for rollback. Restoring that backup into
a new data directory recovers the pre-migration state; it does not incorporate
later writes. A publication error preserves the staged files and keyslots for
manual reconciliation, including when the pointer replacement already happened.
Existing format-switch and pruning commands refuse encrypted
workspaces pending a validated recovery/retention procedure.

At runtime set `COMMONPLACE_KEY_STORE` to the external key store and
`COMMONPLACE_WRAPPING_KEY_FILE` to the server credential file. Only paths, not
key bytes, are passed to workspace workers. For recovery use the recovery
credential and set `COMMONPLACE_KEY_SLOT=recovery`. Recovery does not need the
server slot. Keyslot records use AES-256-GCM with random nonces and authenticate
the workspace id, key id, slot name and format version. No raw data key is
written into those records.

## Backups and limits

Migration records every discovered SQLite file, including unusual filenames and
nested paths, in the mandatory `encryption.database_paths` list in
`workspace.json`. The sorted paths are captured relative to the staging
generation root before in-place exports and published only after all exports
succeed. Missing or malformed inventories stop access; this experimental
format has no legacy metadata upgrade. An empty list is valid for a fresh
workspace. Entries must be unique, canonical relative POSIX paths contained in
the generation, without symlink escapes. `workspace.json` and names ending in
`-wal`, `-shm` or `-journal` are reserved and cannot be inventoried.

Inside an encrypted workspace, `.sqlite3`, `.sqlite` and `.db` are reserved
database extensions. New stores with these extensions work without updating
metadata and receive the same backup handling as migrated files. Other names
must match an inventory entry relative to the exact workspace generation.
The connection factory refuses unlisted names before loading the driver,
unwrapping a key or opening a database. Backups apply this same classification;
unexpected plaintext or corruption at a reserved or inventoried database path
fails the snapshot instead of being copied as an auxiliary file. Outside an
encrypted workspace, SQLite header detection and plaintext snapshots are
unchanged; an opaque auxiliary `.db` file can be byte-copied. Ordinary JSONL,
JSON, log and binary auxiliary files remain byte copies.

Offline backups checkpoint SQLite, acquire its writer lock, reject a remaining
WAL, and byte-copy the database. They do not use SQLite's backup API to
re-encrypt pages. Unchanged encrypted pages therefore retain their exact
ciphertext. Backup manifests verify byte counts and hashes. Encrypted files
can be verified and restored without keys; that checks their bytes, not their
decrypted integrity. SQLCipher page authentication and SQLite integrity are
checked while making the backup and again on keyed use where applicable.

Workspace settings, including this inventory, are trusted operator-controlled
metadata. The inventory is not authenticated by keyslot AAD. Malicious host
operator edits are outside this opt-in, server-decryptable model; it does not
provide operator-blind encryption.

## Keyslot check, credential replacement and recovery

`commonplace workspace keyslot-check` proves that one credential unlocks its
slot and that the unwrapped key opens the workspace databases; it writes
nothing. `commonplace workspace keyslot-rewrap` wraps the unchanged data key
for one slot under a new credential, unlocked by any current slot, so the
recovery slot can re-create a lost server slot. It runs offline, needs the
workspace owner, replaces the keyslot record atomically and keeps the other
slot byte for byte. The step-by-step procedures, the refusal messages and the
cases without recovery are in the
[recovery runbook](recovery/encrypted-workspace.md).

Replacing a credential is not revocation. The data key does not change, so an
old credential kept together with an old keyslot record still unlocks it.

Keyslots must be backed up separately with short retention. The application
does not yet implement that retention policy, host-key unlocking, credential
delivery, data-key rotation/revocation, or deletion/crypto-shredding
guarantees. Losing both slots or both wrapping credentials loses access. Old
copies of credentials and slots can still unlock old ciphertext.

## Open decisions

These are owner decisions; nothing below is built.

1. **Data-key rotation (true revocation).** Option A: an offline
   `sqlcipher_export` into a new generation under a new key and key id, the
   same shape as the first migration, with a verified backup and a retained
   previous generation. It reuses a tested path and keeps rollback, but needs
   twice the disk space and every page changes, so the next backup shares
   nothing with earlier ones. Option B: `PRAGMA rekey` in place. It needs no
   second copy, but an interruption between files leaves one workspace under
   two keys and there is no rollback. Recommended: A. Either way, backups made
   before the rotation stay readable with the old key; revocation is complete
   only when those backups and old keyslot copies are destroyed.
2. **Recovery credential ceremony.** Today the operator creates both
   credential files and decides where the recovery one is kept. Option A:
   keep that, and document offline storage (the recovery runbook). Option B: the
   command generates the recovery credential and shows it once for the owner
   to keep, so the operator never stores it. Option C: split the recovery
   credential between several holders. B and C change who can recover a
   workspace and belong with the operator-blind work, which must not gain an
   operator-held recovery path by accident.
3. **Online backup of encrypted workspaces.** The online snapshot refuses
   encrypted databases; such a data directory is backed up offline. Lifting
   that needs the gateway pause to guarantee a checkpointed, writer-free file
   for the byte copy, which is a change to the online backup protocol.
4. **Keyslot record retention.** How many old copies of the key store are
   kept, where, and when they are destroyed after a credential change.
5. **Files outside SQLite.** See the last section; whole-workspace encryption
   at rest is not claimed.

This change encrypts **SQLite files only**. Revision JSONL files, entity
registries, explicit exports, operational logs, the control database and
retained pre-migration generations/backups are outside this protection. They
can contain sensitive data. It is therefore insufficient for a whole-workspace
encryption-at-rest promise. Auxiliary-file handling and the keyslot lifecycle
must be settled and validated before activation or stronger privacy claims.

SQLCipher's raw-key and export behavior follows its
[official API documentation](https://www.zetetic.net/sqlcipher/sqlcipher-api/).
