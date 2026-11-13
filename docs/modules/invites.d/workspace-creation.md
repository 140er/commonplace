- **Owns:**

  - `workspace_creations` remembers each person's workspace request and creation time. The workspace switcher offers name, confirmation, result and a separate switch action in both looks.

- **Promises:**

  - `POST /api/workspaces` requires an actual signed-in browser session and an explicit same-origin Origin. Bearer credentials are refused, including person-shaped app grants. Names use `config.sanitize_name`.

  - Creation calls the same `ControlDB.create_workspace` and plan assignment hook as signup. It adds only the creator as owner, with no imported data or members. The current session stays in its workspace until the person chooses Open workspace.

  - Request ids are scoped to the person and committed with provisioning. Replaying the same name returns the same workspace; changing the name on that request is refused. Five creations per person per rolling hour is enforced durably. Signup closed also closes new workspace creation. Existing plans are preserved; plan-hook refusals roll back provisioning and show their reason. No per-person workspace count cap is currently configured by the plan module.

- **How it fails:**

  - Validation, signup policy, request conflicts and rate refusals show plain reasons. Failed attempts are audited without the submitted name. Retrying an uncertain response uses the same request id; cancelling confirmation sends no create request.

- **Tests:**

  - `tests/test_workspace_creation.py` proves session authority, CSRF refusal, plan preservation and refusal rollback, durable limits and request replay, owner-only membership and data isolation through real HTTP.

  - `tests/test_workspace_creation_ui.py` and `tests/workspace_creation.mjs` prove the switcher journey without a browser, including cancel, duplicate click, safe rendering and retry. `tests/test_workspace_creation_browser.py` optionally runs the real shell and session journey in both looks at desktop and phone widths.

  - Same-second workspace creation preserves the original sign-in home; personal workspace billing stays associated with its creator.

  - The Deck design round uses a 400px dialog with 24px padding, aligned workspace rows and a quiet creation action. Current membership has a tick without disabled dimming. Confirmation and result each use one short paragraph; a refused create shows its reason and Close only. Known closed-signup and hourly-limit messages have plain Deck wording; other server reasons are preserved. Server routes and Classic behavior are unchanged.
