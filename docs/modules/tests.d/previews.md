- **Owns:** `tests/spaces_surfaces.json` pins the three preview tools in the standard and full profiles, and `tests/test_commonplace_public_surface.py` classifies `/api/previews` and its prefix as authenticated.

- **Tests:** `tests/_commonplace_account_helpers.py` has `Site.publish_preview`, a published preview as publish leaves it, and `tests/test_commonplace_account_export.py` checks that an owner's export carries its rows and no other workspace's.
