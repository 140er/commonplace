### Socket-activated SSH target provisioning

SSH validation needs `/run/sshd` even before a socket-activated daemon has started.
The target installer therefore records and prepares that directory before checking
effective configuration, then provisions the account only after the check passes.
A rejected configuration can leave this recorded preparation for uninstall.
Drop-in installation and removal use `try-reload-or-restart`, which leaves an
inactive daemon inactive and lets the next connection read the current config.
Uninstall refreshes SSH before removing a runtime directory it created and before
deleting the manifest; it retains pre-existing runtime directories. The staged
installer and fake live-host tests exercise this without network access.
