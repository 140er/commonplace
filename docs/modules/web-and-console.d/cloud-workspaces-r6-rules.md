- **Owns:**

  - The browser copy of the image and name rules in `commonplace/web/static/compute-workspaces.js` follows the server rules added after round 5: a bare local suffix (`home.arpa/app`) is private, text after `@` that is not a digest is a login, a lone dotted repository (`node.js`, `my.app:1.0`) is accepted while `ghcr.io:443` is still a website name without an image, and U+FFFC is a hidden name character (both read the shared fixture in `tests/fixtures/compute_workspace_inputs.json`).
- **Promises:**

  - A person is never told a valid repository name needs a path, and is told the true reason for a login typed before an image.
- **How it fails:**

  - A browser rule that drifts from the server fails `tests/compute_workspaces_ui.mjs`, which checks every shared accepted and refused input.
- **Tests:** `tests/compute_workspaces_ui.mjs` (via `tests/test_compute_workspaces_ui.py`).
