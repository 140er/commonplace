- **Owns:**

  - `docs/compute/disposable-sandbox-proof.md` records #370 isolation approval, offline opt-in commands, fixture qualification and the distinction between actual confinement and synthetic SSH transport verification. `docs/compute/run-disposable-sandbox.sh` launches only explicitly configured disposable tests in a transient delegated systemd scope, with a runner child and sibling sandbox subtree; temporary config and test files stay inside the fixture.
