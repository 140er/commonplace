- **How it fails:**

  - `scripts/commonplace_release.py` looks for interface text by name; a card release that does not start from Approve, or a Memory search box that is not named Search saved memory, ends the check with a timeout and a screenshot instead of passing.
