## sealed

- **Paths:** `commonplace/sealed/`
- **Owns:** Immutable opaque envelope framing and trusted internal per-workspace SQLite put/list/get, outside palace generations.
- **Promises:** Explicit creation, append-only commitments, stable retries and finite cursor bounds. No import-time registration, route, crypto, key, companion, export or activation. All original nine obligations remain OPEN.
- **How it fails:** Typed bounded refusals for invalid framing/cursors, unsafe or unsupported opens, workspace/schema mismatch, conflicts and unavailable storage; no repair, replacement or ordinary-memory fallback. Secure opens require Linux, private owned directories and system SQLite with NOFOLLOW support.
- **Tests:** `tests/test_sealed_records.py` and `tests/test_sealed_store.py` exercise synthetic framing, persistence, retries, processes, cursors, faults and deterministic main/side-file replacement races; they establish no device or cryptographic acceptance.
