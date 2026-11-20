- **Owns:** The owner machine listing's guided projection and the unconfigured-card setup refusal.
- **Promises:** Guided machines retain their pairing state after an attest; an unattested developer machine directs its owner to Admin instead of creating another machine.
  Token enrollment and developer attestation keep their existing authority checks.
- **How it fails:** Members and agent credentials cannot manage machines; setup-only machines remain unable to take card work.
- **Tests:** tests/test_compute_pairing.py, tests/test_runner_owner.py and tests/test_card_start_defaults.py cover the real owner routes and actionable refusals.
