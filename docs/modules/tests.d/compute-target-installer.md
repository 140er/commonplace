- **Tests:**

  - `tests/test_compute_target_installer.py` runs the Compute target installer against a staged root only: plan, exact installed tree and modes, sshd drop-in content, no-op second install, manifest uninstall to an empty root, refusals and the key-pin seam. It needs no root, no system command and no network. Follow-up cases cover stopping the account before discarding data, the `.pth` written only after the import check, a kept directory reported as a partial uninstall and the plan's read-only probe wording.
