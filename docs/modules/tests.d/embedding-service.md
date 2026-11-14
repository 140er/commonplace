- **Owns:**

  - `tests/test_embedding_service.py` runs a real service on a temporary Unix socket (skipped where the platform has none) with a stub model, plus real-MiniLM cases that skip when the model is not installed and never download it; it is on the real-embedding list in `tests/conftest.py`.
