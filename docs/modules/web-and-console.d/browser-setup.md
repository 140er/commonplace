- **Owns:**

  - Needs me browser consent cards for supported sign-in, machine access and final fingerprint confirmation.

- **Promises:**

  - Cards use plain labels, 24px titles, phone gutters and a deliberate fingerprint comparison. Opaque identifiers stay out of visible copy. A short provider code is shown once through a person-only action; provider credentials and access secrets are never displayed.
  - Disabled setup contributes no cards, counts or new navigation. Unsupported sign-in offers no workaround.

- **Tests:**

  - tests/test_runner_setup_browser.py verifies the browser-free action contract and optional browser layouts, consent actions and captures; existing Needs me wrappers preserve navigation and filters.
