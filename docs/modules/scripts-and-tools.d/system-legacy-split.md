- **Owns:**

  - `scripts/system_legacy_split.py` moves the legacy `###` decisions of `SYSTEM.md` into `docs/system/<slug>.md` files and proves the move lossless against the base revision.

- **Promises:**

  - `extract` leaves a pointer with the same heading in `SYSTEM.md`, writes the section text byte for byte into its file and records title, slug, size and SHA-256 in `tests/fixtures/system_legacy_part<N>.json`; `verify` rebuilds the original `SYSTEM.md` from the pointers and the files and fails on any dropped, altered or duplicated section.

- **How it fails:**

  - A missing, edited, duplicated or unlisted section, or a rebuilt text that differs from the base revision, exits 1 with one line per problem; it never rewrites anything on `verify`.
