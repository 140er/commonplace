- **Owns:**

  - `.github/workflows/verify-evidence.yml` runs `python -m commonplace.verify --check-body` and fails the pull request unless the body has a Verify evidence block with a git SHA or a line `Could not verify: <reason>`. `.github/PULL_REQUEST_TEMPLATE.md` has that section.
