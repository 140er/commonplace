- **Owns:**
  Repository identity selection for visible board cards linked to pipeline PRs.
- **Promises:**
  Normalize repositories and honor explicit `COMMONPLACE_REPO_ALIASES` old/new pairs in either direction. A unique matching PR repository wins; board repositories apply only to cards missing their PR repository. Workspace and wing isolation and first-doing timestamps are preserved.
- **How it fails:**
  A populated conflicting PR repository is excluded even when the board matches. Ambiguous matches and cards without repository information remain No card; PR-number uniqueness never establishes identity. Owners must migrate renamed card repositories or configure explicit aliases.
- **Tests:**
  `tests/test_pipeline_api.py` covers wrong-repository singleton rejection, normalization, explicit aliases, board fallback, ambiguity, isolation and the first doing timestamp.
