- **Owns:**

  - Owner-visible supervisor stop evidence in the existing runner screen.

- **Promises:**

  - Revoked; stop unconfirmed and Stopped are distinct escaped states. An unconfirmed stop remains in Needs me; a signed termination acknowledgement resolves it. Owner attestation remains separate from host verification.

- **How it fails:**

  - Revocation alone never displays Stopped. No lifecycle rows or navigation appear while setup is disabled.

- **Tests:**

  - tests/admin_runners_ui.mjs through tests/test_admin_runners_ui.py and lifecycle owner projections.
