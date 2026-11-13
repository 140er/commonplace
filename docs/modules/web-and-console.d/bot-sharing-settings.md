- **Owns:**

  - Deck Bot settings exposes a named workspace-person picker from the existing signed-in profile, per-person talk and shared-conversation permissions, previews, grant/change/revoke, and the access log. Members get a read-only permissions view. Frozen bots show adoption or enabling only to workspace owners; workspace management never opens another person's private thread.
  - Shared conversations have a separate selector and an audience preview before creation. All message, confirmation, stop, read and pagination requests retain the selected thread. Drafts are kept separately for private and shared conversations. Learned items can be shared with a chosen project's readers only after the server's words-and-audience preview and a separate confirmation.

- **Promises:**

  - These controls exist only in Deck. Classic keeps its original settings and request paths. The browser uses existing routes and does not widen server authorization. Owner previews name the readers; member previews describe the reader permissions because a member may only read their own share row. Historical log changes never infer permission flags that were not recorded; a person no longer in the workspace is described as a former member.

- **Tests:**

  - `test_bot_sharing_ui.py` runs browser-free settings, permission, audience, request-scope, escaping and classic contracts. `test_bot_sharing_browser.py` checks genuine owner/member sessions, refusal of member edits, immediate revocation, shared-thread creation, lifecycle changes and learned-item sharing at desktop and phone sizes. Browser checks skip when the optional dependency or executable is unavailable.
