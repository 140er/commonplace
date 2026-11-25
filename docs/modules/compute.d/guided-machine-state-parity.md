- **Owns:** The shared guided-machine state projection in pairing.py and the overview's unused-capacity suppression.
- **Promises:** Add a machine, Overview and Admin use the pairing's waiting, unfinished and checked state words; an attestation does not remove the setup-only limitation.
  Read-only callers reuse their own database connection and never initialize pairing tables.
  Hiding unused capacity facts preserves a developer runner's recorded runs and resource reports.
  Accepted setups with incomplete checks retain the guide's Approved or Not finished state; only completed checks yield Set up and checked.
- **How it fails:** A failed or stalled setup stays Not finished with its recovery step; missing pairing tables yield no guided projection.
- **Tests:** tests/test_compute_pairing.py and tests/test_compute_overview.py pin state parity, after-attest behavior, developer-route attestation and omitted capacity figures.
