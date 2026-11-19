### Compute: add a machine without SSH (guided pairing, slice 1)

A person adds a machine in the Compute section by choosing what kind of machine it is, copying one command, running it on the machine and accepting once in the app. The app shows the machine as it connects, shows every step's state, and says what to do next when a step fails. This note is the design; it was written before the code. Slice 1 is delivered as three stacked changes: **A** the server (pairing records, the approval state machine, the API, the code lifecycle), **B** the installer that the command runs, **C** the screens (Classic and Deck, phone included). Where this note says "not in this slice" the app says the same in plain words.

#### What exists today (gap analysis, develop at the start of this slice)

Runner machines (a runner runs Coworkers):

- `commonplace/runner/enrollment.py`, `enrollment_api.py`, `enrollment_crypto.py`: owner opt-in (`/api/runners/install-settings`), one-time enrollment code (256 random bits, five minutes, SHA-256 hash only, one consumer), uniform refusals, durable ten-per-minute counters, `exchange` that creates one disabled, unbound machine with separate machine and worker credentials sealed to the machine's RSA key, key-bound recovery of a lost response. Pinned by `tests/test_runner_enrollment.py`.
- `commonplace/runner/installer.py`, `install_cli.py`, `install_host.py`, `install_files.py`, `install_receipts.py`, `bootstrap.py`: the machine-side installer (`commonplace runner install`), setup-only user service, private 0700/0600 files, manifest, uninstall, signed bootstrap launcher. Pinned by `tests/test_runner_install*.py`.
- `commonplace/runner/setup_api.py`, `setup_approvals.py`, `device_login.py`, `runtime_status.py`, `lifecycle.py`: provider sign-in cards, fingerprint comparison, sealed host grants and machine-reported component facts (`agent_ready` is always false there: reports are facts, never authority). Pinned by `tests/test_runner_setup_*.py`.
- `commonplace/runner/registry.py`, `owner_api.py`, `requester_api.py`, `web/static/adminrunners.js`: the owner screen and API for machine grants (projects, topics, wings, requesters, runtime ceiling), attestation and revoke.
- Gate on running work: a machine runs a card start only when it is enabled **and** its binding is owner-attested (`runner_api.py`, `capacity.py`); an enrolled machine is inert until both.

Compute targets (an SSH-reachable machine that runs approved scripts): `commonplace/compute/target_installer.py` (root install, manifest, socket-activated SSH handled since the follow-up recorded in `docs/system/compute-installer-followup.md` and `docs/modules/compute.d/socket-activated-ssh.md`), `target_helper.py`, `script_sandbox.py`, `docs/compute/RUNNING.md`. Everything about enrolling a target is a proposal only (`docs/design/compute-target-one-click.md`): no code issuance, no signed package, no approval-key pin (`pin_approval_keys` still raises), no API.

What a person must do by hand today to add a runner:

1. Turn on setup with a raw `PUT /api/runners/install-settings` (there is no screen).
2. Work out a 64-hex "manifest digest" and hand-write the grant JSON (label, projects, topics, wings, mode ceiling, requesters), then `POST /api/runners/enrollments`.
3. Copy five values (hub address, workspace id, label, enrollment id, digest) into `commonplace runner install`, and pipe the code in on stdin.
4. Prepare the machine: Python, `cryptography`, a systemd user manager, linger, bubblewrap where scripts are used; on a stock Ubuntu 24.04 box nothing checks these before the code is spent.
5. Find the machine in Needs me, compare fingerprints, attest topics and provider, add every requester again (#452) and re-pick wings and topics (#452).

What is missing for "Add machine, pick kind, one command, appears as it connects, one accept, per-step states":

| Missing | Where it lands |
|---|---|
| A screen or API that creates an enrollment without hand-written JSON or a hand-computed digest | A (`pairing.create` derives the digest and the grant) |
| One string that carries every public value, so nothing is copied by hand | A (`setup` envelope) and B |
| "Same as machine X" copying projects, topics, wings, mode ceiling and requesters (#452) | A (`copy_from`), C (the choice) |
| Seeing the machine appear when it connects, and seeing a failed step | A (`state`, reported `steps`), C |
| A clear refusal for a stale, used or foreign code (the exchange refuses everything alike on purpose) | A (`/api/runner-pairing/check`, code and pairing id as authority) |
| One accept that shows machine identity and grants in words | A (`accept` with an identity digest), C (the sheet) |
| An installer that checks the machine before it spends the code, handles stock Ubuntu 24.04 and reports step results | B |
| Offline handling for an approved machine, revoke and re-enroll with the same values | A (`offline`, `close`, `retry`), C |
| A target (SSH script) enrollment | Not in slice 1 (see below) |

The loose cards: #490 (enroll from the app without a bot) is the whole of this slice for runners; #447 (stock Ubuntu socket-activated SSH) is already fixed in `target_installer.py` and B adds a fixture test for the runner installer's own prerequisites; #448/#449/#450 are the designs this slice builds the runner half of; #451 (no one-click path, runner box needs hand installs) is A+B+C; #452 (requesters) is `copy_from` and the carried `requesters` list; #430 (agentic first) is the rule: one command and one accept, no later prompt on the machine.

#### Decisions

**Machine kinds.** `runner` is available. `target` (script target over SSH) is listed and refused with the reason `kind_unavailable`: its design needs the owner's approval keys (#431/#509) and a signed package that do not exist, and a flow that cannot verify what it claims would be worse than none. The app shows the kind as "Not available yet" with that sentence.

**One pairing record.** `machine_pairings` (control database, next to `runner_enrollments`) links one owner request to one runner enrollment: id, workspace, kind, label, owner, `request_id` and a hash of the request, the enrollment id, `version`, the accept decision, closed reason, last report time. `machine_pairing_steps` holds the machine's latest report per step. The pairing grants nothing by itself: authority stays in `runner_enrollments`, `runner_machines` and the ordinary approvals. Creating a pairing issues the enrollment in the same transaction and audits both.

**Pairing code lifecycle.**
- Created by the signed-in owner of the workspace (a person session; agent tokens and members are refused with a reason). 256 random bits, valid 15 minutes (the legacy enrollment keeps five), stored only as a SHA-256 hash, returned once in the creation response, never again (not by `GET`, not by an idempotent replay, not in the audit row, not in logs). A lost command means "Set up again": a new code, the old one revoked.
- Single use: the exchange consumes it in one conditional update; a second try is refused, and the first machine's credentials are unaffected.
- Scoped: to the workspace, the kind (`runner`), the exact label, and a digest of the frozen intent (grant plus plan version) that the installer must echo back; changing any of them after issuing needs a new pairing.
- Rate limited: creating is capped per workspace and minute, and at ten open pairings; the check and exchange routes share the existing durable per-workspace counters; reports are capped separately.
- Refused clearly when it cannot work, to the holder of the code only: `POST /api/runner-pairing/check` takes the workspace, the pairing id and the code (no cookie, no bearer token) and answers `usable`, or one of `expired`, `used`, `cancelled`, `revoked`, `setup_off`, `not_for_this_workspace`, `code_mismatch`, each with a plain sentence and a next step. The exchange itself keeps its uniform refusal, so a guess learns nothing; the pairing id is not a secret but is useless without the code, and it answers the same `code_mismatch` for a wrong code and an unknown id.
- Expiry is checked at every use; the existing sweeper persists it. An expired pairing is shown as "Code expired" with "Set up again".

**What the installer does and verifies (change B, delivered).** The command is `printf '%s' '<code>' | sh -c "$(curl -fsSL '<hub>/machine-setup.sh')" machine-setup '<envelope>'`. `GET /machine-setup.sh` is public and the same text for every caller (a small shell launcher with `commonplace/runner/machine_setup.py` embedded; it holds no secret and no tenant data). The code arrives on standard input, never as an argument; the envelope has public values only. The launcher finds Python 3.10 or later (if there is none it reports `missing_python` itself) and runs the embedded, standard-library-only program under `python3 -I`. The program, in this order:

1. Reads the envelope and the code and refuses a broken command (exit 2, nothing asked of anyone).
2. Checks the machine, read-only, in a fixed order, each failure with a cause the app already has: not root (`run_as_root`), Ubuntu 24.04 or later or Debian 12 or later (`unsupported_os`), Python (`missing_python`), the `cryptography` package (`missing_prerequisite`), the runner program (`missing_runner_program`), a running user service manager (`no_service_manager`), 256 MB free (`disk_full`). It never touches SSH or the system's service configuration, so socket-activated SSH on stock Ubuntu 24.04 is simply not involved.
3. Reports the result to `POST /api/runner-pairing/check` (code and pairing id as the credential), which also answers whether the code can still work. A failed check is shown in the app with its cause and next step, and **the code is not spent**, so fixing the cause and running the same command again works. An unusable code is explained in the app's own words (used, expired, cancelled, wrong workspace).
4. If the machine does not already keep user services running after logout, asks for one administrator confirmation (`sudo loginctl enable-linger`, the system's own password prompt); a refusal is reported as `missing_privilege`. This is the one accept on the machine, and only on machines where it is needed.
5. Runs the existing installer, which spends the code, writes the private installation (0700/0600) and the setup-only user service, then reports `files`, `service` and `check` with the machine's own credential. An interrupted install resumes with the same command; a failure is reported as the step it reached (`files` or `service`, or `check` after install).
6. Prints the machine key fingerprint in the same eight-character groups the app shows, and says to return to the app.

What this change does not do, and the app says so: it does not deliver the runner program (a signed release channel is a separate piece; step 2 checks it is installed and says so when it is not), the setup-only service sends no heartbeat, so a machine never reads Offline until it has sent one (silence proves nothing; the page shows when it was last seen), and trust in the launcher is the hub's HTTPS origin, the same origin that issued the command. Tests use a fake probe, a recording user manager, the real pairing handlers behind in-memory streams, stub programs for the shell launcher and a real in-process hub; no real host, no real user manager and no network.

**Requester carry-over.** The grant frozen at creation includes `requesters`. `copy_from` copies projects, topics, wings, runtime ceiling, in-flight and hourly limits and requesters from an existing active machine of the same workspace; each value is validated again against the current owner (a requester must still be an active trusted workspace agent), so nothing is inherited from a revoked or lapsed source. Explicit fields override the copy. Nothing is copied that is a credential, a provider sign-in or an attestation.

**What the owner approves (the one accept).** When the machine has connected, the owner sees in plain words: the machine's label, its key fingerprint (short groups to compare with what the command printed), what it may read (wings, projects, topics), who may start work on it (requesters), the runtime ceiling and limits. Accepting posts the pairing `version` and the **identity digest** the screen showed; if the grant or the machine key changed in between, the answer is `identity_changed` and the screen reloads. The accept enables the machine and records who approved what and when. It does not attest the binding and it does not sign in a provider: a machine runs Coworkers only when it is enabled **and** attested (unchanged), so "Approved" and "Ready" never claim that work runs. Approval is an ordinary signed-in owner decision; independent owner keys (passkeys) are not in this slice and the app says so.

**States** (one list, derived by the server, one sentence and one next step each): `waiting` (Waiting for the machine), `connected` (Machine connected, waiting for your approval), `approved` (Approved: finishing setup on the machine), `ready` (Set up and checked), `offline` (nothing heard for over 15 minutes), `failed` (a step failed, with its cause), `expired`, `cancelled`, `declined`, `revoked`. `ready` means: approved, all four steps reported `ok`, and the last report is fresh. It deliberately does not mean "Coworkers can run here"; the view carries `binding_state` so a screen can say that the provider connection and attestation still come next, on the screens that exist for them.

**Offline.** The machine's setup service repeats its check report. A `ready` machine whose last report is older than 15 minutes reads `offline` with the age; it comes back to `ready` by itself on the next report. An offline machine is never silently dropped and never revoked by the app.

**Revoke and re-enroll.** One close action: a waiting pairing is `cancelled` (the code stops working), a connected one is `declined` ("This is not my machine"; credentials and grants are revoked), an approved one is `revoked`. All use the existing invalidation (credentials, recovery, consent requests, grants), so a closed pairing leaves nothing usable. Revoking the machine under Runner machines in Admin or turning setup off closes the pairing as well (it reads `revoked`). "Set up again" is a new pairing with the same values: the owner view of a closed pairing carries the values to prefill, and the label is free again because the old machine is revoked.

**Member view.** Any signed-in member of the workspace may read the list: label, kind, state, step states and the sentence, with no code, no fingerprint, no grants and no ids. Members cannot create, accept or close; agent tokens get no access at all. A member sees "Ask the workspace owner to ..." as the next step.

**Concurrency and writes.** Every write is checked for the owner before the body is read, then runs in one control transaction with its audit rows. `request_id` makes create and accept idempotent (a replay returns the same record, with no code); a different body under the same id is `request_changed`. `version` is compare-and-set: a stale accept or close is `stale_version`. Every refusal carries `{error: {code, field, reason, next}}`.

**Audit.** `runner.pairing.create`, `.accept`, `.close`, `.check` (denials only), `.report` (first report per step and every failure), with ids and fixed outcomes, never the code, the envelope secret or a key.

**Secrets.** The code appears in the creation response and in the command, nowhere else: no URL, no query string, no browser storage, no log, no audit row. The command puts the code on the machine's command line once; the line stays in that machine's shell history, where it is useless after first use or expiry (the app says so next to the command, with the leading space that keeps it out of history on shells that honour it). The envelope that accompanies it carries public values only.

**Not in slice 1 (the app says so).** Script (SSH) targets; independent owner approval keys; provider sign-in and binding attestation (they stay on the existing Needs me and Admin screens); a signed release channel for the launcher (B uses the hub's own origin and says so); starting or stopping anything on the machine; a QR code (a short code is not offered either: a short code would be a weaker secret).

#### Slices

- **A, server (this change):** `commonplace/runner/pairing.py`, `pairing_api.py`, additive `issue_in`/`charge` parameters in `enrollment.py`, classification of the routes and tables, tests (denied non-owner, member read-only, agent token refused, stale/used/expired code, replay, wrong workspace, rate limits, CAS, idempotency, audit in the same transaction, offline, close and retry).
- **B, installer:** the launcher, the prerequisite checks, the step reports, tests with a fixture machine (no real host).
- **C, screens:** the guided flow in the Compute section for Classic and Deck, phone layouts, browser tests at 1280x800 and 390x844, screenshots.

#### Screens (change C)

The Add a machine section sits on the Compute screen between the overview and the cloud workspaces, in Classic and in the Deck Management tab (one module, `commonplace/web/static/compute-pairing.js`). The owner's path, with the clicks counted: **Add a machine** (and **Turn on machine setup** once, if it is off), name the machine and say what it may read, **Create the command**, **Copy the command**, run it on the machine, **Approve this machine** when it appears. The page checks on the machine by itself while its state can still change (every three seconds, not in a hidden tab, and it says a state once when it changes); a settled state is never checked again.

What the person sees, per state, is the server's own sentence and next step. `Waiting for the machine` shows the command (held in memory for this pairing only: leaving it, reloading or a replayed request shows "The command was shown once and is not kept" and offers a fresh one) and the four steps as Waiting. `Machine connected, waiting for your approval` shows the sheet: name, the fingerprint in the installer's groups, what it may read, projects, topics, who may start work on it, what it may do alone, cards at once, with **Approve this machine** and **This is not my machine** (which asks first). `Approved`, `Set up and checked` and `Offline` say what is true and, for the last two, that Coworkers can run only after a provider is connected and the machine is attested under Runner machines in Admin. A failed step names the step, the cause and what to do; running the same command again moves the page on without a reload. `Code expired`, `Cancelled`, `Not approved` and `Access removed` offer **Set up again** with the same values filled in. **Cancel this setup** and **Remove this machine** are one action with the same wording as the server's close.

Honest limits shown on the page: the command does not install the runner program, the second kind (a script target) is listed as not available yet and cannot be chosen, and there is no turn-off control for machine setup here because turning it off removes every machine added this way.

Phones: the actions sit in the flow under the last field (no sticky or fixed bar, so nothing covers a field), buttons are full width and 44px tall on any touch screen, inputs are 16px, the command wraps instead of scrolling sideways, and a refusal's summary takes focus once while each field also shows its own message. Members read the same list with the server's member wording and no command, fingerprint, grant or control.
