# Compute target one-click provisioning: design note

Status: proposed. Card #448, child of #430. Related: #431 and #449/#450.
Docs only: this note installs nothing, changes no setting and enables no target.

## 1. Problem and goal

- Target setup currently requires four copied values, an existing bubblewrap,
  a trusted root-owned package and an approval-key pin that is not implemented.
- Goal: in `#/compute`, **Add target** gives one complete command or signed package.
- Running it once uses **one sudo accept**, covering dependencies and provisioning.
- Enrollment verification and later approvals finish as browser/app **Needs me**
  items, with no subsequent confirmation or secret entry on the machine.
- Setup remains off and invisible until opted in; incomplete setup executes nothing.

## 2. Existing contracts, scope and recorded VM facts

Build on `commonplace/compute/target_installer.py` and
[the running guide](../compute/RUNNING.md). Its offline `plan/install/uninstall`
commands fetch nothing and install no packages. They require workspace, gateway
and node ids plus one controller SSH public key. Install records owned artifacts
before creating them, refuses foreign paths and activates the protected target
configuration last. Reinstall with identical settings is a no-op; uninstall
preserves changed files and execution evidence and stops the target account first.
Keep these properties and the offline core; add a verified provisioning wrapper.

`pin_approval_keys()` still raises `NotImplementedError` pending #431. There is
no shipped passkey approval-keyset format here. Existing Ed25519 provisioning
receipts, SSH host/controller keys and credential-wrapping keysets are different
authorities, and cannot substitute for the owner's passkey approval keys.
`executor.py` accepts independently signed `EnrollmentReceipt` objects through
a private provisioning seam; `script_control.py` separately verifies runtime
receipts. Current management routes cannot mark a node verified. A browser approval
in this proposal requests verification; it does not turn a checkbox into evidence.

Reuse [the runner installer design](runner-box-installer.md), whose enrollment
backend now exists in `commonplace/runner/enrollment.py`, `enrollment_api.py` and
`enrollment_crypto.py`: 256-bit codes, SHA-256 hash storage, five-minute expiry,
transactional consumption, key-bound encrypted recovery for ten minutes and
one-use 60-second recovery challenges. Its current exchange creates runner
credentials and has a runner-specific payload. Extract that lifecycle into shared
enrollment primitives with an explicit purpose and a Compute-specific adapter;
do not send Compute intent to the runner exchange or mint runner credentials.
Coordinate this extraction with the runner installer slices rather than introduce
another enrollment algorithm or couple the two opt-in switches.

### Read-only survey from existing documentation

No remote connection, new probe, installation or setting change was performed
for this note. These are recorded facts, not a fresh machine qualification.

| Source | Recorded fact and limit |
|---|---|
| [Installer record](../system/compute-target-installer.md) | A disposable Ubuntu 24.04 VM exercised root install, identical reinstall, confined execution and clean uninstall. This does not prove the proposed bootstrap. |
| [Runtime requirements](../compute/script-integration.md#confinement-verification-requirements) | Confined execution additionally requires the bubblewrap `--disable-userns` fix. The card records stock 0.9 as unsuitable; merely finding an installed package is insufficient. |
| [Confinement record](../compute/disposable-sandbox-proof.md) | Non-root namespaces, protected read-only runtime, delegated CPU/memory/process controllers, writable fixture termination control and 64 MiB/4096-inode tmpfs were qualified. The installer default is separately 32 MiB/4096 inodes. |
| Same confinement record | Initial positives failed before script execution. A delegated transient scope matching the launch contract then passed the actual confinement positive and three synthetic SSH cases: four passed in 1.99 seconds. |
| Same confinement record | SSH cases use socketpairs; they prove pin checks and transport wiring, not live network SSH integration. Synthetic signed receipts alone never prove confinement. |
| [Running guide](../compute/RUNNING.md#provision-a-script-target) | Socket-activated SSH may have an inactive service and no runtime directory. Preserve that state; validate effective configuration and refresh only as the existing installer does. |

The runner box survey in the sibling note describes a different machine. Its
CPU/disk/installed-command observations do not establish compute VM readiness.
Older build notes recording an unverified sandbox predate the successful delegated
scope record; neither record certifies arbitrary fresh targets.

Non-goals: a remote shell, network scanning, provisioning a new gateway, runner
or provider installation/login, OS upgrades, bypassing runtime verification,
automatic scope grants, recovery by deleting uncertain claims, or changing the
current manual/offline installation contract. Existing control-plane custody,
gateway enrollment and verifier service must already be provisioned. Missing
infrastructure is a maintainer setup blocker, never another target-side prompt.

## 3. Data model and surfaces

All following Compute records and routes are proposed. Use existing opaque ids,
UTC expiry and guarded workspace transactions; add no second node inventory.

| Record | Fields and invariants | Read/write authority |
|---|---|---|
| `compute_install_settings` | `workspace_id` PK, `enabled` default 0, `version`, actor/time; absent or invalid means off | Current person owner writes; admission reads |
| Shared enrollment intent, purpose `compute-target` | `id`, workspace/owner/request ids, reserved `node_id`, `gateway_id`, `code_hash`, intent/envelope digest, settings version, expiry/consume/revoke times, installation id, recipient-key digest; one outstanding intent per workspace/node | Owner issues/revokes and sees status; narrow exchange consumes; no bearer token sees hashes/codes |
| `compute_install_manifests` | enrollment id, schema version, workspace/gateway/node, gateway enrollment digest/epoch, controller public key and digest, approved route ceiling, release id/digest, approval-keyset digest, approved dependency plan, expiry, signed owner intent | Owner approves; exact enrolling target fetches; immutable after issuance |
| `compute_target_installations` | installation/enrollment/workspace/node ids, recipient public-key digest, phase, artifact-manifest digest, target SSH fingerprint, runtime digest, installed dependency versions, verification-request id, revocation epoch, fixed failure code | Target reports bounded observations; owner reads; reports grant no authority |
| Shared encrypted delivery/recovery | enrollment purpose/id, workspace/installation ids, recipient key, ciphertext/context, expiry, challenge hash/expiry, acknowledgement time | Issuer writes; bound key recovers/acknowledges; purge on acknowledgement, expiry or revoke |
| Local approval state | versioned keyset, keyset digest/epoch, accepted approval nonces/sequences, signed receipts, install phase journal and artifact inventory | Root-owned trusted setup/policy component writes; unprivileged execution cannot replace it |

Reserve a node id before generating the command. Current `create_node` requires
a route and host pin; do not fake either to reserve an id. Store the reservation
in enrollment intent, excluded from verified inventory. Finalization creates that
exact node with the independently checked route/pin in the existing Compute store.
Code consumption and installation reservation commit atomically; finalization
under the existing fence is idempotent and checks the original reservation. Never
mark verified or release a credential on an uncommitted cross-store transition.

Reuse existing Needs me requests and audit conventions for install verification,
key rotation and changed scope. Audit ids, digests, versions and fixed outcomes,
not commands, enrollment codes, private keys or credential ciphertext. Classify
new records for account export/deletion; exports omit code/challenge hashes and
delivery payloads. Delete only through existing retention and revocation rules.

Surface names only; none is claimed as available:

- Owner: `GET/PUT /api/compute/install-settings`,
  `POST /api/compute/target-enrollments`,
  `GET/DELETE /api/compute/target-enrollments/<id>`,
  `POST /api/compute/target-enrollments/<id>/decide`.
- Code-only bootstrap: `POST /api/compute-target-enrollment/exchange`.
- Bound-key recovery: `POST /api/compute-target-enrollment/recover`.
- Target-self: `GET /api/compute-target/install-manifest`,
  `POST /api/compute-target/install-status`,
  `GET /api/compute-target/approval-receipts`.
- CLI: verified provisioning wrapper plus existing `plan`, `install`, `uninstall`.
- MCP: no installation, key pinning, code issuance or verification-decision tool.

Code-only/recovery routes need exact-path authentication exceptions, bounded
bodies and shared durable throttling (initially ten attempts/minute/workspace/
purpose). Reject cookies and bearer credentials there; possession of an existing
connection token is not enrollment authority. Recover requires the bound key,
not another use of the consumed code. Target-self requests prove possession of
their installation key and fresh server challenge, and reach only that install.

## 4. One command and enrollment flow

1. **Prepare in the browser.** An owner explicitly opts in, chooses an existing
   verified gateway and a target label, and reviews the route ceiling, package,
   dependency actions and approval-keyset fingerprint. Register a passkey here
   when absent. Workspace and gateway ids come from current owner scope; reserve
   the node id server-side. No id or public key is copied by hand.
2. **Add target once.** Freeze that intent and issue a single-use code bound to
   purpose, workspace, gateway, reserved node, manifest digest and settings version.
   Store only its hash. The first response alone contains the code; issuance
   retries return status, and changed intent conflicts. Losing the command needs
   browser revoke/reissue, never plaintext recovery.
3. **Render one complete command.** Carry a bounded, versioned enrollment envelope
   containing those ids, authenticated hub origin, the literal `ssh-ed25519`
   controller **public** key, its SHA-256 digest, approval keyset/digest, package
   digest/release id, signed intent and expiry. The short-lived code travels on
   private stdin to the verified launcher, separate from public envelope fields;
   never place long-lived tokens or private keys in the command. Encode data as
   strict JSON/base64url, never executable shell fragments or interpolated paths.
   The downloaded package alternative carries the same envelope and expiry in a
   private sidecar; modifying the signed executable to embed per-install data
   would invalidate its release signature.
4. **Verify before elevation, accept once.** An authenticated launcher stages
   and verifies the signed package, then uses one `sudo` invocation for the whole
   declared plan. Root independently rechecks immutable staged bytes. The one
   accept covers only the displayed installation scope; no extra local dialog,
   package question, service enable prompt or later confirmation is allowed.
   Unsupported prerequisites stop safely, without redeeming the code. Code
   expiry during downloads requires browser reissue, never a second local accept.
5. **Redeem and bind.** Persist a private installation id/key before exchange.
   Reuse the shared RSA-3072/4096 recipient-key and encrypted-delivery contract.
   Exchange validates the complete frozen binding, expiry, current owner and
   settings, then atomically consumes once and reserves one installation. Return
   only setup authority, encrypted to this key; it cannot dispatch work or enroll
   another node. At every later phase recheck gateway epoch, keyset/settings and
   revocation. A changed binding stops setup and returns to browser Needs me.
6. **Provision inside that accept.** Install only approved missing dependencies,
   place the trusted package root-owned, materialize the controller public key
   and verified approval-keyset as protected files, and call the existing installer
   with absolute isolated interpreter and all four generated values. No user
   checkout or editable package is imported as root. Keep execution disabled
   until key pinning succeeds; never omit `--approval-keyset` to bypass #431.
7. **Verify in Needs me.** Report target SSH fingerprint, route, package/runtime
   digests and qualification observations. The owner compares the browser's
   fingerprint with the non-secret completion fingerprint printed by the verified
   installer, then signs the exact binding with a passkey. An independent verifier
   checks the pinned two-hop route, protected config, exclusive controller key
   and actual confinement, issuing the existing enrollment/runtime receipts.
   The browser decision requests these checks; target self-report and owner
   consent alone cannot manufacture them. The trusted service accepts receipts
   through the existing private seams, with no public `verified=true` setter.
8. **Ready only after all checks.** Trusted policy applies the exact signed
   receipt locally and finalizes the reserved node id. Credentials remain in the
   existing protected custody system; only public SSH keys reach the target.
   Readiness also requires fresh verification and independently configured
   execution admission. This flow never turns on scripts, discovery or scopes.
   Verification failures, missing infrastructure and later changes stay in
   browser Needs me; no machine prompt or local JSON edit follows.

For copied commands, use a fixed shell form that suppresses history before reading
the private payload; do not assume a leading space disables history. The launcher
must disable tracing, redact diagnostics and never pass the code through argv,
URLs, service definitions or telemetry. The browser marks it private, uses no-store
responses and keeps it out of analytics, clipboard history integrations and shared
evidence. A stolen unexpired code can win redemption; the key/fingerprint check
above prevents that contender becoming the intended verified target. Local root
and the owner's clipboard/session are initial trust boundaries, not a secrecy
guarantee against a hostile OS.

## 5. Trusted package and bubblewrap policy

**Recommend a signed, digest-pinned release package, not a pinned git checkout.**
The signed release manifest covers package SHA-256, version, architecture, OS
support, expiry, dependency closure and minimum compatible bubblewrap build.
Reject unsupported schema/architecture, unknown signer, digest mismatch and
rollback below the approved release floor. An immutable git commit is better than
a branch, but still requires checkout/build tooling and pinned dependencies and
does not independently authenticate its publisher. Do not clone or build as root.

Trust must precede downloaded code: a signature beside a package is not enough.
The launcher/verifier and publisher root must come through an independently
authenticated OS distribution channel, or an equivalently pretrusted signed
installer mechanism. A digest or verification key supplied solely by the same hub
cannot authenticate its own replacement. A bare `.deb` is not automatically
signature-verified. No such distribution bootstrap is shipped by this note;
fresh-machine support stays blocked until that trust path works without an extra
owner key-copy/install step. Never run an unverified fetched shell script as root.

Use a private, root-owned staging directory, no symlink traversal, bounded archive
extraction, no device/absolute/parent paths and no group/world-writable ancestors.
Root reopens and verifies staged files without a copy/verify race, places a complete
version under a root-owned release directory and atomically selects it. Every
created artifact and digest enters the existing inventory before activation.
Launch through an absolute trusted interpreter with `-I -B`; retain the existing
stdlib import check, read-only runtime inventory and config-last ordering. Keep
the preceding trusted release for rollback; never follow a moving git ref.

The current installer deliberately installs **no packages**. The new wrapper is
an explicit, bounded exception, not a changed promise of the offline core:

| Acceptable within the one declared sudo accept | Refuse or defer to browser Needs me |
|---|---|
| Read OS/package metadata; reuse suitable existing packages | Treat package presence/version alone as confinement proof |
| Refresh authenticated metadata from already configured distribution repositories | Add repositories, signing keys, third-party feeds or unsigned downloads |
| Install missing bubblewrap and the exact reviewed minimal dependency closure; reuse existing Python/system services | Full upgrade, automatic removals, unrelated packages or unreviewed transitive changes |
| Noninteractive install with signed metadata, pinned package hashes/versions and bounded lock/download time | Package scripts needing a second prompt, reboot, changed global policy or unrelated service restart |
| Provision the dedicated account, owned mounts, fixed SSH drop-in and delegated launch using existing safeguards | Global SSH weakening, firewall/network changes, privileged/setuid bubblewrap, kernel/sysctl/security-policy changes |

Recommend initially supporting qualified Linux distribution images with systemd,
cgroup v2, Python 3, suitable SSH and a compatible non-setuid bubblewrap available
from their existing authenticated repositories. Preview and bind exact resolved
dependency versions/hashes in the envelope; re-resolve and require equality before
mutation. Package-manager side effects and service hooks must be qualified per
supported image, not assumed harmless because installation is noninteractive.
If the known bubblewrap fix is unavailable through that policy, mark that image
unsupported; do not silently install an arbitrary newer binary or weaken sandbox
flags. Qualify behavior, including `--disable-userns`, in a disposable image.

Do not auto-remove shared distribution dependencies on failure/uninstall. Record
what was newly installed and show retained dependencies in browser status. Resume
the same private journal without repeating completed actions; preserve unrelated
services and the existing socket-activation behavior. Uninstall uses the existing
artifact inventory, stops owned processes first and preserves uncertain evidence.

## 6. Approval-keyset pinning and permissions

Coordinate the wire format with #431 before implementation. Recommended contract
`compute-approval-keyset-v1`, proposed rather than an existing accepted schema:

- Fields: `schema`, workspace id, monotonically increasing `epoch`,
  `previous_keyset_digest`, approval `rp_id`, allowed exact `origins`,
  validity bounds, and bounded `keys` entries (`credential_id`, `key_id`, COSE
  algorithm/public key, user-verification requirement, state). Reject duplicate
  ids, private key material, ambiguous JSON, unsupported algorithms and unknown
  security-critical fields; use deterministic canonical bytes and SHA-256.
- Store in a root-owned 0600 file under the existing protected configuration
  directory; persist its digest/epoch in the root-owned install journal. Bind the
  initial keyset digest to workspace/node, controller-key digest and signed intent.
  Public key material is not secret, but its integrity is execution authority.
- Initial keyset provenance must be authenticated independently of a mutable hub
  response, through the trusted approval application/provisioning trust established
  by #431. A self-signature from a key delivered in the same response proves
  possession, not ownership. This remains a release blocker: if #431 cannot supply
  that independent binding inside the accepted flow, setup stays incomplete.
- Verify WebAuthn assertions locally over an exact action digest: workspace/node,
  installation id, gateway epoch, package/runtime/controller/keyset digests,
  authority version, requested operation/scope, nonce and short expiry. Validate
  RP id hash, exact origin, challenge, signature and user verification. Store
  consumed nonces atomically with application; do not rely on authenticator
  signature counters alone for replay protection.
- Rotation/addition/removal requires a receipt signed by a currently pinned key,
  binding old/new digests and next epoch; write atomically and reject rollback.
  The hub cannot replace the keyset, approve enrollment or broaden permissions.
  Loss of every pinned key needs the separately designed trusted recovery flow;
  no hub-only reset and no extra machine prompt is an acceptable shortcut.

Only a current signed-in person owner may opt in, issue/revoke an intent or decide
Needs me, with same-origin write protection and live membership checks at commit.
Member, requester and bearer-token access remains the intersection of the caller's
own scope, workspace/board/task rights, gateway/node restrictions and current
execution grant. Enrollment adds no read or execute rights. Neither a target nor
the hub may approve itself; signed local decisions cannot override a narrower
current hub grant. A small fixed privileged policy service may update only owned
approval state after local verification; it is not a generic root command channel.

Default off means missing/invalid settings are off; all proposed enrollment and
target-self routes are uniformly unavailable, no Add target control, no capability,
MCP catalog change, discovery, event, count, metadata field or background installer
appears. Pin existing manual-management responses unchanged. The existing page
may remain available for manual metadata; this feature adds nothing while off.
An owner-only opt-in lives in existing settings and is never inferred from old
targets. Runner opt-in and trusted execution/discovery/script switches remain
separate checks; enabling any one does not enable the others.

## 7. Threats, failure and kill switch

| Threat/failure | Required control and truthful outcome |
|---|---|
| Stolen, guessed or replayed code | 256 bits, hashed storage, five-minute expiry, purpose binding, throttling, single transactional consume and fingerprint confirmation; no execution authority from redemption |
| Cross-workspace/node/gateway substitution | Signed immutable intent and exact binding checks at exchange, provisioning, receipt acceptance and every execution |
| Hub substitutes initial owner key or package | Independent publisher and initial approval-key provenance; absent trust blocks installation/readiness; same-source digests do not suffice |
| Hub forges later approval, rotation or runtime claim | Locally pinned passkey checks plus separate independent runtime/enrollment receipts; no plain-hub fallback |
| Mutable checkout, archive traversal or staging race | Verified immutable package, safe extraction, root-owned paths, reopen/reverify before use and isolated imports |
| Dependency install expands privilege or changes unrelated work | Reviewed pinned closure on qualified OS images; no new feeds, reboot, global policy change or arbitrary root hook |
| Code/credential leakage | Private stdin/files, no tracing/history/argv/URL payload, bounded redacted errors, no raw code in audit/export; setup key never grants execution |
| Lost response, double click or concurrent finalization | Request-id/digest idempotency, atomic consume, shared key-challenge recovery of original ciphertext, fenced reserved-node finalization; no second installation |
| Stale settings, gateway epoch, keyset or verifier receipt | Recheck current authority and expiry; invalidate pending approvals; return to Needs me without local edits |
| Partial install, power loss or failed confinement | Config-last activation, durable owned-artifact journal, same-id resume or uninstall; preserve evidence, never label ready |
| Revoke during execution or offline target | Deny new admission immediately, cancel owned work, bounded local watchdog; show uncertain termination until proven |

Reuse the shared ten-minute encrypted recovery window with fresh single-use
challenges and key proof. The consumed code never recovers a delivery or enrolls
twice. Expired recovery requires browser revoke/reissue; purge ciphertext after
acknowledgement/expiry/revoke. A local journal may resume only the same installation
and digest. Identical signed decisions return their recorded outcome; altered or
stale replay is denied and no side effect repeats. Uncertain remote execution is
never retried or deleted merely to free capacity.

Kill switches: workspace **Disable target setup**, per-target **Revoke**, and the
existing trusted execution switches. Disabling this setup flow invalidates its
codes, deliveries and pending approvals, revokes its setup identity and fences
new work from targets enrolled through it; manually provisioned targets retain
their explicit controls. Re-enabling restores no old authority. Revoke bumps
the installation epoch, removes its dispatch authority and requests termination
of owned process groups. Keep forensic claims and do not erase unrelated work.

Require a maximum five-second renewable local authorization lease/watchdog for
enrolled targets, bounded by signed grant expiry; hub loss or lease expiry stops
owned execution. Lease renewal can narrow authority, never replace a passkey
grant. Local pinned policy must validate execution approvals even when the hub
holds the controller SSH key. The fixed-policy helper's stop/lease implementation
is later-slice work, not an existing guarantee. Report **stopped** only with
termination evidence, otherwise **revoked, stop unconfirmed** in Needs me. A hostile
or unreachable OS cannot be synchronously killed by a browser; do not claim it.

## 8. What the owner does by hand

1. **Before first use, browser/app:** explicitly opt in; select the existing
   gateway and target label, review install/dependency scope and register a passkey
   if none exists. Click **Add target** once for the frozen intent.
2. **On the intended machine, once:** run the complete generated command (or its
   verified signed-package equivalent) and accept the single sudo authorization.
   A sudo authentication credential, if required by local policy, belongs only in
   the normal sudo prompt; never in the generated command, chat or evidence.
3. **After installation, browser/app:** compare the non-secret target fingerprint
   with the installer's completion display and approve the exact enrollment
   Needs me item using the passkey. Wait for independent verification status.
4. **Only if setup expires/fails:** decide browser revoke/reissue or the supported
   recovery action. No key/token copying, dependency install, checkout preparation,
   service/config edit or second terminal approval is part of supported setup.
5. **Later, browser/app only:** approve requested scope/key changes or recovery,
   or revoke/disable. Local emergency shutdown/uninstall remains an owner's option,
   not an additional required installation step.

The walkthrough counts one Add action and one machine accept separately from
browser passkey registration and the enrollment decision. Do not hide those
browser authorizations or call an unsupported image successfully installed.

## 9. Six implementation slices and proof

One PR per slice, dormant and independently mergeable. Each updates its matching
module fragment/system pointer. Pin base/head and run the same focused assertions
RED on base/GREEN on head for new behavior, plus relevant existing guards. Setup
errors, skips and timeouts are not RED evidence. Use synthetic workspace/node ids
and a clean-workspace fixture in every slice. Run only affected tests and smoke
checks; never start or wait for a full test suite.

**S1. Shared enrollment lifecycle and Compute intent.** Extract shared purpose-
bound hashed-code/recovery primitives without changing the runner wire contract;
add Compute settings, reservations, owner issuance and setup-only exchange.
- Acceptance: one code binds one workspace/node/manifest; no runner or execution
  credentials; one race winner; unchanged off-state and runner responses.
- Proof: focused shared/Compute enrollment RED/GREEN, expiry/purpose/owner/digest
  failures, recovery/crash races and clean off/on fixtures. Node harness: none.

**S2. Verified package and generated envelope.** Ship authenticated bootstrap,
signed digest-pinned release, bounded envelope and protected root-owned placement.
- Acceptance: no hand-copied ids/keys, no root import from user-writable source;
  unknown signer, tamper, rollback, wrong binding and malicious archive refuse.
- Proof: focused package/command RED/GREEN, secret/history/argv scans, stage-race
  injections and clean supported-image single-sudo proof. Node harness: none
  unless rendering code is included; then its focused wrapper is required.

**S3. Bounded dependency plan and offline installer bridge.** Install only the
  approved package closure, pin exact versions, preserve offline core and inventory.
- Acceptance: missing compatible bubblewrap is installed within the same accept;
  unsupported stock builds fail closed; rerun/uninstall preserve foreign work,
  inactive SSH services and uncertain claims; no reboot/global changes.
- Proof: focused bridge/installer RED/GREEN and disposable clean-image install,
  interrupted install, identical reinstall and rollback evidence; qualify actual
  bubblewrap behavior and package hooks. Node harness: none.

**S4. Approval-key pinning and local signed policy (#431 dependency).** Finalize
  the shared format/trust bootstrap, pin before activation and enforce local checks.
- Acceptance: hub-forged/wrong-origin/expired/replayed approvals and key rollback
  fail; valid passkey decisions apply without local prompts; no hub-only key reset.
- Proof: focused keyset/policy RED/GREEN, initial-key substitution and rotation
  races, clean no-key fixture, and independent WebAuthn browser proof. Targeted
  browser wrappers; shell/navigation wrappers too if their files change.

**S5. Add target and browser verification.** Owner-only UI, finalization through
  Needs me and existing independent enrollment/runtime receipt seams.
- Acceptance: one Add/one sudo walkthrough, exact reserved node, fingerprint
  confirmation, genuine confinement evidence; self-report/owner tap alone cannot
  verify a target. Off feature adds no UI, events, catalog or metadata.
- Proof: focused finalization RED/GREEN, clean-workspace browser flow, foreign
  caller/stale-epoch/revoke races and independently qualified disposable target.
  Run affected UI wrappers and shell/navigation wrappers when shell files change.

**S6. Revocation, offline stop and end-to-end release proof.** Fence enrollment
  and work on disable/revoke, enforce signed policy plus local lease, finish rollback.
- Acceptance: valid work has no machine prompt; connected stop is acknowledged,
  disconnected owned work stops within five seconds, unrelated work survives,
  re-enable restores no authority and uncertain results remain uncertain.
- Proof: focused lifecycle RED/GREEN, clean-image complete walkthrough, actual
  process stop/network-loss evidence and failure-boundary restart/uninstall tests;
  pinned off-state/runner guards and affected browser wrappers. No enablement until
  package trust, #431 and independent runtime/stop proofs all pass.

This note's evidence is documentation obligation/fragment validation only. It
does not supply implementation RED/GREEN, bootstrap, passkey or fresh-image proof.

## 10. Open questions, each with a recommendation

1. **Publisher and initial owner-key trust on a fresh image?** Recommend a signed
   release package authenticated through an independent OS trust channel, plus
   #431's independent owner-key binding. Both are release blockers; neither a
   downloaded key nor a self-signed hub response counts. No extra manual pinning.
2. **Which OS images and bubblewrap builds?** Recommend a small qualified Linux
   matrix with existing systemd/SSH and a compatible build in its configured
   authenticated repositories. Stock 0.9 is not accepted on presence alone; if the
   fix is unavailable, keep that image unsupported rather than add a new feed.
3. **Who finalizes independent verification?** Recommend the existing trusted
   verifier service, triggered by the signed Needs me decision, accepting the
   existing enrollment/runtime receipt types. Never let the hub, target report
   or browser decision sign its own confinement evidence.
4. **Shared enrollment extraction while runner slices ship?** Recommend one shared
   lifecycle/crypto module with purpose-specific transactional adapters; preserve
   current runner endpoints, recovery format and opt-in semantics. Coordinate
   S1 with those slices and require their focused compatibility tests.
5. **Disable/recovery behavior for an unreachable target?** Recommend immediate
   admission revocation and a proved five-second local stop bound; otherwise show
   stop unconfirmed. Lost-all-key recovery stays blocked pending trusted design,
   never falls back to hub approval or an unexpected machine-side prompt.
