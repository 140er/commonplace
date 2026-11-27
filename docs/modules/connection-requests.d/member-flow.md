- **Promises:**

  - Approving creates an untrusted connection belonging to the requester, with shared-memory read access and inbox writes, lasting 90 days from approval. It grants no private-memory, other-workspace or owner authority. Existing restricted-origin release rules apply to its cards.
  - No usable token exists until the requester collects it. Approval and owner lists return no secret. Collection atomically stores only the new token's hash and can return the secret once; owner rotation is refused for these connections. The member and owner can both revoke them.
- **How it fails:**

  - A lost collection response cannot reveal the token again. Revoke the connection and request a replacement. Expired or revoked connections cannot be collected; a departed or disabled member cannot receive a token.
