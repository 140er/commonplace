- **Promises:**

  - Workspace refresh reads the owner decision queue only when the signed-in session advertises owner management. Refused optional reads (401/403) leave the workspace usable and do not expire the session; other read failures retain the existing Retry path. Required authentication failures still request sign-in. Board, bot, memory and eligible card-review access keeps its existing server authority in both app looks.

- **Tests:**

  - `tests/test_member_bootstrap.py` executes the production refresh and Retry handlers for member and owner sessions in both looks, checks optional refusal and transient recovery, and proves the gateway still refuses the owner queue to a member.
