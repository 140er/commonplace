- **Owns:**

  - `oauth_person_grants` is classified by person, workspace and connection as a consent credential association. Account export includes its non-secret fields and deletion removes the leaving account's grant rows, including where a shared workspace stays. Card receipts live with existing card history in the workspace database.
- **Promises:**

  - Phone card scopes give no account export or deletion authority. Account deletion continues to require the existing web sign-in and safeguards. No deletion token is introduced in this slice.
