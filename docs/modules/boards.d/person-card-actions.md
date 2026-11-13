- **Owns:**

  - The existing `POST /api/boards/<slug>/chat` alias recognizes only an explicitly consented phone chat capability and calls the normal person post. It records a `space_message` by the person's user id with no connection stamp. Other connections keep their normal policy checks and connection-reply semantics. No other board or space alias accepts the phone capability.
  - Both chat implementations share the existing `space_post` method dispatch and checked REST inventory entry. The phone post enters `rest_write` with the current person before calling the board operation; its single audit row also names the authenticated client.
