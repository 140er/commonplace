- **Owns:**

  - The existing `POST /api/boards/<slug>/chat` alias recognizes only an explicitly consented phone chat capability and calls the normal person post. It records a `space_message` by the person's user id with no connection stamp. Other connections keep their normal policy checks and connection-reply semantics. No other board or space alias accepts the phone capability.
