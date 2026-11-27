# Web app usability audit: owner and member flows (card #372, slice 1)

Scope: can a person who only has the browser do everything an agent can do through MCP or the API?
This is a read-only audit of the Commonplace web app. No product code changed. Visual redesign
is out of scope; gaps are reported, not restyled.

## Method

- A fresh, empty, throwaway instance built from this revision (the same instance helper the
  [fresh-user release gate](commonplace-release-gate.md) uses: captured mail, no real providers, no
  stored data). Accounts use the reserved `example.invalid` domain. No production or real user data.
- A headless Chromium drove every step as the person would: sign-up, settings, cards, project
  settings, Today. One screenshot per step that matters, in `docs/usability-audit-372/`.
- **Fixtures:** a person cannot make an agent act, so the other side was played over MCP with
  tokens the owner created in the browser (an untrusted agent that adds cards, a Coworker that
  proposes configs and asks questions). Those calls only created things to look at. Every
  action *by the person* was done in the UI, never through MCP or the API.
- Clicks count interactions after the page is open (clicks, selects, typed fields as one, file picks); a
  scroll is noted separately. "Screens" counts page or panel changes.
- Not exercised: starting an agent run (needs a registered machine, none can exist in a throwaway
  instance), real social sign-in, real mail, phone push. They are marked below.

## Verdict in one paragraph

Almost every owner action has a control, including the four the card asked about: config
accept/reject with a diff, release, drop a reservation and set priority. What fails is
discoverability and feedback. The controls sit in places nothing points to (project settings
reached from Settings, a collapsed "Edit card" section), use different words from the agent
side (assignment vs reservation, Approve vs release, Todo vs Ready), and the things an owner
must act on (a proposed config, a card waiting for release) do not reliably appear in Today. A
first-time member gets the sign-in page of a brand-new workspace instead of an invitation. The
ranked gaps are at the end.

## Current state after the fixes (27 Nov 2026)

The tables in "Flows" below describe what the audit found. This table says where each flow stands on
develop now. Numbers after a hash are pull requests; "open" means not merged yet. The flows marked
**walked** are driven end to end, as a brand-new owner with nothing but a browser, by
the fresh-user browser walk test, named in the tests module docs (sign-up, project, card create, edit, prioritise and move,
config accept from Today, release from the card, drop reservation, trust in Admin, Coworker stop and
resume, memory import to Preview). The test makes no API, MCP or bot call while it walks; a seed
plays the agents beforehand (see its docstring).

| Flow | Now | Pull requests | Still missing |
|---|---|---|---|
| 1. Sign-up | partial, walked | none | The sign-in page still promises three steps, then the app lands on a board with no "what now" prompt |
| 2. Workspace and project setup | partial, walked | #793, #810 (names), #814 open (invite landing) | The invite landing waits for #814; "Ready to start?" still lists model, key and machine as "Needs you" for a plain board |
| 3. Cards: create, edit, prioritise, move | partial, walked | #810 (Move lists every status), #823 (lanes follow the card) | Priority is only in "Edit card" with no marker on the board; #775 open |
| 4. Releasing restricted-origin cards | yes, walked | #804 | none |
| 5. Dropping a reservation | yes, walked | #818 | Handing a card to a chosen agent is not walked |
| 6. Coworker configs | yes, walked | #803, #819 | none |
| 7. Giving trust | partial, walked | #811 (Admin explains and changes it), #804 (creator and trust on the card) | The start-permission form still cannot be finished in a workspace with no machine |
| 8. Starting and stopping agents | partial, walked | #806 (Stop and Resume) | Starting a run needs a registered machine and is not walked; void, reassign and retire are not offered |
| 9. Agent limits | partial, walked | #816 | No control for the workspace-wide cap |
| 10. Needs-me items | yes | #803, #804, #819, #825 | none |
| 11. Tokens and connections | partial | #807 (memory question first) | A member cannot connect their own agent; not walked |
| 12. Notifications | partial | #822 (owner copy, opt-in email) | Phone push needs a configured server; not walked |
| 13. Memory import | yes, walked to Preview | #812 | The final Import click is not walked; see the dialog race below |
| 14. Member view | no change | none | Members still get no reason for a missing control and no way to connect an agent; not walked |

### Still open

- First-run "what now" prompt after sign-up (flow 1).
- Invite landing shows the workspace and inviter: #814 open.
- Visible priority marker and picker on the card face: #775 open.
- Member explanations for owner-only controls and a decision on member tokens (flows 11 and 14).
- Workspace-wide agent cap control (flow 9).
- Start-permission form with no machine, and starting a run without a registered machine (flows 7 and 8).
- Void, reassign and retire from the web app (flow 8).
- Phone push on a server that has it configured (flow 12).
- Found by the walk: the memory import dialog loads its destination rules after it opens and then
  redraws, which resets a format picked in the first moments; the person then previews with the
  default destination. The walk waits for the rules, so it does not hide the bug.

## Flows

Legend for "Possible in the UI": **yes** = completes with no API or bot; **partial** = completes
but is hard to find, hidden, or incomplete; **no** = only through MCP or the API.

### 1. Sign-up

| Step | Possible | Clicks / screens | Where a user gets stuck | Mirrors |
|---|---|---|---|---|
| Open the app, type an email, request a link | yes | 2 / 1 | Page says "Name your first project", but a project already exists after sign-in | `POST /app/auth/email` |
| Open the mailed link, confirm | yes | 1 / 1 (confirm page) | Link alone does not sign in; the extra "Sign in" button is correct but unexplained | `POST /app/auth/email/verify` |
| Land in the app | yes | 0 / 1 | Lands on a board named after the default project; no "what now" prompt. The sign-in page promised three steps (name a project, add a card, connect an agent) and none appears in the app | `GET /api/me` |

Total: 3 clicks, 3 screens, then a board. Screenshots: [sign-in](usability-audit-372/01-sign-in.png),
[first board](usability-audit-372/02-first-board.png).

### 2. Workspace and project setup

| Step | Possible | Clicks / screens | Where a user gets stuck | Mirrors |
|---|---|---|---|---|
| Workspace | yes (automatic) | 0 | Named after the email address; renaming is not offered; "Switch workspace" lives in Settings | `GET /api/me` |
| First project | yes (automatic) | 0 | It is called a project, a bot and a board in different places; the sidebar lists "Bots", settings say "Projects", the board says "Board" | `board_add`, `space_add` |
| New project | partial | 3 / 1 (Bots, New bot, fill, Create bot) | Creates a "bot"; nothing says it is also a project with its own board and memory ([screenshot](usability-audit-372/30-new-bot.png)) | `board_add` |
| Project readiness | partial | 3 / 2 | "Ready to start?" lists five items, three read "Needs you" (model, key, machine) even when the person only wants a board ([screenshot](usability-audit-372/11-project-settings.png)) | Coworker inbox route |
| Invite a member | yes | 3 / 1 (Admin, email, Make invite link) | Link is shown once | invites API |
| Member opens the invite | partial | 4 / 3 | The invite lands on the generic sign-in page of a new workspace ([screenshot](usability-audit-372/21-invite-landing.png)), then a "Join workspace" step ([screenshot](usability-audit-372/22-join.png)); nothing tells the invitee which workspace they are about to join until the third screen | invite accept route |

### 3. Creating, editing, prioritising and moving cards

| Step | Possible | Clicks / screens | Where a user gets stuck | Mirrors |
|---|---|---|---|---|
| Create a card | yes | 3 / 1 (Add card, title, Add card) | Form includes Who, Label and Priority, so priority can be chosen at creation ([screenshot](usability-audit-372/03-add-card-form.png)). "Me (no run starts)" lands the card in the Active lane as "Ready" while its status is Todo | `task_add` |
| Open a card | yes | 1 / 1 | Opens a side panel; the status line says "Ready", "Needs you" or "Change", which are lane words, not statuses ([screenshot](usability-audit-372/04-card-panel.png)) | `task_show` |
| Edit title and details | yes | 3 / 1 (Edit card, Edit, Save) | "Edit card" is collapsed by default, and "Edit title" is also in the Move menu | `task_update` |
| Change priority | partial | 4 / 1 (Edit card, select, Set) | Priority is only in the collapsed "Edit card" section ([screenshot](usability-audit-372/05-edit-card-priority.png)). After setting Urgent, nothing on the card or board shows it. A member sees no priority control and no reason why | `task_update priority` |
| Move a card | partial | 2 / 1 (Move to…, lane) | The menu offers Active, In review, Shipped only; Backlog, Todo, Doing, Review, Done are in a select under "Edit card" with a separate Move button. Shipped is greyed with "Move it to Active first." ([screenshot](usability-audit-372/06-move-menu.png)) | `task_update status` |
| Hold, snooze, goal, blockers | yes | 1-3 | Hold appeared on a card waiting for release; Snooze is a collapsed disclosure | `task_snooze`, `card_goal` |
| Board refresh after a change | partial | 0 | After releasing a card the open panel updated but the lane behind it still showed the old column until a reload ([screenshot](usability-audit-372/09-after-release.png)) | live sync |

### 4. Releasing restricted-origin cards

A card an untrusted agent created waits in backlog until a person releases it.

| Step | Possible | Clicks / screens | Where a user gets stuck | Mirrors |
|---|---|---|---|---|
| Find the card | partial | 1-2 / 1 | It appears in "Needs you" with the badge "Release" before any Coworker is connected ([screenshot](usability-audit-372/07-restricted-card.png)). Once a Coworker is connected, Today listed only the Coworker's question; the waiting card sat in a Backlog list at the bottom of the board | `needs_me`, `task_list` |
| Release from the card | partial | 3 + type + scroll / 1 (open card, Approve, reason, Release) | The word is Approve on the button, Release on the form, and "origin" in the docs. Approve opens the "Edit card" section above the footer and the reason field lands below the visible area ([screenshot](usability-audit-372/08-approve-release-form.png)); the person must scroll up to find it | `task_approve` |
| Release from project settings | yes | 4 / 2 (Settings, project button, reason, Release) | A "Waiting for release" list with a reason and Release per card, but only if the person knows to look there | `task_approve` |
| After release | yes | 0 | Card becomes Ready, the Coworker row appears. No confirmation message | |

### 5. Dropping a reservation (#358)

| Step | Possible | Clicks / screens | Where a user gets stuck | Mirrors |
|---|---|---|---|---|
| See that a card is reserved | partial | 1 / 1 | There is no marker on the card face. Only the "Coworker" row in the panel names the holder | `task_show` |
| Drop it | yes | 3 / 2 (open card, Remove assignment, confirm) | Called **Remove assignment** here and "reservation" everywhere else; the confirm dialog says the card "goes back to the pool" and takes an optional reason ([screenshot](usability-audit-372/10-remove-assignment.png)). The confirm click was not submitted in this run | status update todo to backlog, owner drop |
| Reserve to a second agent | no (agent side only) | n/a | A handoff to another connection was refused until that connection exists and is active; the person has no way to hand a card to a specific agent from the card except the "Hand off" button, which was not exercised | `task_handoff` |

<a id="6-accepting-and-rejecting-orchestrator-configs"></a>

### 6. Accepting and rejecting Coworker configs

| Step | Possible | Clicks / screens | Where a user gets stuck | Mirrors |
|---|---|---|---|---|
| Learn that a proposal exists | yes | 0 | Since #662 a proposed config is an item in Waiting on you, counted in the sidebar and Today badge ([screenshot](usability-audit-372/31-today-config-after.png); before: [screenshot](usability-audit-372/30-today-config-before.png)) | `orch_config_get` |
| Find the list | yes | 1 / 1 (Waiting on you) | The proposal is the first item in Waiting on you. Project settings keeps "Coworker config versions" as the history view | `GET /api/orch/boards/<board>/configs` |
| Read the change | yes | 1 / 1 (Changes) | The item names the proposer ("<project> bot", not the raw `conn:Coworker:<project>`), says the change in one line (for example "Agent limit: 3 to 5") and expands to the diff | `orch_config_diff` |
| Accept | yes | 1 / 1 | One click on the item, with the effect in words above it ("Accept means yes: <project> uses v4 from now on") | `orch_config_accept` |
| Reject | yes | 2 / 1 | Reject asks once with the project settings wording, then Reject version ([screenshot](usability-audit-372/15-config-reject-confirm.png)) | `orch_config_reject` |

Total to accept: 4 interactions plus one scroll; nothing leads there.

### 7. Giving trust

| Step | Possible | Clicks / screens | Where a user gets stuck | Mirrors |
|---|---|---|---|---|
| Turn on Coworker-managed starts | yes | 2 / 1 (Settings, checkbox) | The workspace switch is off by default and described in a long sentence under Workspace; the trust form does not exist until it is on | Coworker setting |
| Trust a Coworker to start work | partial | 5 / 2 | Form sits in a collapsed "Coworker start permissions" disclosure; needs "select machines" and a confirm box, but a new workspace has no machine, so the form cannot be completed ([screenshot](usability-audit-372/16-coworker-status.png)) | `POST /api/projects/<slug>/orchestrator/trust` |
| See or change a connection's trust level | partial | 2 / 1 | Admin lists each connection as `untrusted` or `standard`; there is no control to change it. The two agents in the run were `untrusted` and `standard` with no explanation of what that allows ([screenshot](usability-audit-372/20-admin.png)) | token trust level |
| Trust on the card | **no** | n/a | The card shows no trust information about its creator, so the person cannot see why a release is needed | `task_show` provenance |

### 8. Starting and stopping agents

| Step | Possible | Clicks / screens | Where a user gets stuck | Mirrors |
|---|---|---|---|---|
| Start an agent on a card | not exercised (needs a machine) | n/a | The card's Runs section says "No machine is ready to run an agent yet" and links to Cloud workspaces ([screenshot](usability-audit-372/27-card-runs-no-machine.png)). In the source, "Start agent" appears only when a machine and config allow it | `orch_card_start`, `card_start` |
| Cancel a queued start | yes (source) | 1 | Cancel exists only while a job is queued, leased or starting | `card_start_cancel` |
| Stop or resume a Coworker session | **no** | n/a | The API has stop and resume; the UI has neither. The project status block says voiding a step, reassigning it and retiring an experiment "are not offered on this page yet" | `orch_resume`, stop route |
| Disconnect an agent | yes | 3 / 2 (project settings, Disconnect…, Disconnect everywhere) | The dialog is clear that the revoke is workspace-wide ([screenshot](usability-audit-372/25-disconnect.png)) | token revoke |

### 9. Agent limits

| Step | Possible | Clicks / screens | Where a user gets stuck | Mirrors |
|---|---|---|---|---|
| Set the project cap | yes | 3 / 2 (Settings, project button, +/−) | "Max agents working at once" stepper in project settings, "No limit" by default ([screenshot](usability-audit-372/17-agent-limit.png)) | project limit |
| See the effective limit | partial | 2 / 1 | Admin has a read-only "Agent limits" table. With version 1 accepted (it set a limit of 2), the table still read "Accepted config —" and "Unlimited", while project settings said "Accepted config: version 1". Either the table ignores configs without a runner section or the read is stale; the card should say which | `GET` project limits |
| Workspace-wide cap | no | n/a | Admin reads "Workspace concurrency cap: not configured"; no control | config |

### 10. Needs-me items

| Step | Possible | Clicks / screens | Where a user gets stuck | Mirrors |
|---|---|---|---|---|
| See what waits | yes | 1 / 1 (Today) | Works well: one list, filters, keyboard hints ([screenshot](usability-audit-372/18-needs-me.png)) | `needs_me` |
| Answer a decision | yes | 1-2 / 1 | Option buttons, optional note, "Open card", "Ask a question". Nothing to fix here | `decide` |
| Config proposals, release waits | **no / partial** | n/a | Not listed, see flows 4 and 6 | `needs_me` |

### 11. Tokens and agent connections

| Step | Possible | Clicks / screens | Where a user gets stuck | Mirrors |
|---|---|---|---|---|
| Create a connection | yes | 3 / 2 (Settings, name and client, Create token) | Whole page is one long scroll; the "Connections" tab only scrolls. The form has long paragraphs on import and answering permissions; "Restrict access or set a parent" is collapsed | `commonplace token add` |
| Copy the secret and setup | yes | 2 | Token, URL, header and a config file snippet, each with a Copy button, shown once. The snippet shown is a config file example; a client select beside it switches the format (not exercised) | |
| Connect the project Coworker | partial | 4 / 2 (Settings, project button, Connect agent, memory choice) | First click fails with "Could not connect the agent. This project uses shared memory…" and opens a choice dialog; the person must pick between "own memory section" and "all shared memory" with no recommendation ([screenshot](usability-audit-372/12-connect-agent-memory-dialog.png)) | `board_own_wing` |
| A member connects their own agent | **no** | n/a | The member sees "Could not load Agents: Owner access required" in place of the form ([screenshot](usability-audit-372/24-member-settings.png)) | token add |

### 12. Notifications

| Step | Possible | Clicks / screens | Where a user gets stuck | Mirrors |
|---|---|---|---|---|
| Phone notifications | no | n/a | The owner is told "Ask the workspace owner to connect a notification service" ([screenshot](usability-audit-372/26-settings-notifications.png)); there is nothing to enable | push settings |
| Webhooks | partial | not counted | A form with URL, project, "Waiting on" and an event filter exists under Workspace; not exercised past the form | `webhook_add` |
| In-app | partial | 0 | Today badge and sidebar count only. Config proposals are counted since #662; no count yet for release waits (flow 4) | |

### 13. Memory import

| Step | Possible | Clicks / screens | Where a user gets stuck | Mirrors |
|---|---|---|---|---|
| Open the import dialog | yes | 2 / 1 (Memory, Import memory files) | | import route |
| Import a text file with the defaults | **fails** | 4 / 2 | The default "Save into" is Workspace conversations; a `.md` file is refused only after Preview with "Memory text files require your private imported history destination" ([screenshot](usability-audit-372/28-import-wrong-destination.png)) | import route |
| Import with the right destination | yes | 6 / 2 (source, destination, file, Preview, consent box, Import) | Preview shows count, size and "No AI model reads it" ([screenshot](usability-audit-372/29-import-preview.png)). The final Import click was not submitted | import route |

### 14. Member view of the same flows

A member sees the board, Today and cards, can move cards and add blockers, and cannot see
priority, release, trust or settings controls. Nothing says why a control is missing; Settings
shows "Only the owner can change these" at the top and an error where Connected apps would be
([card](usability-audit-372/23-member-card.png)).

## Ranked gaps (proposed follow-up cards)

Priorities: `urgent` blocks a person from finishing a core task; `high` needs the API today; `normal`
works but is hard to find or understand; `low` polish.

### 1. Config proposals appear in Today with Accept, Reject and a plain-words diff

- Priority: high
- Problem: A proposed Coworker config never appears in Today or on a card; the owner finds it only
  in project settings, four interactions deep. Proposer is a raw identifier; accepting gives no
  summary of effect.
- Fix: List each proposed config in Today as an approval (proposer by name, one-line summary of
  changed values such as the agent limit, "Changes" expanded, Accept and Reject with the existing
  confirm). Count it in the Today badge and sidebar. Keep project settings as the history view.
- Done when: a fresh owner whose Coworker proposes a config sees it in Today without opening
  settings, accepts or rejects it in at most two clicks, and the audit's flow 6 shows `yes`.

### 2. Release and trust on the card, in view

- Priority: high
- Problem: Approve on a waiting card opens a form inside a collapsed section, with the reason
  field below the visible area. After a Coworker connects, waiting cards leave Today and sit in a
  Backlog list. Trust of the card's creator is not shown.
- Fix: Show a "Waiting for release" block at the top of the card with who created it, its trust
  level, a reason field and one Release button; keep Hold. Show these cards in Today at all times
  for the owner. Use one word (Release) on button, form and board.
- Done when: opening a restricted-origin card shows the release form without scrolling, release
  takes two clicks and a reason, and the card is in Today whether or not a Coworker is connected.

### 3. Drop reservation under its own name, with the holder shown on the card

- Priority: normal
- Problem: The control is called Remove assignment and is easy to miss; the card face does not show
  that the card is reserved or for whom.
- Fix: Show a reserved-for marker on card face and header; name the action Drop reservation
  (matching the docs and API) and keep the confirm with optional reason.
- Done when: a reserved card shows who holds it on the board, and the owner drops the reservation
  from the card in two clicks (control, confirm).

### 4. Priority picker on the card face

- Priority: normal
- Problem: Priority is only in the collapsed "Edit card" section; the board and card show none, and
  members get no explanation.
- Fix: A visible priority control in the card header for owners; a priority marker on the board card
  (and in lane order, if that is how priority works); read-only display for members.
- Done when: owner sets priority in two interactions from the open card, the marker shows on board
  and card, and a member sees the value without the control.

### 5. Memory import defaults to a destination the chosen file type accepts

- Priority: normal
- Problem: A `.md` or `.txt` import fails after Preview because the default destination is not
  allowed for that type; the person has to start over.
- Fix: Choose the destination from the file type (or disable the incompatible option with a reason)
  before Preview.
- Done when: importing a text file with default choices reaches the Preview step, and the error
  appears before a file is chosen if a combination cannot work.

### 6. Invitee lands on an invitation, not on first-run copy

- Priority: normal
- Problem: An invite opens the generic sign-in page whose side panel says "Name your first project";
  the workspace and inviter appear only on a later Join page.
- Fix: Show the workspace name and inviter on the first invite screen; skip first-project copy for
  invitees. Also make the first screen after sign-up carry the three promised steps.
- Done when: opening an invite link shows who invited the person to what before any form.

### 7. One vocabulary for project, bot, Coworker, agent, board, and for statuses

- Priority: normal
- Problem: Sidebar says Bots, settings say Projects, boards say Board. Lanes say Needs you, Active,
  In review, Shipped; the card says Ready; the status select says Todo, Doing; the Move menu hides
  half the statuses.
- Fix: Pick one name per concept; show the real status next to the lane word; make the Move menu
  list every status the person may use.
- Done when: a glossary of the nouns in the app matches the MCP tool and doc names, and Move
  reaches every status without opening Edit card.

### 8. Connect an agent without a failed first attempt

- Priority: normal
- Problem: Connecting the project Coworker first shows an error ("This project uses shared memory")
  and then a two-way choice with no recommendation.
- Fix: Ask the memory question first with a default that is safe for a new project, with no error
  state.
- Done when: Connect agent on a new project reaches the token screen in two choices, no error text.

### 9. Stop and resume Coworker sessions from the web app

- Priority: normal
- Problem: Stop and resume exist only through the API; voiding a step, reassigning it and retiring
  an experiment are not offered at all.
- Fix: Stop and Resume on the project's Coworker row (with confirm), then void, reassign and retire
  as follow-ups. Show a failed-to-start card start with its reason.
- Done when: the owner stops and resumes a Coworker session from the project settings page; each
  action shows in card history.

### 10. Per-connection trust is visible and changeable

- Priority: normal
- Problem: Trust level of each connection is shown in Admin with no explanation and no control; the
  start-permission form cannot be completed in a workspace with no machine.
- Fix: Explain each level in words next to it; let the owner change it, or say why not; show the
  trust form's blocker (no machine) with a link instead of an unfinishable form.
- Done when: the owner can read what `untrusted` and `standard` allow and change a connection's
  level, or sees exactly what blocks it.

### 11. Agent limits show the effective value, from one place

- Priority: low (investigate first)
- Problem: Admin's table read "Accepted config —" and "Unlimited" while project settings showed
  version 1 accepted with a limit of 2. Limits are set in three places (config, project stepper,
  workspace cap not editable).
- Fix: Check whether the table ignores configs without a runner section, fix or explain. Show
  configured, config and effective limit side by side in project settings.
- Done when: the number the table shows matches the one an accepted config and the stepper imply,
  with a test.

### 12. Notifications that work for the owner

- Priority: low
- Problem: Phone notifications tell the owner to ask the owner. Nothing alerts the person to new
  Today items outside the app.
- Fix: For owners, show how to enable phone push, or a plain "not available on this server" line;
  offer email for Today items.
- Done when: the owner never sees an instruction to ask themselves, and a new Today item can reach
  them outside the app.

### 13. Members see why controls are missing, and can connect their own agent

- Priority: low
- Problem: Members lose controls with no explanation, and see "Owner access required" in place of
  the connections form.
- Fix: Replace missing controls with a one-line "Only the owner can change this" and decide whether
  members may create their own tokens.
- Done when: every owner-only control a member would look for either shows a reason or is absent
  by design in a documented list.

### 14. Board freshness and narrow-width layout

- Priority: low
- Problem: A released card's lane did not update behind the open panel until reload; at a 1280-pixel
  window the board header squeezed to one word per line when a side dialog was open
  ([screenshot](usability-audit-372/10-remove-assignment.png)).
- Fix: Refresh the lane when a card action completes; keep the header stable with a side dialog.
- Done when: a released card changes lane with no reload, and the header keeps its layout with a
  dialog open.

## Not covered

Real social providers, real mail and phone push; starting an agent run (needs a registered
machine); the confirm click of drop reservation and Import (dialogs verified, not submitted);
Hand off; the Cloud workspaces and Compute screens beyond their landing pages; mobile layouts.
The audit used a 1280 by 800 window.

## Reproduce

Start a throwaway instance with the helper used by `scripts/commonplace_release.py`
(`throwaway_instance` in `scripts/commonplace_release_support.py`), sign up through the email form
with a captured link, and drive it with Playwright. Create connection tokens from Settings and
use them only to create fixtures. See [the release gate](commonplace-release-gate.md) for the
browser setup. Nothing in this audit needs a production instance.
