- **Tests:**

  - `tests/test_commonplace_public_surface.py` classifies `GET|POST /api/setup-proposals` and the `GET /api/setup-proposals/` prefix (card #675) as authenticated, so an anonymous request gets 401 before any validation.

  - `tests/spaces_surfaces.json` pins `setup_propose` and `setup_show` (names, descriptions and input schemas) for the standard and full profiles; the slim profile does not list them.
