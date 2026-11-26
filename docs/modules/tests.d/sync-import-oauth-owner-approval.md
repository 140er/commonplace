- **Tests:**

  - The live-sync import fixture (`tests/commonplace_sync_fixtures.py`) ticks the owner's client approval on the OAuth consent page, the way a real owner approves a new client, so `test_oauth_import_then_live` keeps exercising the approved-client import path.
