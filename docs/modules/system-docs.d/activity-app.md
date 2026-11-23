- **Owns:**
  `docs/system/compute-activity-app.md`: the Activity app's privacy and provenance boundary, current owner/consent authority, retained-state disclosures, refresh behavior and list-only scope. Collector implementation and counting documentation remain separate.
- **Promises:**
  The guide distinguishes stored aggregates from liveness, missing data from zero, retained PR states from window events, and list summaries from a detail view. It documents account export/purge ownership without weakening the existing lifecycle rules.
- **How it fails:**
  A detail feature absent from the app, unsupported task mapping or incomplete collection is not described as available or complete. Documentation does not imply remote source-file access or an execution grant.
- **Tests:**
  Activity API, authority, account and UI tests plus the documentation obligation and fragment guards.
