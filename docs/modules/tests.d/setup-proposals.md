- **Tests:**

  - `tests/test_commonplace_public_surface.py` classifies `GET|POST /api/setup-proposals` and the `GET /api/setup-proposals/` prefix (card #675) as authenticated, so an anonymous request gets 401 before any validation.
