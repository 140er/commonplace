- **Owns:**

  - `tests/test_compute_security_regressions.py` covers script self-approval policy, taskless-scope storage refusal, durable refusal, outcome and recovery audits and rename-to-execute substitution with synthetic fixtures; IPv6 /127, /128 and endpoint normalization are covered in `tests/test_compute_scopes.py`. Cancellation fixtures resolve their synthetic job path before the held descriptor closes, and audit redaction checks exclude arbitrary content while allowing fixed outcome reason names.
