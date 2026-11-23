### Inert sealed-record storage

`commonplace/sealed/` supplies trusted internal opaque append-only put/list/get at
`<data_dir>/sealed/<workspace_id>/records.sqlite3`, outside palace generations.
Its frozen six-field envelope supports memory and printed-only recovery-wrap
metadata. It handles no key/code, crypto, local index, route, registration,
ordinary-memory forwarding, export or lifecycle activation.

The opener requires private owned paths, descriptor-relative no-follow traversal
and a Linux system SQLite Unix VFS supporting NOFOLLOW through its public C ABI.
Directory-inode locks cover owned connections. Unsafe/unsupported paths, stale
side files, foreign bindings and unrecognized schemas refuse without replacement
or repair. Finite cursor bounds never refresh or clamp; invalid cursors refuse.
The OS account is trusted. These storage controls do not prove cryptography,
power-loss recovery, server-image safety or distinct-device behavior.

The contract and all nine remaining OPEN obligations are in
`docs/sealed-memory-store.md`; finite synthetic controls are in
`tests/test_sealed_records.py` and `tests/test_sealed_store.py`. Printed recovery
code only and release of the old SAMEchat99 holds remain settled; no full-card
acceptance, scope grant, key UX, export coverage or deployment follows.
