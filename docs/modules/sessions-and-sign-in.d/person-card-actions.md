- **Owns:**

  - OAuth consent shows separate unticked boxes for `cards:decide:person` and `cards:approve:person`, using the exact strings in `person_actions.CONSENT`. Only a session-authenticated current member can grant them for their own account and current workspace. A new code creates a new connection and recorded grant; refresh only narrows existing scopes.
- **Promises:**

  - Server discovery omits the scopes while phone actions are off; protected-resource discovery and default scopes remain unchanged. Web session policies exclude the app-only scopes. Disabling the switch between consent form display and submission refuses the grant.
