## imports

- **Paths:** `commonplace/imports.py`, `commonplace/import_files.py`, `commonplace/import_grant.py`, `commonplace/import_parts.py`, `commonplace/import_sources.py`, `commonplace/import_tools.py`, `commonplace/ingest_cadence.py`
- **Owns:**

  - Consented one-time imports of conversations and facts and the shared ingest cadence.

- **Promises:**

  - Signed-in file previews are write-free and bounded to 4 MiB of UTF-8. Confirmation rechecks the same import permissions and consent, then calls the existing batch importer with a stable content identity; no staging files or new tables are created.

  - One table in `import_sources.py` says which destination takes which file type. The server checks every import against it, and `GET /api/memory/import/rules` gives the same table to the web form, which picks a compatible destination from the file type, disables the others with a plain reason and shows an impossible combination before a file is chosen. The server still refuses an incompatible pair.

  - Imported-history consent: Reader listing uses literal account-prefix pagination and live Recall authorization; unconsented agents receive the missing-resource response.

  - Imports are verbatim, bounded and consented; no capture, model or external call is involved.

- **How it fails:**

  - A refused or interrupted import leaves already committed batches in place and reports where it stopped.

- **Tests:**

  - `tests/test_commonplace_import_files.py` proves the rules table matches what the server accepts, that a text file sent to the default destination is still refused, preview isolation, byte-equal archives, exact retry, current member scope, private consent, upload refusals and redacted errors over HTTP.

  - `tests/test_commonplace_import.py`, `tests/test_commonplace_import_grant.py`, `tests/test_commonplace_import_list.py`.
