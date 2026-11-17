## verify

- **Paths:** `commonplace/verify.py`
- **Owns:**

  - `python -m commonplace.verify` runs lint and format checks on changed Python files, affected tests, and the four repository guard files, then prints a Verify evidence block that includes the git SHA of HEAD.

- **Promises:**

  - The block is printed only after those checks pass. Tests are selected as explicit file paths; the command never falls back to the full suite.

- **How it fails:**

  - A failing check exits non-zero and does not print a passing evidence block. A missing tool or base ref prints `Could not verify: <reason>`.

- **Tests:**

  - `tests/test_verify.py` covers the evidence block, affected-test selection, mandatory guard files, and failing narrow checks.
