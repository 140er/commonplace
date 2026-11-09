### Operator-blind token slots: primitives with production locked

- B3 is opt-in via the proposed `encryption.key_mode=token-v2` marker. This slice rejects that marker (and unknown key modes) with an encryption error before operator-held B2 keys or content are opened, including backup snapshots. Existing workspaces without the marker retain their existing behavior; no workspace is automatically migrated or modified.

- The isolated token-slot primitive uses HKDF-SHA256 over raw random token bytes and a fresh random salt, then AES-256-GCM with a fresh nonce to wrap a caller's 256-bit workspace DEK. Its authenticated context binds format version, workspace, epoch, authority, token/grant kind, token digest and slot ID. Strict records reject unknown fields, mixed versions and server/recovery wraps. No raw token, DEK or operator wrapping key is persisted by this module. User-only recovery will use a separate envelope in a later slice. Synthetic tests exercise two tokens and two workspaces and exact payload bytes.

- This primitive does not authenticate current control state or establish runtime authority. A caller must authenticate current workspace/epoch/grant state before unwrapping; digest-only metadata cannot unlock a slot. A scoped bearer exposes the whole workspace DEK, so wing scope remains API policy. Active code/TLS/RAM control can capture secrets; sizes, timing and routing remain visible. Slot-only restore must not restore revoked authority; full control-state rollback, deletion and client compromise are outside this threat model.

- Production enrollment, OAuth schema, capabilities, worker leases, job/cache closure and refresh serialization belong to the runtime slice. Fresh-DEK cutover, user-only recovery, exact-data comparison, crash/restore proofs and activation belong to the final slice. Production remains locked pending those slices, complete encrypted-content inventory acceptance and owner decisions. No cutover, pointer publication, archive deletion or B2 wrap reuse is implemented.

- Raw-key `cipher_connect` is a low-level SQLCipher utility used by B2 migration, not a B3 authority boundary. Future B3 callers must use `connect` or a dedicated guarded entry point that authenticates current control state before content opens.


