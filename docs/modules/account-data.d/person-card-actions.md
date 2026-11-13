- **Owns:**

  - `oauth_person_grants` classifies each consented capability by person, workspace and connection for account export/deletion. No bearer is stored. Card receipts live with the existing card history in the workspace database, which workspace exports include.
- **Promises:**

  - Phone card/chat capabilities grant no account export or deletion authority. Existing deletion safeguards remain unchanged. No deletion token is introduced in this slice.
