- **Owns:**

  - The browser copy of the image and name rules in `commonplace/web/static/compute-workspaces.js` follows the server: `user_info` and `registry_ip` have their own messages, the resolver names and suffixes are private, and U+180F is a hidden name character (both read the shared fixture in `tests/fixtures/compute_workspace_inputs.json`).
- **Promises:**

  - A person is told the true reason for a refused address: a login in the address, a public IP address and a private network are three different messages.
- **How it fails:**

  - A browser rule that drifts from the server fails `tests/compute_workspaces_ui.mjs`, which checks every shared accepted and refused input.
- **Tests:** `tests/compute_workspaces_ui.mjs` (via `tests/test_compute_workspaces_ui.py`).
