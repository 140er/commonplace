# Usability audit — November 2026

Card: #372. Audit date: 2026-11-13. Observed revision:
`4a6bb3f192b5465a84a45c6ea700c359b2f0592e` (`develop` when the walkthrough began).
This is an audit and proposed backlog, with no product changes.

The main blockers are getting a second person into a usable workspace, keeping
navigation available on a phone, and completing setup without asking a bot.
Priority selection, origin release, config acceptance with a diff, reservation
removal, and learning acceptance already have working controls. Their remaining
problems should not be described as wholly missing features.

## Method and evidence limits

The real web gateway ran locally against a newly created temporary data directory.
It began with no accounts or workspaces. Two disposable email accounts signed up
through the email form and confirmation page; mail delivery used a loopback-only
test mailbox. The first account's automatically created workspace and board were
empty. A second project, cards, bot and conversation were then created in the UI.
There was no production access, real user content, configured external provider,
real machine connection or external application traffic. Outbound gateway
connections were blocked, and the browser allowed only the local app origin.

Chromium used separate fresh owner/member contexts. Captures use a device scale
factor of **2**, at **1280×800** and **390×844** CSS pixels. Viewport captures are
2560×1600 and 780×1688 pixels respectively. Credentials in reveal panels were
masked. Screenshots remain outside version control. The evidence column gives
filenames only: `stem-{desktop,phone}.png` means the two actual files with those
suffixes, not a file literally containing braces.

Important qualifications:

- A fresh browser at this revision selects Classic unless a saved preference or
  `?ui=deck` selects Deck (`commonplace/web/static/deck.js`). Deck was explicitly selected for
  the main audit. Initial Classic observations are not used to claim Deck passes.
- Cold signed-out loading hit HTTP 429 and a raw `slow down` response. Subsequent
  flows used the documented local rate-limit switch set to off. Successful later
  sign-in does **not** resolve or disprove that default-configuration failure.
- The ordinary Join workspace form returned HTTP 403. To inspect the member
  experience beyond that failure, the same local invite was accepted with an
  explicit valid Origin using a fixture request. This was **not a UI pass**.
- Synthetic API/store fixtures supplied two config versions, a restricted-origin
  card, an assigned card, an owner question and a proposed learned preference.
  They also established connection access needed to produce those states. These
  preparations are excluded from the UI interaction counts. Reviewing, accepting,
  releasing, handing off and answering used actual UI controls and real writes.
- On phones, some secondary surfaces were opened on desktop and then resized for
  inspection because the phone board itself loses the route to them. Such a
  nested control can work while the complete phone journey remains partial.
- No real agent was launched, no real worker termination was attempted, and no
  model output, notification delivery, external sign-in, native Mac or iOS app
  was validated. Start/stop and trust rows name their reached prerequisites.
  A web phone viewport is not evidence of native-app coverage.
- Source-only parity findings below are explicitly separate from browser
  observations. There is no new automated E2E coverage in this documentation PR.

Counts are UI activations from the **named entry surface**, excluding typing,
scrolling, waiting and switching to the mailbox. A select change counts as one
activation. Screens count views, dialogs and detail panels. A blocked flow lists
only the activations reached; no hypothetical successful count is substituted.

## Owner flow table

“Yes” means the named operation completed in the UI under the stated local
configuration. “Partial” means reachable controls with a missing step, prerequisite
or phone entry path. “No” means no working UI path was found. These labels do not
promise successful execution on a connected machine.

| Flow and entry surface | UI: desktop / phone | Activations / screens reached | Observed result or point of friction | Screenshot filenames |
|---|---|---|---|---|
| Fresh signup, signed-out app | Partial / partial | 1 submit, then 2 for mailed link + confirm / 3 | Cold load exposed a raw JSON response and later 429. Account/workspace creation completed after retry; G02. | `01-signin-desktop.png`, `01-signin-phone.png`, `02-email-sent-phone.png`, `05-after-confirm-desktop.png` |
| Returning email sign-in and sign-out | Yes / yes, rate limits off | 1 sign-out; 1 email submit + 2 link/confirm / 3 | Sign-out, email, confirmation and return worked; this is the qualified continuation, not a cold-start pass. | `84-owner-signed-out-{desktop,phone}.png`, `85-returning-signin-email-{desktop,phone}.png`, `86-email-confirmation-{desktop,phone}.png`, `87-returning-owner-{desktop,phone}.png` |
| First workspace setup, signup | Yes / yes after authentication | 0 extra / 1 | An empty owned workspace and default project are created automatically. | `06-owner-home-desktop.png`, `06-owner-home-phone.png` (initial Classic baseline) |
| Switch workspace, workspace button | Yes / partial from board | 2 / 2 | The workspace button opens the general command palette first; Switch workspace then opens the membership list. No create-workspace action; G17. | `73-workspace-switch-{desktop,phone}.png` |
| Create project, Projects list | Yes / yes once Projects is reached | 2 / 2 | New project → enter a name → Create project. No bot required. Reaching Projects from a phone board is the separate G04 problem. | `15-project-list-{desktop,phone}.png`, `16-create-project-{desktop,phone}.png`, `17-new-project-{desktop,phone}.png` |
| Create and prioritise a card, project board | Yes / yes | 2, or 3 with priority / 2 | Add card has title, details, person/agent assignment, label and priority. Added a card with High priority. | `18-deck-add-card-{desktop,phone}.png` |
| Edit card, board card | Yes / yes | 3 / 3 | Open card → Edit → Save; title change persisted. | `10-deck-edit-{desktop,phone}.png`, `11-deck-card-{desktop,phone}.png` |
| Change priority, board card | Yes / yes | 3 / 2 | Open card → choose priority → Set; High changed to Urgent. A missing priority picker is not a current gap. | `11-deck-card-{desktop,phone}.png` |
| Move status, board card | Yes / yes | 3 / 2 | Open card → choose Doing → Move; the board and assignee updated. | `11-deck-card-{desktop,phone}.png` |
| Release restricted-origin card, card details | Yes / yes | 2 / 2 | Enter a reason and Release. The release form disappeared and the activity recorded the reason. | `46-origin-release-card-{desktop,phone}.png`, `47-origin-released-{desktop,phone}.png` |
| Drop a reservation, assigned card | Partial / partial | 3 / 3 | Hand off → type `anyone` → Hand off returned the card to the pool. There is no plain “Remove assignment” action; G12. | `48-reservation-handoff-{desktop,phone}.png`, `49-reservation-dropped-{desktop,phone}.png` |
| Inspect and accept config, project board | Yes / partial entry | 3 / 2 | Settings → Changes → Accept displayed the actual old/new prompt diff and accepted v2. First-version review shows the full config. Phone entry is hidden; G04. | `42-config-diff-{desktop,phone}.png`, `43-config-accepted-{desktop,phone}.png` |
| Reject config, project settings | No / no | 1 Changes disclosure / 1 | A proposed version has Changes and Accept but no Reject. The rejection endpoint exists; G05. | `42-config-diff-{desktop,phone}.png` |
| Author/change config, project settings | No / no | 1 Settings entry / 1 | Config versions can be reviewed, but there is no editor or proposal action; G14. | `13-project-settings-{desktop,phone}.png`, `42-config-diff-{desktop,phone}.png` |
| Give or withdraw start trust, project settings | Partial / partial | 1 Settings; disclosure present with connected prerequisite / 1 | Start-permission UI is conditional on orchestration and a connected project connection; no machine was ready. Grant/withdraw was not executed. The control disappears when the connection is revoked; G13. | `69-trust-prerequisites-{desktop,phone}.png`, `72-connection-revoked-{desktop,phone}.png` |
| Enable managed starts, workspace Settings | Yes / yes | 1 toggle / 1 | Changed the explicit workspace opt-in and observed Saved. This does not install or start an agent. | `67-agent-start-setting-{desktop,phone}.png` |
| Start an agent, unassigned card | Partial / partial | 1 card open / 1 | Runs says no machine is ready and directs the owner to “Workspace settings, under Runner machines”; the enrollment form is actually in Admin. No real launch was attempted; G08/G13. | `70-agent-start-prerequisite-{desktop,phone}.png` |
| Stop an agent, card Runs | Partial / partial | 1 card open / 1 | No run exists, so there is no stop action to exercise. Source provides cancellation for eligible jobs; real termination remains unproved. | `70-agent-start-prerequisite-{desktop,phone}.png` |
| Agent concurrency limit, project settings | Yes / partial entry | 2: Settings + stepper / 1 | Changed 3 to 4, with a saved timestamp and Use default. Only one-step controls are offered; workspace/machine limits are separate. | `68-agent-limit-{desktop,phone}.png` |
| Answer Needs me, Needs me list | Yes / yes once reached | 2: item + choice / 2 | Opened the synthetic owner question and chose Short checklist. The item left the queue. Its later ordinary decision was not a held approval. Phone navigation from a board is G04. | `51-needs-me-{desktop,phone}.png`, `52-needs-decision-{desktop,phone}.png`, `53-decision-after-choice-{desktop,phone}.png` |
| Invite a member, Admin | Partial / partial | 1 Make invite link / 2 | Invite creation and one-time reveal worked. The recipient's ordinary join failed; G03. Sending the link is manual. | `23-invite-created-{desktop,phone}.png`, `27-member-join-{desktop,phone}.png`, `28-member-after-join-{desktop,phone}.png` |
| Create connection/token, Settings → Agents | Partial / partial | 2 including client selection / 2 | A name containing a space failed with a generic error. A machine-style name succeeded; one-time token and config were available. No external client was connected; G11/G13. | `19-token-create-{desktop,phone}.png`, `20-token-create-result-{desktop,phone}.png`, `29-token-reveal-{desktop,phone}.png` |
| Rotate and revoke connection, Agents list | Yes / yes | 2 each including confirmation / 2 | Rotation produced a replacement reveal; revoke changed the row to Revoked. Neither action proves termination of a running process. | `30-connection-controls-{desktop,phone}.png`, `71-token-rotated-{desktop,phone}.png`, `72-connection-revoked-{desktop,phone}.png` |
| Notifications, workspace Settings | Partial / partial | 1 Settings entry / 1, substantial scroll | Phone notifications say “Not set up on this server.” No delivery was attempted. The section is below the long action-policy list; G18. Webhooks have Add/Test/Remove UI. | `66-notifications-{desktop,phone}.png`, `89-tool-policy-{desktop,phone}.png` |
| Import memory, Memory and Settings | No / no | 1 entry / 1 per surface | Memory has search/filter controls. Settings offers an agent import permission, not a file chooser, format preview, import progress or report; G06. | `65-memory-import-missing-{desktop,phone}.png`, `19-token-create-{desktop,phone}.png` |
| Create bot, Bots | Yes / yes | 2 / 2 | New bot → name/job/instructions → Create; opened its thread without a bot doing the setup. | `32-bots-empty-{desktop,phone}.png`, `33-bot-create-{desktop,phone}.png`, `34-bot-thread-{desktop,phone}.png` |
| Share bot, bot settings | No / no | 1 settings button / 1 | Name, job, instructions, learning, memory and archive are present; no people/access/share control, despite share APIs; G07. | `35-bot-settings-{desktop,phone}.png` |
| Review learning, bot settings | Yes / yes | 3: settings + learned + Accept / 2 | A fixture preference showed Accept, Reject, Edit and Open conversation. Accept removed its pending controls. Pause/forget controls exist; rejection/edit/forget were inspected but not executed. | `36-bot-learning-{desktop,phone}.png`, `55-learning-proposal-{desktop,phone}.png`, `56-learning-accepted-{desktop,phone}.png` |
| Compute map, Compute | Yes for viewing / yes | 1 / 1 | Empty map, detail tabs, route selector and zoom controls render. No actual remote reachability was tested. | `37-compute-map-empty-{desktop,phone}.png` |
| Add machine/site without a bot, Compute map | No for self-contained setup / no | 1 Add machine / 1 | The action copies a prompt or displays a collector command instead of an in-app enrollment flow. Add site uses the same agent-mediated direction; source-confirmed; G08. | `38-compute-add-machine-{desktop,phone}.png` |
| Compute management, Compute → Management | Partial / partial | 2 to open a gateway form / 2 | Inventory and forms exist. A gateway needs a separately provisioned credential reference and full key; no provisioning action is offered. No connection, discovery or script was submitted; G09. | `45-compute-management-loaded-{desktop,phone}.png`, `74-compute-gateway-form-{desktop,phone}.png` |
| Project Coworkers, bot → Coworkers | Partial / partial | 1 / 1 | Empty state says no project Coworker is recorded; no create/connect action or prerequisite checklist; G19. | `57-coworkers-{desktop,phone}.png` |
| Conversation Coworker setup, Open conversation | Partial / partial | 4 for bot + DM forms / 3 | A second, separate Create bot form exists here. New conversation asks for raw member IDs and an optional project slug; G10/G19. | `58-coworker-conversations-{desktop,phone}.png`, `59-coworker-create-bot-{desktop,phone}.png`, `60-coworker-new-conversation-{desktop,phone}.png`, `64-coworker-member-picker-{desktop,phone}.png` |
| Coworker conversation memory | Yes / yes | 1 Add memory / 1 | Saved exact words scoped to the conversation and saw a Hide control. This is not workspace memory import. | `61-coworker-memory-{desktop,phone}.png` |
| Coworker response and cancellation | Partial / partial | 1 Send + 1 attempted cancellation / 1 | Message persisted, initially Queued, then Needs configuration. Cancellation did not yield a confirmed cancellation before configuration settled. Refresh timeouts also appeared; no model or real worker result is claimed; G19. | `62-coworker-send-{desktop,phone}.png`, `63-coworker-cancel-result-{desktop,phone}.png` |

## Member flow table

The second account completed email sign-in with local rate limiting off, and
received the invite created in Admin. Clicking **Join workspace** produced
`same-origin request required` (G03). After the explicitly qualified fixture join,
the active workspace was the shared workspace, but `/api/needs-me` returned 403.
The application bootstrap treated that as fatal and displayed **Disconnected —
only a workspace owner can do that** (G01). Retry repeated it.

Consequently no downstream member write is reported as a UI pass. Diagnostic
route visits below establish the extent of the common entry blocker; they are
not counted as discovering navigation in the app. Each reached one screen and
zero usable task controls. Owner-only operations must stay owner-only when G01
is fixed; their absence must not be “fixed” by granting members owner authority.

| Member flow attempted | UI result and reached activations/screens | Screenshot filenames |
|---|---|---|
| Signup/sign-in | Yes after the rate-limit qualification; 1 email submit + 2 link/confirmation / 3 | `24-member-signin-{desktop,phone}.png`, `25-member-email-sent-{desktop,phone}.png`, `27-member-join-{desktop,phone}.png` |
| Join shared workspace | No through ordinary form; 1 Join / 1 confirmation then error | `27-member-join-{desktop,phone}.png`, `28-member-after-join-{desktop,phone}.png` |
| Workspace/project setup and switch | Blocked at bootstrap / 1; owner restrictions remain separate | `75-member-projects-{desktop,phone}.png` |
| Create cards | Blocked before Add card / 1 | `76-member-cards-{desktop,phone}.png` |
| Edit cards | Blocked before details / 1 | `77-member-card-controls-{desktop,phone}.png` |
| Prioritise cards | Blocked; source additionally makes priority owner-only / 1 | `77-member-card-controls-{desktop,phone}.png` |
| Move cards or drop a reservation | Blocked / 1; reservation authority depends on ownership | `77-member-card-controls-{desktop,phone}.png` |
| Restricted-origin release, config accept/reject/diff | Owner-only by policy, plus fatal bootstrap / 1 | `77-member-card-controls-{desktop,phone}.png`, `78-member-settings-{desktop,phone}.png` |
| Trust, starts/stops and agent limits | Owner management is restricted; no usable member surface / 1 | `78-member-settings-{desktop,phone}.png`, `83-member-compute-{desktop,phone}.png` |
| Needs me and assigned reviews | Fatal owner-only request instead of a scoped member experience / 1 | `79-member-needs-{desktop,phone}.png` |
| Tokens, own agent connections, notifications | Blocked before any eligible own-connection controls / 1 | `78-member-settings-{desktop,phone}.png` |
| Memory search/import | Blocked / 1; owner import UI is also absent | `80-member-memory-{desktop,phone}.png` |
| Bots: create, shared access and learning review | Blocked before the bot roster / 1 | `81-member-bots-{desktop,phone}.png` |
| Compute map/management | Blocked / 1; management remains an owner operation | `83-member-compute-{desktop,phone}.png` |
| Coworkers/conversations | Blocked / 1 | `82-member-coworkers-{desktop,phone}.png` |

## Ranked gaps

P0 blocks an entire ordinary role. P1 blocks a core journey or requires a bot/API.
P2 makes an available action difficult to find or finish. P3 covers less frequent
advanced parity. The order within a priority puts visible, frequent problems first.
G01–G13 and G17–G19 have browser witnesses; G14–G16 and G20–G24 include source-only
coverage as detailed in the inventory below.

| Rank | Priority | Gap | One-line reason and user-visible effect |
|---|---|---|---|
| G01 | P0 | Member bootstrap calls an owner-only queue | An ordinary member sees Disconnected and cannot use any workspace flow. |
| G02 | P1 | Cold sign-in can consume its anonymous request budget | New users can submit a visible form and get raw JSON/429 instead of completing sign-in. |
| G03 | P1 | Join form fails its same-origin check | A valid invited person cannot join through the confirmation button. |
| G04 | P1 | Phone boards/Needs me lose Deck navigation | Settings, Today, Compute and workspace tools vanish behind an inconsistent two-tab shell. |
| G05 | P1 | Config review has no Reject action | Owners can accept a proposed change but cannot explicitly decline it in the app. |
| G06 | P1 | No memory file-import journey | A user must configure an agent or call an import API instead of selecting an export file. |
| G07 | P1 | Bot sharing is API-only | An owner cannot give a member access, inspect the audience or withdraw a share in bot settings. |
| G08 | P1 | Compute map setup requires an agent or terminal | “Add machine” is not a self-contained app action for a person with no bot. |
| G09 | P2 | Compute credential prerequisite has no setup path | The gateway form asks for an existing credential reference without helping create/select one. |
| G10 | P2 | Conversation invites require raw user IDs | A person must obtain and paste identifiers instead of selecting workspace members. |
| G11 | P2 | Agent Name silently expects an identifier | Ordinary names with spaces fail only on submit with a generic multi-cause error. |
| G12 | P2 | Reservation removal is hidden in free-text handoff | Users must know to type `anyone`, supply a note and interpret “pool.” |
| G13 | P2 | Start/trust prerequisites are scattered or disappear | Users are directed to the wrong settings area and cannot see a complete connection/machine/trust checklist. |
| G14 | P2 | Config authoring/proposal is API-only | Owners can review versions but cannot originate a workflow change without an agent or API. |
| G15 | P2 | Memory offers search but no general note composer | Saving standalone workspace notes is available to agents but has no clear equivalent on Memory. |
| G16 | P2 | Snooze/wake and goal/split controls are missing | Scheduling a card's return or managing its goal requires agent/API knowledge. |
| G17 | P2 | No additional-workspace creation entry | Signup provisions one workspace, but the switcher only lists existing memberships. |
| G18 | P2 | Settings exposes a long raw-action policy list | Common settings such as notifications sit below implementation vocabulary and extensive scrolling. |
| G19 | P2 | Coworker setup has disconnected concepts and dead ends | Two bot concepts, an empty project roster and a configuration status do not explain the next usable step. |
| G20 | P3 | Workflow recovery has no complete owner surface | Restore acknowledgement, resume, forced release and step recovery require APIs. |
| G21 | P3 | Advanced memory organisation/maintenance lacks UI parity | Graph facts, drawers, diary and connections between memories remain API operations. |
| G22 | P3 | Session-history and handoff controls are not discoverable in Deck | History pages exist, but starting a handoff and following a whole chain have no clear Deck entry. |
| G23 | P3 | Skill and operational status inventory has no plain app view | Users cannot readily inspect the capabilities, bridge/meeting status and cost/budget controls exposed elsewhere. |
| G24 | P3 | Some project operations lack direct controls | Project panel editing and coordinator replacement are not the same as the exposed model/limit settings. |

No additional P0 is asserted from the deliberately unconfigured model, machine or
notification environment. Their successful operation needs separate integration
proof after the relevant UI work.

## Agent/API actions without a clear UI equivalent

This inventory was checked against the registered gateway extensions,
`commonplace/policy.py`, the board/config/runner schemas and bot/Compute routes,
then compared with `commonplace/web/static/`. Names below are shipped product API
identifiers. A listed source gap means no corresponding control was found; it is
not a claim that the endpoint was executed during the walkthrough.

The following are the missing or partial equivalents, including operations
outside the primary walkthrough. Existing controls and protocol-only operations
are listed separately so they cannot be mistaken for new missing buttons.

| User intention | Public actions with missing/partial UI | Closest UI and gap |
|---|---|---|
| Decline or retire a config | `orch_config_reject`, `orch_exp_retire` | Versions have Accept only; G05/G20. |
| Create/edit a workflow config | `orch_config_propose`, `orch_config_write`, `orch_config_render` | Changes is a review disclosure, not an editor or export action; G14. |
| Import transcripts or facts | `POST /api/import`, `facts_import` | Agent consent exists; file selection, preview and import report do not; G06. |
| Share a bot or change access | `POST/PATCH/DELETE /api/bots/<id>/shares`, access/access-preview routes, shared-thread creation | Bot settings have no sharing/audience controls; G07. |
| Share learned knowledge or configure learning mode | `share_learned`, learning settings/policy and per-bot mode routes | Review/accept/reject and pause are present; sharing and an explicit off/ask/automatic mode chooser are not; G07/G19. |
| Build/edit the topology without a bot | `topology_propose`, `topology_discover_site` | Read-only map and agent-directed Add machine/Add site; G08. Route lookup and map reads do have UI. |
| Provision/select a Compute credential | Credential provisioning used by gateway/target APIs | Management asks for a separate credential reference; G09. |
| Save a general memory note | `memory_save`, standalone `mempalace_add_drawer` | Memory is search-first; bot notes, conversation memory and Pocket are narrower scopes; G15. |
| Snooze/wake a card | `task_snooze`, `task_unsnooze` | No Snooze or Wake control found in card details/menu; G16. |
| Set goals and split remaining work | `goal_list`, `goal_set`, `card_goal`, `card_split_done` | No corresponding goal/split UI found; G16. |
| Create another workspace | Workspace creation through the control/CLI layer | Workspace switcher lists memberships only; G17. This is a setup gap, not a claim that a public workspace-creation endpoint exists. |
| Recover workflow ownership and paused work | `orch_restore_status`, `orch_restore_ack`, `orch_resume`, `orch_force_release`, `orch_void_step`, `orch_reassign_step`, `orch_exp_retire` | Status can be viewed in project surfaces; no complete recovery action UI; G20. |
| Inspect/resolve detailed workflow findings and steps | `orch_finding_ack`, detailed `orch_checkpoint`, `orch_journal_*`, `orch_gate_*` records | Coworker status is a partial projection, not a navigable finding/step ledger; G20. |
| Edit graph facts and their history | `mempalace_kg_add`, `mempalace_kg_save`, `mempalace_kg_supersede`, `mempalace_kg_invalidate`, graph query/timeline/stats/wing APIs | Pocket can pin a fact; it is not graph/history management; G21. |
| Organise and maintain stored memory | Drawer update/delete/bulk-delete/delete-by-source; diary read/write; taxonomy/wing/room/drawer listings; checkpoint, duplicate checks, filing state | Search can return words, but not administer these structures; G21. Source storage must remain verbatim in any future UI. |
| Follow and manage memory links | Tunnel create/delete/list/follow, hallway list/delete, traversal, graph statistics, mesh-peer reads | Memory search/map is not a complete management surface; G21. |
| Start a conversation handoff or inspect a chain | `handoff_start`, `thread_chain`, `thread_get`, `thread_recall` | Separate thread/history and memory-settings pages exist; no obvious Deck handoff/chain entry; G22. |
| Inspect skills and detailed operational status | `skills_list`, `skills_get`, `skills_select`, `bridge_status`, `meeting_status`, `costs_summary`, cost/budget settings APIs | Console shows connection activity, not this complete inventory; G23. |
| Replace/clear a project's coordinator or edit its panel | `board_set`/`space_set` coordinator change, project-panel write/clear APIs | Project settings expose connection/model/limit controls, not a clear independent replacement/panel editor; G24. |

Present UI equivalents, with qualifications already recorded above:

- `board_add`/`space_add`, list/read aliases, project memory-section creation,
  `board_own_wing`/`space_own_wing`, and `space_post`/`space_read`: Projects,
  project memory and chat. General project settings remain subject to G24.
- `task_add`, `task_list`, `task_show`, ordinary `task_update`, `task_comment`,
  `task_handoff`, `task_approve`, `task_take_next`, `ask`, `decide`, `needs_me` and
  `orchestrator_inbox`: board/details, Hand off, status/review controls and Needs
  me. Choosing Doing is the person-facing start-work equivalent, not an agent
  process launch. Asking another person uses the handoff/review form.
- `card_start`, `orch_card_start`, `card_start_status`, `card_start_cancel`,
  `card_start_resolve`: Runs contains start/cancel and uncertain-outcome controls
  when eligible. Only the no-ready-machine boundary was reached in this audit;
  this is **source parity, not launch/stop proof**. Compute management likewise
  has proposal/approval/cancellation, scope, script and target forms.
- `orch_config_get`, `orch_config_diff`, `orch_config_accept`: project config
  versions and Changes; acceptance was exercised. Rejection/authoring are not.
- `memory_search`, `memory_get`, `memory_link`, `fact_save`: Memory source and
  link actions, Pocket and scoped memory forms. These do not replace import,
  standalone note writing or graph administration.
- Bot creation/settings, ordinary conversation creation/messages, personal bot
  notes, learned-list/review, `forget_learned`, `forget_all_learned` and
  `pause_learning`: Bots and its memory/learning sheets. Scope/sharing and mode
  setup remain G07/G19; only acceptance was executed on a learned item.
- `rules_get`, `rules_publish`, `rules_feedback`, `rules_changed`: Rules has
  reading, proposals, edits, acceptance and suggestion controls. This is a
  source comparison, not an additional executed Rules walkthrough.
- Webhook add/list/test/remove, agent token/grant/rotation/revocation, project
  limits/models, schedules, and runner enrollment/attestation/revocation have
  UI surfaces. Some require technical setup or inactive prerequisites; those
  should not be labelled absent. Delivery, scheduling and runner execution were
  not exercised.

Heartbeat, connection polling, event acknowledgement/waiting, session-start
context assembly and journal/gate writes made **by an executing agent** are
protocol mechanics, not separate owner intentions. Their UI equivalent is
automatic status/activity plus a usable owner recovery surface. G20 covers the
missing recovery surface; it does not propose manual heartbeat or fake-execution
buttons. Low-level event/artifact/patch APIs similarly need readable activity,
artifact access and recovery, rather than exposing their wire format to users
(G20/G21). No hidden endpoint was treated as a successful user journey.

## Proposed cards

These are proposals only. No cards were filed by this audit. Each entry maps to
one ranked gap; acceptance should be proved in fresh owner/member browser contexts
at both sizes without API calls standing in for user actions.

| Gap / priority | Proposed title | Acceptance criteria |
|---|---|---|
| G01 · P0 | Let members open their workspace | A newly invited member loads boards, bots, memory and eligible reviews without an owner-only bootstrap failure; disallowed management stays hidden/refused; Retry recovers a transient read error. |
| G02 · P1 | Make cold email sign-in finish reliably | A fresh uncached browser can load and submit sign-in under default limits; assets cannot exhaust the user's authentication journey; loading, sent, throttled and expired-link states stay in a readable app screen with recovery. |
| G03 · P1 | Make invite confirmation join the workspace | A member signs in, opens a valid one-use invite and taps Join successfully with the shipped response policies; replay/expired/wrong-person cases remain safe and actionable. |
| G04 · P1 | Keep Deck navigation on every phone screen | From a board, card, project settings and Needs me, Today/Bots/Compute/Settings remain reachable in at most two taps; Projects/Memory/Needs me and workspace switch remain discoverable; drawers close and return predictably. |
| G05 · P1 | Add config rejection beside the diff | Every proposed config offers Changes, Accept and Reject to its owner; show actor, version and result; a rejection cannot be mistaken for acceptance; members cannot mutate it. |
| G06 · P1 | Import memory files from Memory | Memory → Import lets a person choose a supported export, preview source/scope/count, explicitly confirm, see progress/report and retry safely; exact reimport adds no duplicates; words remain verbatim; no agent/token setup is required. |
| G07 · P1 | Manage bot sharing in bot settings | Pick workspace people by name, preview who can talk/see shared history, grant/change/revoke access, and clearly separate private from shared threads; learning sharing has an explicit audience and preview; verify access as the member. |
| G08 · P1 | Enroll Compute machines from the app without a bot | Add machine offers a guided install/enroll route and one explicit acceptance boundary; Add site offers a direct form; display prerequisites, progress and failure recovery; an agent prompt is an optional alternative. |
| G09 · P2 | Guide Compute credential setup | Gateway/target setup offers a safe create/select credential path, explains verification in plain words and preserves explicit consent; invalid/missing references have a working next action; no secret appears in ordinary history. |
| G10 · P2 | Select conversation participants by name | Search/select eligible members and projects without copying IDs/slugs; show the audience before creation; preserve private/group authorization and explain unavailable participants. |
| G11 · P2 | Accept or explain human-readable connection names | A normal display name works, or an identifier is generated separately; invalid values get field-specific guidance before submit; duplicate/client/parent errors are distinguished and entered values survive. |
| G12 · P2 | Add Remove assignment to reserved cards | An authorized person removes a not-started reservation in one clear action plus any required reason; shows who will be able to pick it up; preserves origin release and all non-owner/active-work guards. |
| G13 · P2 | Show one start-and-trust readiness checklist | A card links directly to the missing connection/machine/trust setting; project settings show current permissions even when disconnected; grant/withdraw is explicit, and revoke/unavailable states explain how to recover; phone controls do not just say to use a computer. |
| G14 · P2 | Edit and propose workflow settings in the app | An owner can change supported config values, validate and preview a versioned diff, then propose/accept it; explain first-version and unsupported values; require no pasted API request. |
| G15 · P2 | Add a standalone note in Memory | Memory has an Add note action with project/scope selection, verbatim preview, save result and immediate retrieval; distinguishes workspace notes from bot/conversation-only memory. |
| G16 · P2 | Expose card return dates and goal actions | Card details offers Snooze/Wake and goal/remaining-work actions matching the APIs; display when a card returns, current goal and created follow-up links; enforce the same authorization and preserve existing work. |
| G17 · P2 | Create another workspace from the switcher | The switcher offers Create workspace with name, confirmation and a clear isolated empty result; joining/switching remains separate; no bot or command is required. |
| G18 · P2 | Make common Settings easy to reach | Notifications, connections and workspace navigation have short visible entries; action permissions are searchable/grouped in plain words rather than an expanded wire-name list; unavailable notification setup names the owner action needed. |
| G19 · P2 | Give Coworkers a complete setup and recovery path | Explain the relationship between personal bots, project Coworkers and conversation bots; empty states offer the next action; select learning off/ask/automatic explicitly; missing-model/connection states link to setup; distinguish cancel-response from stopping project work and show the confirmed outcome. |
| G20 · P3 | Add owner workflow recovery and evidence | From Coworker/project status, inspect findings/steps/restore state and safely resume, release, retire or repair eligible work; show the affected run/version, consent and result; no manual fake heartbeat or success stamping. |
| G21 · P3 | Add advanced memory organisation and maintenance | Provide discoverable views/actions for the graph, stored sources/diary and links, with readable scope/history and explicit deletion/recovery semantics; preserve verbatim records and existing access rules. |
| G22 · P3 | Make history chains and handoff reachable in Deck | A person opens a stored thread/chain and requests an authorized handoff from a named app action; shows exactly which history is used, confirmation, destination and failure recovery. |
| G23 · P3 | Show capabilities and operational status in the app | Offer a readable skills/status/cost view with links from Settings, current scope and unavailable reasons; expose existing eligible budget controls; never imply that an activity timestamp proves a live process. |
| G24 · P3 | Manage project presentation and coordinator replacement | An authorized owner edits/clears the project panel and explicitly selects/replaces/clears the coordinator with an effect preview; distinguish this from model selection and token rotation. |

## Verification and follow-through

Browser evidence establishes observations at the pinned revision, including actual
successful UI writes and the blocking failures. Source comparison establishes
the named parity gaps. Documentation obligations, fragment validation and the
public-surface/evaluation guard files are the appropriate narrow checks for this
PR; they do not substitute for fixing or retesting the usability findings.

The next fresh-user E2E should begin with G02/G03/G01, then cover G04–G08, ordinary
card operations, config review and learning, using only UI actions for the user
journey. Exceptional states may be fixture-prepared but must remain visibly
qualified. Connected-machine start/stop, model answers, notification delivery
and native clients need their own scoped integration evidence. The proposed
cards and that E2E remain follow-up work; this audit does not claim the broader
card's implementation acceptance is complete.
