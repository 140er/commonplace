# Owner-supplied host issuer pin

The owner supplies the host issuer's public key through the install plan, using
`--host-issuer-pin issuer-pin.json`. The JSON has exactly two fields: `issuer`
(a short issuer name) and `public_key` (an Ed25519 public key in PEM format).
The source must be an owned or system-owned regular file without group or other
write permission. A symlink, private key or malformed file is refused.

Before accepting, the owner reviews the issuer name and grouped SHA-256 public
key fingerprint shown by `--plan`, comparing it with the independently trusted
source that supplied the key. The existing single `--accept` accepts this plan
along with the rest of setup. No additional machine approval prompt is added.
The standalone launcher snapshots the reviewed key before launching the signed
installer, so a later change to the source file cannot select another key.

The installer records the owner selection separately from enrollment and stores
the normalized pin in its private journaled state with mode 0600. Reinstall must
select that same pin; replacing or removing it requires uninstall and acceptance
of a new plan. Uninstall removes only the owned pin. Without a supplied pin,
setup can continue, but no scoped grant reaches the host producer and no host
signature is accepted locally. Enrollment metadata, machine keys, response keys
and the hub's approval key cannot supply or substitute for this pin.

Local status acceptance checks the owner pin, a fresh signed binding and scoped
grant, the host signature, nonce, identity, exact scope digest, expiry and watchdog
bound. A scoped preflight also checks the selected project, topic and runtime
ceiling against the approved scope. These checks return setup-only facts, never
execution permission. A changed or missing pin, unknown issuer, stale scope or
out-of-scope request fails closed. Every lifecycle exchange checks status again
before forwarding it; an explicit status argument cannot bypass the local pin.
The hub independently verifies against its existing trusted configuration. A
signed hub assertion alone cannot mark local verification successful. Stop,
revocation and cleanup remain available without a host pin.

Execution remains disabled even when both signature checks pass. Claude and Codex
authorization are outside this change; Codex remains unproved. No login or
standing enrollment is required for the disposable proof.

## Separate native host follow-up

S9 must implement the native boundary that denies direct requests outside the
granted project/topic/runtime ceiling, invalidates grants on revocation and bounds
offline work with the existing watchdog. The interface is the bounded private
stdin `runner-status` request and nonce-bound signed response documented in the
[runtime producer guide](runner-runtime-issuer.md). It must work with sealed
executable invocation and must not expose a grant through arguments, environment
or output. The approved release digest remains a separate integrity requirement.

The pin source is the explicit owner selection above, never the enrollment
record. Before native deployment, decide who provisions, protects and rotates the
corresponding signing key and how its public key reaches the owner through an
independently authenticated path. The hub must also retain its independent trust
configuration; the machine cannot install a hub trust root.

Acceptance needs live signatures from the actual host, wrong-pin and stale/replay
refusal, direct out-of-scope project/ceiling denial, and connected/offline stop
proof with unrelated work preserved. Use only a disposable enrollment and revoke
it afterward. A synthetic signing fixture or local scope preflight does not prove
native enforcement, provider authorization or execution readiness.
