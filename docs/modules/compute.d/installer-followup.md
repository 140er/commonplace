- **Owns:**

  - `target_installer.py` follow-up: uninstall stops the account's processes before it discards workspace data or removes anything (a failed stop removes nothing), the stdlib-only import is checked before the `.pth` is written, a non-empty recorded directory makes uninstall a non-zero partial result that stays in the manifest, and the plan states its read-only probe.
