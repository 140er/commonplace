- **Promises:**

  - `target_installer.py` records and creates a missing `/run/sshd` before `sshd -T` and `sshd -t`; it uses `try-reload-or-restart` on install and uninstall so an inactive socket-activated SSH service stays inactive. Runtime directories created by the installer are removed after the uninstall refresh; pre-existing directories stay untouched.

- **How it fails:**

  - Effective SSH configuration refusal happens after manifest-backed runtime preparation but before account provisioning. Uninstall can reverse that preparation; unsafe configuration never activates a target.
