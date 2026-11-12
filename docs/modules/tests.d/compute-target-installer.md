- **Tests:**

  - `tests/test_compute_target_installer.py` runs the Compute target installer against a staged root only: plan, exact installed tree and modes, sshd drop-in content, no-op second install, manifest uninstall to an empty root, refusals and the key-pin seam. It needs no root, no system command and no network.
