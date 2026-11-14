- **Owns:**

  - `docs/design/compute-target-one-click.md` proposes target provisioning for card #448, reusing the runner enrollment lifecycle and recording existing disposable VM evidence without new machine access.

- **Promises:**

  - The proposal keeps setup off and invisible, separates one sudo accept from browser verification, requires signed package integrity and local passkey approval checks, and leaves the existing offline installer contract intact.

- **Tests:**

  - Documentation obligations and fragment validation cover this docs-only note; six later slices require focused enrollment, package, dependency, signature, browser verification and stop proofs before enablement.
