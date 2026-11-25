- **Owns:** `tests/test_console_guards.py` pins `GET /p/` as a public route (the preview route checks the session itself) and `tests/test_commonplace_public_surface.py` classifies it as public.

  - Original direct links into a `files/` directory keep the nested file, with both a missing root counterpart and a different root counterpart; transport under reserved `~files/` cannot shadow either. Browser cases keep the reserved frame path and the tab-to-viewer redirect.
