- **Tests:**

  - `tests/test_commonplace_public_surface.py` pins `PUT /api/bots/` (and so the shared-bot access routes `GET`/`PUT /api/bots/<id>/access` and `GET .../access/log`) in the installed-route inventory as authenticated; `tests/test_bots_access.py` covers the effective-access rule and those routes.
