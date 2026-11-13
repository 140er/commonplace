- **Owns:**

  - Browser-consent security witnesses, a browser-free UI action harness and optional browser captures for runner setup.

- **Promises:**

  - All identities, issuer material and credentials are synthetic. Tests prove scope, replay, expiry, revocation, secret isolation and final fingerprint prerequisites without a real provider login or host execution claim. Browser absence skips only the browser cases.

- **Tests:**

  - tests/test_runner_setup_contract.py provides the base/head behavioral witness. tests/test_runner_setup_approvals.py covers authority and delivery. tests/test_runner_setup_browser.py runs with and without a browser, with existing affected installer, binding, account, public-surface and documentation guards.
