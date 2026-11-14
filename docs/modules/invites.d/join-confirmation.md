- **Owns:**

  - `commonplace/web/static/join.js` submits the invite confirmation with a same-origin fetch and renders refused invite pages without following an error as success.

- **Promises:**

  - The join POST requires an explicit valid Origin, including when fetch metadata claims same-origin. The browser keeps the shipped no-referrer and Content-Security-Policy headers; confirmation sends the browser's Origin through a same-origin fetch. A missing, opaque or foreign Origin cannot consume an invite or replace a session.

- **Tests:**

  - `tests/test_commonplace_invites.py` checks missing, opaque and foreign Origins and the valid retry. `tests/workbench_e2e/test_invite_join.py` clicks Join under the shipped response policies at desktop and phone sizes, checks the resulting member session, and covers replay, expiry and an account change before confirmation.
