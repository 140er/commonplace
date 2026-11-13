# Browser approvals for runner setup

Slice 5 of [the installer plan](design/runner-box-installer.md) adds browser
consent, private access delivery and final fingerprint confirmation. Setup stays
off by default. This slice does not enable execution; supervised stop and lease
proofs in slice 6 remain required.

## What the owner does by hand

1. In the browser or app, enable runner setup, review the selected projects,
   topics, providers and access limit, and choose **Add runner** once.
2. On the intended machine, run the generated command and accept its installation
   scope once. Keep the displayed machine fingerprint available for comparison.
3. In **Needs me**, compare the fingerprint with the machine, then choose
   **Allow sign-in** for each selected provider with a supported device flow.
   Choose **Show sign-in link and code**, open the provider's own website on any
   device, sign in to the intended subscription account and enter the displayed
   short code there. Never enter a credential or a returned login code on the
   runner machine. The short code is displayed once; after a lost or expired
   presentation, wait for a fresh request.
4. In **Needs me**, review the machine fingerprint, projects, topics and access
   limit, confirm the comparison and choose **Approve access**. Delivery to the
   machine is automatic; there is no secret to copy, type or carry.
5. When **Confirm this machine** appears, compare every fingerprint group again,
   confirm the comparison and choose **Confirm matching machine**. This records
   the owner's attestation, not independent machine verification. Execution stays
   disabled until the lifecycle prerequisites are implemented and proved.

The counts are one Add action and one installation accept, followed by one
browser consent and one provider website sign-in per missing supported login,
one access approval and one final machine confirmation. Existing subscription
credentials are not copied or silently reused. There is no additional terminal
prompt, password entry or configuration edit.

## Compatibility limits

The device-capable subscription CLI documents a dedicated device login command;
its own process exchanges the device authorization and writes its credentials in
the private runtime home. The hub receives only a signed success fact. Access
keys and alternate billing paths are not used.

The other selected subscription CLI documents browser OAuth with a local
callback and a fallback that requires pasting a returned code into the terminal.
Its checked login command offers no device-code option. That remote login path
is **unsupported** here: no callback proxy, terminal-code relay, credential copy
or billing fallback is invented. Its Needs me item states the limitation and
setup stays incomplete. Sources: [device authentication](https://learn.chatgpt.com/docs/auth)
and [browser authentication](https://code.claude.com/docs/en/authentication).

The access file minted by the hub contains a random secret and a signed exact
scope: machine key, workspace, installation, provider instance, projects, topics,
wings, requesters, versioned binding and access limits. It is a dormant host
integration contract. Compatibility with a real host issuer and enforcement of
the scope on direct host calls remain release blockers, along with the lifecycle
proofs. A synthetic issuer or a successful delivery is not proof of live access.

## Authority and recovery

Only the enrollment's current person owner can decide its browser requests.
Browser writes require JSON and the existing same-origin protection. Machine,
worker and other connected credentials cannot approve, reveal a short code or
perform final confirmation. All successful decisions record the person in audit.
Machine reports require the enrolled private key and current machine credential.

Requests expire after five minutes and freeze a digest of the complete approved
scope. Decisions consume one pending state. Changed scope, settings, key,
workspace or binding versions invalidate old decisions. Final confirmation uses
the existing owner attestation route and refuses missing logins, unacknowledged
access delivery, a wrong fingerprint or replay.

Short provider codes exist only in bounded, expiring process memory until one
owner read; only a hash is stored in the database. A restart loses the display,
requiring a new request after expiry. Provider access and refresh credentials
never reach the hub. The login process has closed standard input, an isolated
private home, no inherited credentials, bounded output and a hard deadline.

Access secrets are stored as a verifier hash and machine-key ciphertext in the
hub. The only plaintext persistent copy is the machine's private access file,
mode 0600 in its 0700 installation directory. Acknowledgement follows a durable
write and removes ciphertext. Lost fetch responses recover the same ciphertext;
replay never mints another secret. The access file expires after ten minutes.
Renewal requires fresh browser consent; there is no silent renewal or permission
expansion. These are setup lifetimes, not execution leases.

Revoke and Disable runner setup invalidate machine credentials, login status,
pending decisions and access verifiers together. Current reads immediately
refuse revoked authority. Offline process termination is not claimed by this
slice. The setup service clears private cached access after a refusal and stops
its owned login process. Re-enabling revives no previous approval. Export
redacts verifiers and ciphertext; account deletion removes the dependent rows.
