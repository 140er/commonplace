- **Owns:**

  - `tests/test_even_g2_bridge.py` runs the G2 glasses bridge's Node suite (`integrations/even-g2/bridge/test`): every allowlisted route forwards with the token upstream only, routes outside the allowlist (including traversal and other methods) never reach the hub, a hub that echoes the token gets it redacted, query and body fields are filtered, the pairing key and CORS origins are enforced, and static serving cannot leave its directory. It skips when `node` is missing.
