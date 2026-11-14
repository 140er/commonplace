# Runner box installer: design note

Status: proposed. Cards #449 and #450, children of #430 (agentic first).
Related: #447, #448 and #452. Docs only: this note installs nothing, changes no
machine setting and authorizes no login. It applies to every workspace.

## 1. Problem and goal

- A fresh runner machine needs credentials, configuration, acknowledgements,
  a service, an agent host and provider logins before it can do agent work.
- Today the owner carries tokens, edits JSON and performs separate setup steps.
- Goal: **Add runner** gives one command containing a short-lived enrollment code;
  running it requires **ONE accept**, covering installation and service setup.
- After that accept, all login, grant and risk approvals happen in the browser or
  app as **Needs me** items; no machine-side confirmation or secret entry follows.
- Automatic setup is off by default; an unapproved or incomplete machine runs no
  agents, and existing tokens gain neither visibility nor authority.

## 2. Existing structures and scope

Build on [the runner contract](../runner.md), `commonplace/runner/registry.py`,
`owner_api.py`, `runner_api.py`, `agent.py`, `chat_agent.py` and the runner command
group in `commonplace/cli.py`. Reuse the project settings and orchestrator seat
rules in `commonplace/projects/`, and the machine adapter in `commonplace/bots/`.
Bots continue to call the runner's checks; installation creates no new principal
that can bypass them.

The registry already separates machine and worker credentials, records
`grant_version` and `binding_epoch`, and requires a person owner for attestation.
Topic/wing changes reset a binding; old jobs cannot cross its epoch. Attestation
remains **owner attested, not independently verified** (`verified:false`). The
worker's wing policy does not constrain the separate local agent-host grant;
the installer must provision and check both authorities explicitly.

| Today | Proposed replacement |
|---|---|
| Browser shows machine and worker tokens to copy | Browser shows only an expiring, single-use enrollment command |
| Owner writes a local configuration and token environment | Installer writes private files from an approved, versioned manifest |
| Owner types binding, checkout and runtime acknowledgements | Fetch an owner-approved binding/settings receipt and derive the local compatibility fields |
| Owner separately installs a service and enables linger | The one installation accept covers the user service and required linger setup |
| Owner installs the agent host and each provider CLI, then wires topics | Locate compatible components or install verified releases, then apply the approved provider/topic mapping |
| Owner places the local agent-host grant secret | Owner approves a scoped grant in the browser; installer receives it privately |
| Provider login and runtime questions appear on the machine | Browser/app authorization and Needs me, with no terminal interaction |

Non-goals: a generic remote shell, automatic permission expansion, copying a
person's existing credential directory, paid subscriptions, changes to unrelated
services, and provisioning the separate command-execution target. #447/#448 own
that target's package, confinement and socket-activation work. This installer must
not claim those capabilities merely because both roles use the same machine.

## 3. Read-only survey: the second runner box

Observed 2026-11-13 through a noninteractive remote shell as the intended runner
account. Only OS metadata, command lookup, processor count, service-manager state
and disk availability were read. No installation, configuration or login occurred.
Component labels below describe roles rather than internal executable names.

| Check | Observation |
|---|---|
| Operating system | Ubuntu 24.04.5 LTS |
| Logical CPUs | 12 |
| Version-control client | Present on the remote command path |
| Python environment/package manager | Present on the remote command path |
| Unversioned Python interpreter command | Absent from that path |
| Python 3 interpreter command | Present on that path |
| Primary JavaScript runtime command | Absent from that path |
| Alternative JavaScript runtime/package manager | Present on that path |
| Headless agent-host command | Absent from that path |
| Subscription provider CLI A | Absent from that path |
| Subscription provider CLI B | Absent from that path |
| User service manager | Running |
| User linger | Enabled |
| Root filesystem | 436.0 GiB total, 360.9 GiB available |
| Runner home filesystem | Same capacity and availability; do not add these figures |

Command lookup proves availability on that shell's path, not compatibility or
absence everywhere on disk. No credentials or login state were inspected. A later
installer proof must check versions and headless capabilities. The second runner
box is intended for both checks and agent work: preserve existing workloads,
apply capacity limits and give each agent project its own approved checkout.

## 4. Data model and authority

Proposed control-side tables, following the existing workspace-keyed registry and
audit conventions. Opaque ids use the existing id generator; times are UTC.
No conversation text, command output or raw credentials belong in these rows.

| Record | Fields and invariants | Who reads/writes |
|---|---|---|
| `runner_install_settings` | `workspace_id` PK, `enabled` default 0, `version`, `updated_by`, `updated_at`; missing row means off | Person owner reads/writes; admission reads internally |
| `runner_enrollments` | `id`, `workspace_id`, `machine_label`, `code_hash`, `expires_at`, `consumed_at`, `revoked_at`, `owner_id`, `request_id`, `manifest_digest`, `settings_version`, `machine_id`, `machine_key_digest`; one outstanding code per workspace/label | Owner creates/revokes and reads status; exchange service consumes atomically; no token reads the code/hash |
| `runner_install_manifests` | `id`, `enrollment_id`, `version`, `digest`, approved projects/topics/wings/requesters, runtime ceiling, checkout choices, component release digests, approval-key id, owner approval receipt | Owner approves; only the enrolled machine reads its manifest; installer cannot edit it |
| `runner_installations` | `id`, `workspace_id`, `machine_id`, `machine_key`, `manifest_digest`, installed component versions, provider instance id, project ids, phase, receipt digest, last contact, revocation epoch | Installer reports bounded facts; owner reads; reports grant no authority |
| `runner_setup_requests` | `id`, workspace/machine/install ids, kind (`login`, `grant`, `binding`, `scope`, `runtime`, `recovery`), frozen argument digest, grant/binding/settings versions, owner id, expiry, state, decision receipt | Owner decides through Needs me; machine reads only its requests and signed outcomes |
| `runner_secret_deliveries` | `id`, workspace/machine/install ids, recipient-key digest, kind, ciphertext, expiry, delivery receipt, acknowledged_at | Credential issuer writes; only the key-bound machine fetches; owner sees status, never the payload |

Retain `runner_machines` as the source of machine/worker connection ids, grants,
enabled/revoked state and binding epochs; do not create another worker or permission
registry. Token verifiers stay in the existing credential store. Enrollment,
connection creation, code consumption and encrypted delivery commit together.
The setup-request row links to the existing Needs me decision path instead of
creating another inbox. Decisions require a current person-owner session.

Local state: a private installation directory (0700), separate machine token,
worker token, agent-host grant and provider credential files (0600), machine
private key (0600), generated config (0600), and a phase journal containing ids,
digests and outcomes only. Reject symlinks, foreign ownership and permissive files;
write with a restrictive umask, exclusive creation and atomic replacement. Secrets
never enter command arguments, service definitions, stdout/stderr or journals.
The user unit reads private credentials; children receive only their own worker,
provider or agent-host credential, never the machine management credential.

### Surface names

These routes are proposals, not claims that they exist. Keep the current owner
registry routes; add narrow enrollment and machine-self routes rather than making
the owner routes accept machine tokens.

- Owner browser/app: `GET/PUT /api/runners/install-settings`,
  `POST /api/runners/enrollments`,
  `GET/DELETE /api/runners/enrollments/<id>`,
  `POST /api/runners/<id>/setup-requests/<request>/decide`.
- Code-only bootstrap: `POST /api/runner-enrollment/exchange`.
- Key-bound delivery recovery: `POST /api/runner-enrollment/recover`.
- Machine-self: `GET /api/runner/install-manifest`,
  `POST /api/runner/install-status`, `POST /api/runner/setup-requests`,
  `GET /api/runner/setup-requests`, `POST /api/runner/secret-delivery`,
  `POST /api/runner/install-ready`, `POST /api/runner/authorization-renew`.
- CLI: `runner install`, `runner check`, `runner status`, `runner uninstall` in
  the existing runner command group; a signed bootstrap makes install available
  even when the runner package is absent.
- MCP: no enrollment, attestation, login or grant-decision surface. Existing
  start/status/cancel surfaces retain their current caller and worker checks.

## 5. Enrollment and installation flow

1. **Prepare in the browser.** A person owner explicitly enables runner setup,
   chooses a unique machine label and reviews projects, topics, wings, requesters,
   checkout sharing, component choices and runtime ceiling. The existing
   orchestration switch remains a separate required admission check. A #452
   “same settings as” choice copies proposed settings, never tokens, provider
   identity or an old attestation; resolve and revalidate all copied scopes.
2. **One Add runner action.** Freeze the approved intent and issue one command
   carrying a cryptographically random code (at least 256 bits, five-minute
   lifetime), bound to exactly this workspace, label and manifest digest. Store
   only its hash. No machine/worker token exists in the browser response. The
   command names the signed release channel and the hub's authenticated origin;
   no live address or real code belongs in documentation or evidence.
3. **One installation accept.** The signed launcher verifies its release, checks
   OS, resources, existing services and privilege capabilities, and shows the
   installation scope once. This accept covers dependencies, the runner, its
   systemd user unit and linger. Use a narrow privileged helper only where needed;
   the runner and credential files belong to the intended unprivileged account.
   Do not ask for a password or another machine prompt. If the platform cannot
   complete this through the approved single-accept path, stop before consuming
   the code and show an unsupported-install Needs me item; do not claim success.
4. **Redeem once.** Before exchange, persist a machine key and installation id
   privately. Send the code in the protected request body, with the public key,
   installation id and manifest digest. Validate expiry, workspace, label,
   current owner/settings and unconsumed state under one transaction. Atomically
   consume the code, enroll an initially unbound machine and create separate
   machine/worker tokens. Encrypt delivery to that machine key; the installer
   writes the tokens 0600 and acknowledges durable receipt, without printing them.
5. **Build local runtime.** Locate compatible headless agent-host and selected
   provider CLIs through adapters; use verified, pinned releases when absent.
   Do not replace unrelated installations. Wire a dedicated provider instance,
   selected topic/model mapping, project ids and checkouts from approved intent.
   Report discovered ids and versions, not credentials. Unknown binaries or a
   missing supported headless/device authorization interface block readiness.
6. **Finish browser approvals.** An existing valid provider login can be reused
   only for the intended account and scope. Otherwise create a browser/app
   device-authorization request for each selected provider. The owner signs in
   there, never in the remote shell. The adapter completes the provider's
   supported exchange and stores the result directly on the machine. The owner
   approves the separate agent-host grant for the exact machine, provider
   instance, projects, topics and ceiling. An authenticated issuer delivers that
   secret through the hub's machine-key-bound encrypted channel; it is never a
   field to copy or type. The hub is a relay, not an issuer of provider authority.
7. **Fetch acknowledgements.** The owner approves the final discovered binding
   in that browser grant step, preserving owner-only attestation. Fetch the
   current binding and browser-approved project receipt. Verify workspace,
   machine key, provider/worker ids, projects, topics, wings, checkout choices,
   runtime ceiling, `grant_version`, `binding_epoch`, expiry and signature. Derive
   `binding_acks`, `shared_checkout_ack` and `runtime_ack` for the existing local
   validator; the installer never invents or self-signs consent. The manifest's
   acceptance and this final binding must match exactly or return to Needs me.
8. **Ready, then serve.** Run local checks without printing secrets; activate
   the user unit with linger and obtain a fresh authorization lease. Partial
   setup may run a setup-only service but cannot lease agent work. The hub marks
   ready only after logins, scoped grant, binding, receipts and service health
   are proved. A clean agent start must finish without any local prompt.

The ONE accept counts installation authorization on the machine. Provider login
and the agent-host grant are separate browser/app authorizations, required only
when missing or changed; they are never disguised as another terminal prompt.
The product walkthrough must report both counts, including the fresh-account case.

## 6. Permission and runtime rules

- Only a current signed-in person owner may enable setup, issue/revoke codes,
  approve settings, attest a binding or authorize grants. App authorization must
  preserve that person-owner check; a bearer worker/machine/coordinator token
  cannot impersonate it. Existing attestation routes remain owner-only.
- A machine token may report this machine and fetch its approved receipts and
  deliveries. It **cannot enroll another runner**, issue codes, choose scopes,
  approve itself, read another machine's secrets or change requesters.
  The bootstrap exchange rejects token-authenticated requests; it neither treats
  a machine token as an enrollment code nor combines their authorities.
- Effective reach is requester rights ∩ machine grant ∩ worker policy ∩ approved
  project/topic mapping ∩ local agent-host grant. Apply the lowest accepted
  runtime ceiling across hub, project and local host at every start/action.
  A bot adds its existing asker/access restrictions; installation changes none.
- Retain `approval-required` as the conservative default. Necessary approvals
  route to Needs me. A headless adapter must suspend before the action until the
  exact browser decision arrives; it must not silently raise the ceiling or
  auto-answer a terminal prompt. An accepted higher ceiling authorizes only
  actions within it; riskier actions create a scoped, expiring Needs me request.
- Wing/topic/provider/worker changes invalidate the binding and cached receipt;
  project/checkout/runtime changes invalidate the matching settings receipt.
  Fence pending work with versions and epochs. Browser reapproval regenerates
  acknowledgements and any narrower grant; no JSON edit on the machine.
- Requester additions/removals use the existing grant-version compare-and-set
  checks. Removing rights is effective immediately; broadening any grant waits
  for the owner. Never broaden the worker policy to make a failed test pass.
- Automatic setup is off in every workspace until explicit owner opt-in; a
  migration does not infer consent from legacy machine rows. Missing/corrupt
  settings fail closed. While off, new routes are uniformly unavailable to
  existing tokens, with no catalog, capability, discovery, event, count or
  metadata additions. Preserve pinned pre-feature responses on existing paths;
  the existing manual runner contract is not silently expanded or enabled.

## 7. Failure, replay and kill switch

Browser issuance deduplicates `(workspace_id, owner_id, request_id)` with the
frozen digest: same request returns status, never reprints a code; changed intent
conflicts. Losing the command means revoking/reissuing, not recovering plaintext.
Exchange uses a conditional consume: concurrent redemption has one winner and
every later use of that code is refused, including by the same machine.

A dropped exchange response must not mint a second machine. Recover only the
original encrypted delivery using a signed server challenge, the persisted
machine key and installation id, within its short delivery lifetime. The code
cannot authorize recovery. Purge delivery ciphertext after durable acknowledgement
or expiry; after expiry the owner revokes stranded credentials and issues a new
enrollment. Phase-journal replay converges on the same unit, provider instance and
project ids; an ambiguous agent launch remains uncertain, never retried as a new
run. Unsupported adapters leave setup incomplete, without a manual login fallback.

Kill switches are workspace **Disable runner setup** and per-machine **Revoke**.
Disabling stops exchanges and outstanding approvals, invalidates codes and
authorization leases for installations in this flow, and stops their agent work;
re-enabling never revives old codes or approvals. Existing manually managed
machines retain their explicit controls, including the orchestration switch.
Revoke atomically disables the machine, revokes both tokens, bumps the epoch and
invalidates its local-host grant and pending deliveries/decisions. No new lease,
renewal, action or result authorization may pass after that commit.

“Revocation kills the machine immediately” means immediate loss of hub authority
and an immediate stop signal to its supervised runner and agent process groups.
Show **stopped** only after the supervisor acknowledges termination; otherwise
show **revoked, stop unconfirmed** in Needs me. A fresh short authorization lease
(maximum five seconds, monotonic deadline) and a local watchdog stop owned process
groups on hub loss or lease expiry. A synchronous physical kill of an unreachable
or hostile OS is impossible; do not claim it. S6 must prove the connected stop and
offline deadline before enabling agent-ready mode. Do not terminate unrelated
check workloads. Local uninstall stops/removes owned units and private artifacts,
revokes the installation when online, and undoes linger only if this installer
enabled it and no other user service depends on it.

All authority transitions, exchanges, delivery acknowledgements, denials,
readiness, revocation and decision outcomes are audited by workspace/machine/
installation ids, versions, actor and fixed outcome codes. No enrollment codes,
tokens, login responses, private paths or raw provider errors enter the audit.
Fail closed on missing authority/audit persistence; reconcile interrupted cross-
store steps without activating work.

## 8. Threat table

| Threat | Control and required witness |
|---|---|
| Code theft, guessing, replay or racing exchanges | Five-minute 256-bit code, hashed at rest, rate/body limits, single atomic consume, frozen workspace/label/digest; concurrent and expired-code tests |
| Stolen code enrolls the attacker's box first | Bind the first exchange to a machine key; browser grant names that fingerprint and discovered binding; no execution before owner approval; attestation is not hardware identity proof |
| Machine/worker token grows into enrollment authority | Code-only exchange and owner-only issuance/attestation; negative tests with every token class, including another workspace |
| Hub or transport swaps a binding or a release | Authenticated transport, pinned release verification, owner approval key pinned at install, signed versioned receipts; tamper, rollback and wrong-key tests; no plain-hub fallback |
| Overbroad agent-host grant bypasses worker rights | Dedicated scoped grant, explicit issuer enforcement and lowest ceiling; test direct host calls outside project/topic/ceiling, not just honest runner checks |
| Secrets leak through process arguments, files or logs | Encrypted key-bound deliveries, private ownership/modes, no argv or unit secrets, child credential separation, synthetic-secret scans including crashes and service logs |
| Headless provider flow opens a terminal or enables a paid API fallback | Capability check before readiness, browser-only login, subscription authorization adapter; unsupported flow is Needs me, never an alternate billing path |
| Stale manifest, settings or browser approval regains rights | Compare-and-set versions plus binding/revocation epochs; freeze exact arguments and expiry; recheck at start and action; revoke-during-delivery tests |
| Installer damages existing check services or executes untrusted binaries | Verified release digests, constrained helper, owned-file manifest, dedicated project checkouts, preflight collision refusal and idempotent uninstall; test an occupied machine |
| Off workspace leaks runner existence to old tokens | Literal pinned baseline envelopes/catalog/events, hidden-present versus absent controls, no probes/discovery; repeat for every caller class |
| Revoke or network loss leaves supervised agents running | Immediate authority denial, stop acknowledgement, short lease/watchdog and owned process groups; connected and disconnected live-process proofs |
| Duplicate approval or crash runs an action twice | Frozen request digest, single decision transition, durable phase journal and existing uncertain-launch handling; lost-response/crash injection |

## 9. What the owner does by hand, and when

This docs-only phase requires **no machine action, login or secret from the owner**.
For the implemented flow, the complete manual checklist is:

1. **Before installation, browser/app:** enable runner setup and choose/review
   the machine label, projects, topics, wings, requesters, checkout use, selected
   providers and maximum runtime. Click **Add runner** once.
2. **On the intended machine, once:** run the generated enrollment command and
   accept its installation scope **once**. No token, password, grant secret or
   acknowledgement text is typed; no service/configuration edit follows.
3. **When a selected provider lacks a usable login, browser/app:** follow its
   device-authorization link, sign in and approve that machine's CLI login.
   Repeat only for selected providers requiring authorization. Credentials stay
   out of the command, chat and evidence.
4. **After runtime discovery, browser/app:** approve the exact local agent-host
   grant and final owner attestation shown with machine fingerprint, provider
   instance, worker, projects/topics and runtime ceiling. The installer receives
   and stores the grant secret; the owner never carries it to the machine.
5. **Later, only when requested, browser/app:** decide Needs me items for changes
   to scope, checkout use, requesters, bindings, runtime or recovery, and for
   actions outside the accepted authority. No further machine-side prompt.

Do not promise the fresh-account flow has zero browser authorizations. Those are
the explicit CLI-login and grant-approval steps required by #450, within #430's
browser-only approval rule.

## 10. Slice plan and evidence

Six small, ordered PRs; each independently mergeable, dormant until owner opt-in.
No slice may set the switch on, auto-approve a request or mark an incomplete
installation agent-ready. Fixtures can exercise later contracts without exposing
them to users. Each PR updates its module field fragment and system pointer.

For each slice, pin base and exact head, run the same new assertion selection on
both (RED missing behavior, GREEN correct behavior), and record both SHAs and
counts privately. Import/setup errors, skips and timeouts are not RED proof.
Every slice also runs its relevant pinned baseline selection GREEN on both and a
fresh-workspace fixture with no existing machines, credentials or real names.

**S1. Enrollment code store and routes.** Store settings, intent, hashes,
expiry/consume state, encrypted-delivery recovery and audit; owner issuance and
code-only exchange. No service or execution activation.
- Acceptance: one code enrolls exactly one workspace/label/key; expiry, replay,
  wrong digest, wrong workspace and non-owner/token issuance fail closed. A lost
  response recovers the same ciphertext, never a second machine or code.
- Proof: proposed `tests/test_runner_enrollment.py` RED/GREEN for races and crash
  boundaries; clean-workspace off/on cases; existing identity/owner tests and
  baseline response/catalog/event fixtures GREEN unchanged. Node harness: none.

**S2. Installer command and service.** Add install/status/uninstall in the
existing runner command group, verified bootstrap, private files, phase journal,
user unit and linger under one accept; setup-only until later prerequisites pass.
- Acceptance: absent runner bootstraps; code exchanges without token output;
  files are 0600, directories 0700; restart/reinstall converges and uninstall
  removes only owned artifacts. Insufficient privilege stops before redemption.
- Proof: proposed `tests/test_runner_install.py` RED/GREEN with filesystem and
  service adapters, injected interrupted writes and secret scans; clean Linux
  user-manager fixture plus disposable live-host logout/restart proof; preserve
  existing units and both linger states. Node harness: none.

**S3. Approved acknowledgement fetch.** Machine-self manifest/receipt fetch and
compatibility generation for `binding_acks`, `shared_checkout_ack`, `runtime_ack`.
- Acceptance: only current owner-attested binding and project settings produce
  local acknowledgements; stale/tampered/wrong-machine receipts block work;
  changed wings/topics/ceiling create Needs me without a local file edit.
- Proof: proposed `tests/test_runner_install_receipts.py` RED/GREEN, clean-workspace
  manifest with no approvals refused, version/reset races and wrong signatures;
  existing agent/binding-fence tests GREEN. Node harness: none.

**S4. Headless host and provider CLI locate/install.** Adapter interface for
version/capability discovery, pinned release install, provider instance/topic
wiring and dedicated project checkout mapping. No automatic login or grants.
- Acceptance: compatible components are reused; missing selected components
  install; unselected/occupied components stay untouched; mapping is deterministic
  across reruns; unsupported headless/auth capability blocks agent readiness.
- Proof: proposed `tests/test_runner_runtime_install.py` RED/GREEN using signed
  fake releases; clean workspace with no runtime, a partial install and an
  occupied machine; disposable Linux host version/wiring proof. No real provider
  login needed in tests. Node harness: none.

**S5. Browser login and grant approval.** Device-authorization adapters,
Needs me approval binding, encrypted grant delivery, owner-only final attestation
and readiness prerequisites. Until S6, execution remains disabled.
- Acceptance: selected CLI login results and the scoped agent-host grant reach
  only the bound machine; no secret is typed/shown; a foreign person session,
  workspace or token cannot decide; stale/replayed approvals do nothing.
- Proof: proposed `tests/test_runner_setup_approvals.py` RED/GREEN with fake
  issuers, expiry/revoke/delivery races and clean fresh-account fixtures; browser
  walkthrough counts one Add action/one machine accept and each browser login/
  grant decision separately. Targeted browser wrappers; all shell/navigation
  wrappers if shell files change. Real issuer compatibility is a release blocker.

**S6. Needs me runtime enforcement and stop proof.** Route local action approvals
to the existing decision surface, enforce the accepted ceiling in the host issuer
and supervisor, and gate ready on revocation/watchdog support.
- Acceptance: an authorized agent start completes without local prompts; a
  riskier action waits for its exact browser decision. Revocation denies all
  authority immediately, stops connected owned agents and proves the offline
  deadline; unrelated check work survives. Turning off prevents all new setup.
- Proof: proposed `tests/test_runner_setup_lifecycle.py` RED/GREEN plus a clean-
  workspace walkthrough; disposable live-process stop/network-loss evidence,
  direct out-of-grant host calls, stale decisions and secret scans. Rerun pinned
  off-state baselines and existing runner start/revoke/binding tests. Browser
  wrapper scope as in S5. Only after these proofs may an owner enable agent-ready
  mode on the second runner box through the supported install flow.

This note's own checks are documentation obligations and fragment validation;
it supplies no implementation RED/GREEN, installed-runtime or login proof.

## 11. Open questions, each with a recommendation

1. **Independent approval signatures (#431).** Recommendation: pin an owner
   approval key in the trusted installer and require signed manifests/decisions;
   align with that card's format. Ship dormant slices first; no plain-hub fallback
   and no ready mode before signature verification is available.
2. **Single-accept privilege path on fresh Linux accounts.** Recommendation:
   support user units plus linger through the installer's constrained privilege
   channel. Prove it on stock supported distributions, including no existing
   linger. An extra password or privilege prompt fails the release criterion.
3. **Provider device authorization and scoped host-grant support.** Recommendation:
   require adapter capability proofs for the selected subscription CLIs and local
   host issuer. Unsupported combinations remain setup-only in Needs me; never
   copy credential directories, ask for a terminal login or substitute a paid API.
4. **Enrollment and interrupted-delivery lifetime.** Recommendation: five minutes
   for the code and ten minutes for key-bound encrypted-delivery recovery; one
   outstanding enrollment per workspace/label. Expiry requires browser reissue.
5. **Stop semantics on an unreachable machine.** Recommendation: immediate hub
   revocation, connected termination acknowledgement and a maximum five-second
   local authorization lease; show unknown execution truthfully. Require the
   live-process proof before calling agentic-first setup available.
