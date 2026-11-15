- **Promises:**

  - `GET /api/admin/instance/deploy` reports the running `commit`, a 12-character `commit_short` and `commit_source`: `build` for the image's build marker, `env` when the marker is empty and `COMMONPLACE_COMMIT` holds a valid full commit, otherwise `unknown`. Admin shows the short commit, the full commit and which source named it.

- **Tests:**

  - `tests/test_admin_instance_api.py` and `tests/test_admin_instance_ui.py` cover the environment fallback, the build marker winning and invalid values being ignored.
