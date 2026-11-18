- **Owns:**

  - Credential guidance in the Compute gateway and target forms (`compute.js`, `compute.css`): a list of the workspace's existing credentials of the right kind to select from (name, version, account and state only), a plain-words check of the typed or chosen reference, and, when none exist, the list cannot be read, or the reference is missing, revoked, expired or for the other kind, a copyable operator step that is the one documented in `docs/compute/RUNNING.md`. The manual reference and version fields stay, and saving is never blocked by this guidance.
- **Promises:**

  - The form never accepts, stores or shows a secret: the list carries no key, no public key and no bindings, and the copyable step only fills values that pass a strict name, account and version pattern. A short paragraph explains what verification means before the host-key confirmation, which is still required and still withdrawn by any edit.
