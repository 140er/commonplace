## verify

- **Paths:** `commonplace/verify.py`
- **Owns:**

  - `python -m commonplace.verify` runs lint on the changed Python files and the affected tests, then prints a Verify evidence block that includes the git SHA of HEAD.

- **Promises:**

  - The block is printed only after those checks pass. `--check-body` accepts that block or one line `Could not verify: <reason>`.

- **How it fails:**

  - A failing check exits non-zero and does not print a passing evidence block. A PR body with neither the block nor `Could not verify: <reason>` fails `--check-body` and says to run `python -m commonplace.verify` and paste the output.

- **Tests:**

  - `tests/test_verify.py` covers the evidence block, the PR-body rule, and which changes add the guard files.
