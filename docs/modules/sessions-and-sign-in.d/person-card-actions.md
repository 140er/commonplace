- **Owns:**

  - Native OAuth consent shows three separate unticked phone capabilities using the exact strings in `person_actions.CONSENT`. Only a session-authenticated current member granting to the exact native callback with `memory:read` can acquire them. They are recorded separately from OAuth scopes and do not change any existing scope list.
- **Promises:**

  - New codes create new connections and recorded capabilities. Existing clients/tokens gain nothing automatically. Refresh never grants a capability; disconnecting or converting a connection to machinery cuts off the grant. Web session policies and ordinary OAuth principal types remain unchanged.
  - Machinery conversion revokes stored phone capabilities before parsing the connection policy. Malformed JSON or scope data cannot leave active phone grant rows behind; revocation remains audited and repeatable.
