## web-and-console

- **Paths:** `commonplace/web/`, `commonplace/console/`
- **Owns:**

  - The instance-admin sign-up waitlist routes (`GET`/`POST /api/admin/instance/waitlist`, mounted from `commonplace/capacity.py`) and the plain `waitlist` sign-in message in `signin.js`.

  - The Admin runner block (`adminrunners.js`): owner-only machine list, enroll, attest, wings edit and two-step revoke over `/api/runners`, with one-time token display (and a token-free setup note) kept outside the Admin sections' redraws; revoking a machine clears its token panel.

  - Runtime switch/orchestrator/machine read projectors, effective limits, stuck facts and separate legacy needs-me rendering in the existing Admin lifecycle.


  - The GET-only Admin dispatcher, scoped users/connections projections, account/palette navigation and independently settling page controller.


  - The card panel's Runs section (`cardruns.js`) lists the card's start jobs with Cancel and the owner's Resolve choice, and offers the owner a Start agent control fed by the options route, with plain words for each refusal.

  - The cookie-authenticated web app and the live console, including the owner orchestration switch, project seat-consent controls and default agent-limit display.

  - Settings mounts the owner-only Scheduled prompts section (`agent-schedules.js`) next to Agents; members see nothing there.

- **Promises:**

  - Settings → Agents offers an unticked `cards:decide` consent on create, shows the grant on PAT and OAuth rows with **Stop answering**, and offers **Allow answering Needs-me** only on rows the server marks `decide_grantable` (the viewing owner's own eligible, unexpired PATs; never agents, runners, seats, board coordinators or other members' connections; the grant route refuses coordinators too); the consent copy comes from `decide_consent` in `GET /api/connections`; `PATCH /api/connections/<id>` with `{"decide": bool}` grants (PAT only) or withdraws (PAT or OAuth).

  - Event polling sends the readable-set epoch and refetches the open screen, including card details, before adopting a reset cursor. The owner feed resets non-empty member-era epochs; pre-deploy member tabs require a reload to resume live events.

  - Non-owner person event feeds project payloads and cursors into visible events; private-only insertions do not change member responses.

  - Admin callback eligibility reconstructs person policies with the queried membership role rather than assuming private read authority.

  - Sign-out and expiry stop an existing Admin controller; it is null until mounted.

  - Background prerequisite failures keep rendered Settings sections and form drafts mounted, surfacing the named error separately; initial and foreground failures still render Retry.

  - The Admin runtime panel lists each machine's requesters and the active trusted agents an owner may add; its one write is the owner-only add or remove of a single requester on the stored list, and the project settings models form binds its save handler before the page's slow reads.

  - Settings and its prerequisite reads use ten-second per-request deadlines through JSON decoding; each failed section is named with Retry while successful sections retain saved values.
  - The navigation-only instance flag falls back to false on read faults without breaking /api/me; receipt parent refreshes invalidate older mode generations.

  - The runner block never stores, logs or redraws enrollment tokens: they live only in a dismissible panel that sign-out, session expiry and leaving Admin also clear.

  - Admin validates runtime metadata, probes optional columns, filters inactive grants and preserves paginated rows with source-specific retry errors.

  - Instance deploy/backups and /api/me share the exact per-request RO users.is_admin flag predicate after person-owner checks. Operational denial text is exact; deployed SHA is package data, health is independently validated, and receipt mode retries preserve siblings. test_admin_instance_api.py and test_admin_instance_ui.py cover authority, schemas, independent deadlines and disposal.

  - Only the exact Admin route bypasses shared refresh; leaving it disposes requests, card hashes load their requested card, joined counts match rows and Retry repeats the failed page.

  - Uses generation-aware encrypted RO board/runner reads and workspace-scoped control SELECTs only; injected UTC/monotonic clocks, query-only connections, short busy bounds and progress interrupts apply to all new sources. No store, seat or subscription initialization or application lock; pagination is list-bound and deduplicated.

  - Admin requires a signed-in current-workspace owner before read-only SELECTs; explicit credential-free allowlists, display redaction, 100-row cursors, injected clocks and 2 second source/5,000 ms client deadlines preserve private state boundaries.


  - Imported-history consent: The owner form offers independent unticked import and reader checkboxes and displays reader authority beside Revoke.
  - The Connect checklist renders the supplied webhook event recommendation, including card moves and optional card creation, with escaped values.

  - Enabled waiting tags show dependency titles or occupied-slot counts and suppress the available tag.

  - `blockers.js` renders the card-details Blocked by section (blocker title, Waiting or Done, Clear, Add blocker by card number) only when card views carry `blocked_by`, and edits the list through the card `PATCH`.

  - `waiting.js` shows the server's waiting sentence as a board tag and in the card details, and a strip of `waiting_notes` above the board; it adds no sentences of its own.

  - `card-reviewer.js` renders the card panel's Reviewer row: a member picker for the owner (server-enforced, default says the workspace owner reviews) and a read-only name for everyone else; the board card carries a Reviewer tag.

  - A current refresh failure clears that responsibility, so later background reads still preserve busy Rules forms.

  - A background refresh never replaces an edit, hand-off or ask form open in the card panel, including one opened while the refresh's card read was in flight. On Needs me (including a hash the router does not recognise, which draws it too) the item's detail waits (paused) while a hand-off form is open; Cancel, a saved hand-off, choosing another item or a filter that drops the item gives the panel back and focuses the item heading, and a read that failed meanwhile is repeated. J and K do not move the selection while a form is open, wherever focus is. Closing the panel does not read a card, so the close itself does not reopen it; a later change to the Needs me payload re-selects the item and opens the panel again, as before. When a filter leaves no items, focus goes to the filter button. On a card route the panel redraws from a fresh card read.

  - The latest refresh carries a pending foreground render for its screen, so background events cannot leave Settings or Console on Loading; completed Settings views keep their usual background-update policy.

  - The person-owner task-type API rejects non-object JSON with 400 and replaces a connection’s sorted allowlist atomically; GET never creates a boards store.

  - The web app calls the same services as REST; it shows only what the signed-in person may read.

  - **Coordinator status:** Project settings > Orchestrator reads the read-only checkpoint (`roles/main/checkpoint`) and shows who holds the lease, when it runs out, the accepted config version and counts of open, in-doubt and unstarted work; only a signed-in owner sees End lease, behind a confirm, and ending it leaves unfinished steps listed. A board without orchestration, or one the person cannot read, shows nothing.

  - **Browser GitHub connection:** Needs shows local GitHub connection state and validated PR links; selection and session teardown discard connection state.

  - **Connection lifecycle proof:** Versioned fixture cards preserve the loading assertion; stopped Needs controllers clear the detail DOM as well as cached connection state.

  - **Connection teardown boundary:** Stopping clears rendered GitHub details while preserving the established teardown contract for other Needs details.

  - **Connection recovery:** Callbacks reopen the selected Needs item; GitHub-only status failures preserve card actions, and connections in another workspace show an explicit disconnect control.

  - Needs me renders origin/configuration as approvals, requires a release reason and posts directly; coordinator configuration navigates to existing settings without mutating a card. Proposal Decline and plan Reject controls follow reject permission independently of approve permission and use the existing rejection handler.
  - Shell needItems includes approvals when counting each project and retaining selected details on refresh; release ranks between person decisions and plan review.

- **How it fails:**

  - Runtime suberrors preserve completed sibling facts; needs-me and stuck settle independently. Deployment/health and backup receipts have independent subpanels. Missing optional data stays absent and corrupt sources return safe errors.


  - Safe source errors remain local to one section; session expiry/disposal clears data and pending generations. Ineligible operational sections send no requests; token contact is not liveness.


  - Rendering errors surface in the browser; the server state is unaffected.

- **Tests:**

  - tests/test_admin_runtime_api.py, tests/test_admin_runtime_controls.py, tests/test_admin_runtime_ui.py and tests/admin_runtime_ui.mjs cover fixed-time accounting, authority, wildcard subscription eligibility, no-write/lock sentinels, partial failures and GET-only pagination.

  - tests/test_runner_requesters_admin.py and the requesters case of tests/admin_runtime_ui.mjs hold the Admin requester list, candidates, the per-requester owner-only edit with its version precondition and the other-workspace refusal.


  - `tests/test_admin_api.py` covers person/role/workspace refusals, fixed lifecycle time, pagination, redaction and no writes/locks; `tests/test_admin_ui.py` and `tests/admin_ui.mjs` cover cold routing, independent deadlines, retries and disposal.


  - The `tests/test_workbench_` files, the `tests/test_console_` files and the `tests/test_commonplace_console_` files.

  - `tests/workbench_e2e/test_refresh_forms.py` drives a real browser: an open hand-off form (also one opened mid-refresh) and a comment in progress survive a background refresh, the panel updates once the form is cancelled, and on Needs me a queue change leaves an open hand-off form alone and Cancel closes it.

  - `tests/orchstatus.mjs` (run by `tests/test_orchstatus.py`) covers the status block states, the End lease confirm, refusals and escaping; `tests/test_orch_lease_status.py` covers the checkpoint fields it reads and the owner-only force-release contract.

  - `api.js` joins a refusal's `error` and `next_step` and words a 403 without a JSON body; `tests/test_commonplace_owner_403_message.py` and `tests/owner_403_message.mjs` cover it.
