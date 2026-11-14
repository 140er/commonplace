- **Promises:**

  - The control table `bridge_install_index` (chat-platform install ids, one row per install, no secret or text) is classified by workspace. A workspace's `chat_bridge.sqlite3` is exported to its owners like any workspace store, except that the sealed credentials column of `bridge_installs` is written as `[redacted]`: those credentials are write-only everywhere. The same holds for the `code_hash` column of `bridge_link_codes` (hashed link codes): it is exported as `[redacted]`.
