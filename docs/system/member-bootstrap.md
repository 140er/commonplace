### Workspace bootstrap and owner-only reads

- The shell's shared refresh previously required `GET /api/needs-me` even for a member, so its owner-only refusal prevented otherwise permitted workspace screens from loading.
- The shell now skips that queue without owner management and treats an optional queue's authentication or permission refusal as an empty count. The route retains its existing owner check. A transient read error still offers Retry, and required authentication errors still request sign-in.
