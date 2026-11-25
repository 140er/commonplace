- **Tests:**

  - `tests/test_commonplace_oauth_approval.py` exercises real registration, consent and token routes: no implicit approval, owner/member/agent fences, stale roles and sessions, workspace/client isolation, request/code/refresh replay, atomic rollback, migration and established-token continuity, schema classification and member-removal cleanup. Existing OAuth, import, phone, bot and decision fixtures explicitly approve clients before testing approved-client behavior. Chromium consent checks tick the new owner approval separately from optional scope consent.

  - The OAuth top-level actor regression obtains its connection through real registration, owner approval and token exchange, retaining its parent and creator assertions without bypassing authorization.
