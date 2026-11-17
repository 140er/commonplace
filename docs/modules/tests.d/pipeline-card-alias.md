- **Owns:**
  Pipeline API regressions for repository-scoped card selection.
- **Promises:**
  Neutral fixtures reproduce a unique wrong-repository card with the same PR number. URL/name normalization and explicit aliases in both directions retain valid matches; only missing card repositories permit matching board fallback.
- **How it fails:**
  Wrong-repository singletons, conflicting card/board metadata, missing repository information and multiple matching candidates must not borrow a card. Isolation failures or changing the first doing timestamp fail assertions.
- **Tests:**
  `tests/test_pipeline_api.py` selects all API cases, including no card, two repositories sharing a PR number, workspace and wing access, explicit renamed repositories and repeated doing transitions.
