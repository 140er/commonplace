### Compute target installer

`commonplace.compute.target_installer` (`plan`, `install`, `uninstall`) provisions the
script target that `target_helper.py` and `script_sandbox.py` document as operator
prerequisites. A real install runs once as root and needs no later prompt; it reads
no secret, contacts no server and installs no package (bubblewrap, systemd, sshd and
the importable package are preflight requirements). Steps record into a root-owned
manifest before they act: dedicated account, private workspace with attempts/inputs,
bounded tmpfs scratch via a systemd mount unit, root-owned runtime tree bind-mounted
read-only (its `runtime_inventory` is the config digest), a fixed ForceCommand entry
that runs the helper in a `Delegate=yes` scope with the runner/fixture cgroup layout,
a root-owned copy of the commonplace and mempalace python sources under
`/opt/commonplace-compute/lib` plus a `.pth` in the system python's local dist-packages
(import checked with `-I`, stdlib only), an sshd drop-in whose Match block uses only
directives sshd allows there (`ExposeAuthInfo yes`, no AcceptEnv of the auth-info
variable, root-owned authorized key; global `PermitUserEnvironment no` is verified with
`sshd -T`), an optional approval-key pin through the
`pin_approval_keys` seam (#431 format pending) and, last, the protected config.
Install is a no-op when already complete; uninstall removes only manifest entries,
keeps changed files and attempt evidence, and stops the account's sessions and user
manager before `userdel`. Each directory, file and the account is recorded before it is
created (an interrupted run resumes or uninstalls cleanly) and bytecode that later imports
add to the placed source tree is ignored by the comparison and removed by uninstall. `--root` stages without root or system
commands. A real root install, idempotent reinstall, confined run and clean uninstall were
verified on a disposable Ubuntu 24.04 VM.
