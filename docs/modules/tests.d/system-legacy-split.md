- **Tests:**

  - `tests/test_system_legacy_fragments.py` covers the legacy-decision move: extraction rebuilds the original byte for byte for every part size, slugs are unique and avoid existing files, a dropped line, deleted file, dropped or duplicated pointer, section left in `SYSTEM.md` in full, copied file, empty file or wrong heading each fail, the repository's own manifests are one-to-one, `docs-check` is clean against the development branch, and two changes to two moved decisions merge cleanly in both orders (a changing-neighbours control conflicts in the single file).
