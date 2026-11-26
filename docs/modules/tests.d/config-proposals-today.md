- **Owns:**

  - Server and web coverage for config proposals in Waiting on you: owner-only listing, summary and diff words, accept and reject through the existing routes, and the Today item, count and ledger.

- **Promises:**

  - The server test builds proposals through the real store and reads them through the real feed; the web harness drives the real Today mount with a small fake page.

- **How it fails:**

  - A member or agent token that sees `config_proposals`, a count that ignores them or an item that survives its decision fails here.

- **Tests:**

  - tests/test_config_proposals_today.py, tests/deck_config_proposals.mjs.
