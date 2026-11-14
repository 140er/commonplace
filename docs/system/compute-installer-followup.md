### Compute target installer follow-up

Uninstall stops the account's sessions, user manager and processes first and only then
discards workspace data or removes anything else; if the processes cannot be stopped it
removes nothing and reports that. A recorded directory that is not empty makes uninstall
exit non-zero as a partial result and stays in the manifest, so a later uninstall retries
it. The `.pth` is written only after the stdlib-only import check passes. `plan` says it
runs a read-only probe and changes nothing on disk.
