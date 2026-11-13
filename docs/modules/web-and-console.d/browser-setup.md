- **Owns:**

  - Needs me browser consent cards for supported sign-in, machine access and final fingerprint confirmation.

- **Promises:**

  - Cards use the real Deck shell, 24px titles, a 720px maximum width, 16px phone gutters and inner padding, selectable eight-character fingerprint groups in a 4/2 grid, distinct action titles, machine-specific step counts, exclusive form/result states and a deliberate comparison. Refusal has one confirmation step and revokes enrollment. Opaque identifiers stay out of visible copy. A short provider code is shown once through a person-only action; provider credentials and access secrets are never displayed.
  - Disabled setup contributes no cards, counts or new navigation. The supported terminal sign-in card gives the exact command, three numbered instructions and the promise "Commonplace never sees this code." It resolves only from signed machine status.

- **Tests:**

  - tests/test_runner_setup_browser.py verifies the browser-free action contract and optional browser layouts, consent actions and captures; existing Needs me wrappers preserve navigation and filters.
