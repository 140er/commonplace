- **Owns:**

  - Review round 4, pairing and installer (`pairing.py`, `pairing_routes.py`, `runner/machine_setup.py`, `runner/installer.py`): a stalled setup is labelled "Not finished"; the refused-confirmation cause reads the same on the page and in the window; the window names the screen "Cloud workspaces"; another site's (or an unreadable) installation stops the command before the administrator question; a non-string `identity_digest` is a 400; unreported final steps are said instead of "Done".

- **Promises:**

  - Page and window agree on a cause; nothing is asked or changed on a machine whose installation belongs to another site or cannot be read.

- **How it fails:**

  - A manifest that cannot be read leaves the machine untouched and says so.

- **Tests:** `tests/test_machine_setup.py`, `tests/test_compute_pairing.py`.
