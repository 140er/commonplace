# Recovering an encrypted workspace

This runbook covers a workspace that was encrypted with
`commonplace workspace encrypt` (see [workspace encryption](../workspace-encryption.md)).
It applies to the opt-in, server-decryptable format only.

## What you need

Opening an encrypted workspace needs three things. A backup of the data
directory holds only the first.

| Item | Where it lives | Secret? |
| --- | --- | --- |
| Encrypted SQLite files and `workspace.json` | data directory and its backups | no (ciphertext) |
| Keyslot record `WORKSPACE_ID.json` | the external key store | no, but useless to lose: it holds the wrapped data key |
| One wrapping credential (32 random bytes, mode `0600`) | server credential on the host, recovery credential kept elsewhere | **yes** |

The keyslot record has one slot per credential: `server` and `recovery`. Each
slot wraps the same workspace data key. Either slot, with its own credential,
opens the workspace. A credential only fits its own slot of its own workspace.

Keep the recovery credential and a copy of the key store away from the host
and away from the data backups. A person who holds a data backup, a keyslot
record and a matching credential can read the workspace.

## Check before you need it

`keyslot-check` proves that a credential unlocks its slot and that the
unwrapped key opens every encrypted database of the workspace. It writes
nothing and the service may keep running.

```sh
commonplace --data DATA workspace keyslot-check WORKSPACE \
  --key-store EXTERNAL_KEY_STORE --slot recovery --key-file RECOVERY_CREDENTIAL
```

Run it for the recovery slot after encrypting a workspace, after every
credential change and as part of each restore drill. Exit status 0 and
`"state": "ok"` mean that credential can recover the workspace. The output
lists the slots present, the number of databases opened and the key id. It
never contains key bytes.

## Messages and what they mean

| Message | Cause | What to do |
| --- | --- | --- |
| `Encrypted workspace requires a key store and wrapping key file` | `COMMONPLACE_KEY_STORE` or `COMMONPLACE_WRAPPING_KEY_FILE` is unset, or `COMMONPLACE_KEY_SLOT` is not `server` or `recovery` | set the environment of the service |
| `Workspace keyslot is unavailable` | no readable `WORKSPACE_ID.json` in the key store | restore the key store copy |
| `Wrapping key file is unavailable` | the credential file is missing or unreadable | supply the credential |
| `Wrapping key must be a private regular file` / `exactly 32 random bytes` | wrong permissions or a damaged credential | fix the mode to `0600`; a damaged credential is lost |
| `Workspace keyslot could not be unlocked` | the credential does not fit this slot of this workspace, or the slot is absent or damaged | use the right credential and slot, or the other slot |
| `Encrypted database could not be opened with this key` | the slot unlocked, but its key does not open the database files | the key store belongs to another generation of the data; find the matching copy |
| `SQLCipher requested but sqlcipher3 is unavailable` | the driver is not installed | install the `sqlcipher` extra |

A refused open never falls back to plaintext and never creates a database.

## Restore on a new host

1. Restore the data backup into a new directory (`commonplace restore`, see
   [backups](../backups.md)). This needs no key: encrypted files are verified
   and copied as bytes.
2. Put the key store copy and one credential on the host, outside the data
   directory, with private permissions.
3. Run `keyslot-check` against the restored directory with that slot.
4. Start the service with `COMMONPLACE_KEY_STORE` and
   `COMMONPLACE_WRAPPING_KEY_FILE`. When only the recovery credential is
   available, also set `COMMONPLACE_KEY_SLOT=recovery`, then re-create the
   server slot as below and remove that setting.

## The server credential is lost

The recovery slot re-creates the server slot. Stop the service first.

1. Create a new random 32-byte credential file with mode `0600`.
2. Run:

   ```sh
   commonplace --data DATA workspace keyslot-rewrap WORKSPACE --by OWNER \
     --key-store EXTERNAL_KEY_STORE --slot server --new-key-file NEW_SERVER_CREDENTIAL \
     --unlock-slot recovery --unlock-key-file RECOVERY_CREDENTIAL
   ```

3. Point `COMMONPLACE_WRAPPING_KEY_FILE` at the new credential, start the
   service, and run `keyslot-check` for both slots.
4. Copy the changed keyslot record to wherever the key store is backed up.

## Replacing a credential

The same command replaces either credential. `--unlock-slot` names a slot you
can still open; `--slot` names the slot that gets the new credential. To
replace the recovery credential, unlock with the server slot:

```sh
commonplace --data DATA workspace keyslot-rewrap WORKSPACE --by OWNER \
  --key-store EXTERNAL_KEY_STORE --slot recovery --new-key-file NEW_RECOVERY_CREDENTIAL \
  --unlock-slot server --unlock-key-file SERVER_CREDENTIAL
```

What the command does and does not do:

- It needs the service stopped, the workspace owner (`--by`) and a credential
  that opens a current slot. It refuses a new credential that already opens a
  slot, so the two slots never share one.
- It checks that the unlocked key opens the workspace databases before it
  writes anything.
- It replaces the keyslot record atomically and reads it back. An interrupted
  run leaves the previous record in place. Keep the old credential until the
  command reports `"state": "rewrapped"` and `keyslot-check` passes.
- It does not change the data key. No database is rewritten, and older
  backups open with the new record.
- For that reason it is **not revocation**. Someone who kept the old
  credential together with an old copy of the keyslot record can still unlock
  the data key. Destroy old key store copies when you retire a credential. If
  a credential and a keyslot record may have leaked together, the data key
  itself must change; that operation does not exist yet (see the open
  decisions in [workspace encryption](../workspace-encryption.md)).

## When recovery is not possible

- Both credentials are lost, or every copy of the keyslot record is lost: the
  encrypted databases cannot be opened. There is no escrow and no back door.
- The backup made by `workspace encrypt` and the retained pre-migration
  generation are plaintext copies of the state before encryption. They hold
  nothing written afterwards, and they need the same protection as any
  plaintext backup until you delete them.
