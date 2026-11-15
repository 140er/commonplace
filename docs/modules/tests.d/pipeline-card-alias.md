- **Tests:**

  - `tests/test_pipeline_api.py` also covers the card lookup: a card linked under a repository's old name, two cards with one number (exact repository preferred, none fitting, the board that holds the repository), `COMMONPLACE_REPO_ALIASES` read both ways with junk ignored, a card that never was doing starting at its creation, and no card borrowed from another workspace. The old-name, board and alias cases fail on the previous lookup.
