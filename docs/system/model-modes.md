### Model modes: personal, org and hybrid

Card #617 (goal card #611, item 4). Design only: this note adds no code. It builds on the model per role and escalation (#387), the default model and the never-silent rule (#419, `docs/system/role-model-default-and-needs-me.md`), and the cost ledger and budgets (#361, `docs/system/cost-ledger.md`, `docs/system/cost-budgets.md`, chat answers in the same note, card #610). It belongs under **Decisions**. Slice plan and acceptance are at the end; every choice that is not the project owner's is settled here with a default.

**What the owner gets.** Today a workspace has one way to provide models. The owner chooses, per workspace and per project, how it works in plain words with an effect preview:

- **Org.** Runs use the workspace's shared machines and accounts, and the spend counts against the workspace's budgets.
- **Personal.** Each person's runs use that person's own account. Nothing falls back to the workspace's account, so the workspace never pays for a run it did not choose to pay for.
- **Hybrid.** Org is the default; a person may use their own account instead, only where the owner allows it.

## What exists on develop (gap analysis)

Found by reading the code on develop; nothing below is changed by this note.

**Model per role (works, keep).**

- `commonplace/projects/models.py`: `effective(db, workspace, board, role)` returns the model and its source (`project`, `workspace`, `default`, `none`). `_chosen` reads `chat_model_settings` (control database): the board's row wins over the workspace row (`scope='*'`). With no choice the first model of the project list (`project_model_catalog` plus the workspace's rows) is the default. `effective_roles` covers the four slots `main`, `worker`, `reviewer`, `gate` (`projects/types.py`).
- `commonplace/projects/escalation.py`: one rule per project (after N failed rounds or for chosen labels, roles `worker` and `reviewer`), picked from the same list.
- `commonplace/orch/runner_bridge.py`: the start plan resolves the model with `models.effective`, applies escalation, and refuses with `model_unresolved` when no model exists. The job stores `model_provider`, `model`, `model_source`, `model_reason`.
- `commonplace/runner/sessions.py`: a persistent Coworker session is bound to the effective model and fenced as `model_changed` when it moves.
- Settings and display: `GET` project view returns `models`, `models_effective` and `model_catalog` (`projects/routes.py`); `PUT /api/projects/<board>/models` and `POST .../models/catalog` are owner-only and audited; the section "Agent models and escalation" (`web/static/projects.js`, `effectiveWords`) shows "Runs on: <model> (chosen for this project / for the workspace / default)".
- Needs me: one item per project and role when no model resolves (`kind: model_role`, `boards/service.py`, `web/static/needs.js`), owner only, self-clearing.

**Gaps against this card.**

- **G1. There is no mode concept anywhere.** No setting, no column, no word in the resolver. A run knows its model but not whose account serves it or why.
- **G2. No owner-facing writer for workspace-level role models.** The resolver reads a workspace row (`scope='*'`) for `main`, `worker`, `reviewer` and `gate`, but the only writers are the board's `models.put` and the chat front's `set_front` (role `front` only). "Workspace" is a source label with no setting behind it for these roles; a mode setting at workspace level needs the same level for roles to make sense in the settings screen.
- **G3. A provider connection is not a hub object.** The hub holds no provider credentials for runs. The account a run uses is whatever the machine's own agent CLI is signed in to; the hub knows the machine (`runner_machines`: label, `enrolled_by`, attested `provider_instance_id`, worker connection, requesters) and a provider label. The only hub-held key reference is the chat front's `key_ref` (`env:COMMONPLACE_CHAT_KEY_<NAME>`, operator-allowlisted workspaces), which is org-style by nature.
- **G4. Machines belong to the owner.** Enrolling, attesting and changing machines is owner-only (`runner/owner_api.py`, `docs/runner.md`). A member cannot own a machine, so a member has no way to have a personal connection. Card starts use any enabled machine whose grant covers the board and whose requesters include the caller; there is no "private to one person" flag.
- **G5. Chat already behaves personally, cards behave as org.** `chat_relay.online` only offers machines that the poster enrolled (`runner_machines.enrolled_by = poster`), so a chat answer runs on the asker's own machine; card starts run on the workspace's machines. Because only owners enroll machines, a member's chat message finds no runner today (`no_runner`). The existing behaviour is therefore already a mix, and migration must keep it as it is.
- **G6. Runs record no account and no mode.** `runner_jobs` and `run_ledger` carry `requested_by`, `machine_id`, `agent` (the worker connection), `model_provider`, `model`, `model_source`. Chat rows are `source='chat'` with the poster as `requested_by`. Nothing says "org account" or "personal account", and there is no label.
- **G7. Spend has no person or account-kind dimension.** Cost events and sums group by workspace, agent connection, project, board and card; budgets attach to `workspace`, `agent`, `project` or `card` (`runner/budgets.py`, `KINDS`). Everything a machine reports counts toward the workspace sum, whoever pays for the account behind it.
- **G8. The never-silent rule has no personal case.** Needs me, the start refusal and the chat reply know "no model" and "budget exceeded". There is no "you have no connection" item, refusal code or reply, and no person-addressed Needs me item (all current model and budget items are for the owner).
- **G9. The hosted chat front uses an operator key.** `chat/settings.py` `front()` resolves one provider, model and `key_ref` per workspace or board. In personal mode this path must not answer for a person who has no connection, or the workspace's key would pay silently.
- **G10. MCP has no model-setting tool.** `costs_summary` reads spend; budgets are REST-only by design; model settings are REST and settings UI only. Agents can read nothing about which account their next run uses.
- **G11. Account export and deletion need a rule for any new control table.** `account/tables.py` classifies every control table by user, workspace or connection; `tests/test_commonplace_account_tables.py` fails when a table is missing. New tables below must be classified.

## Terms

- **Mode** is how the account behind a run is chosen: `org`, `personal`, `hybrid`, or `unset`. **Account** is the provider login a run is billed to: an org account (a workspace machine) or a person's own account (a connection that person registered). The **model** is still chosen by the role settings above; the mode never changes which model is picked, only whose account serves it.
- **Connection** is a record that a person has an account for a provider on a machine of theirs: provider, a label of their choosing and the machine. It holds no secret: the sign-in stays on the machine, exactly as for workspace machines today. The **label** is free text (at most 60 characters, for example "my work subscription"); a value that looks like a credential is refused without a write, as in the machine attestation rules.
- "Personal mode" is unrelated to a person's personal workspace; user copy says "your own account" to avoid the clash.

## Mode settings and precedence

A **mode record** is `{mode, allow_person_override}` stored for the workspace (`scope='*'`) or for one project (`scope=<board slug>`), in the control database next to `chat_model_settings`. `allow_person_override` is meaningful only when the mode is `hybrid`.

**Precedence, from the top:**

1. The project's record, when it has one. The whole record wins; it is never merged field by field with the workspace's.
2. Otherwise the workspace's record.
3. Otherwise `unset`: no record means today's behaviour, unchanged (see Migration). `unset` is a real answer in every display ("current setup"), never rewritten to `org`.
4. **Person choice.** A person's own account is used instead of the org's only when the effective mode is `hybrid`, the record allows overrides, the person has switched "use my own account" on for this workspace, and the person has a usable connection for the role's provider. In `personal` mode the person's account is not a choice but the rule.

The person's switch is a per-person, per-workspace preference, not per project, so one decision covers every project that allows it. The owner stays in control: a project record with `allow_person_override: false` switches the preference off for that project without deleting it.

**The matrix** (provider of the role's effective model; "org account" means a usable workspace machine for that provider; "own" means the requesting person has a usable connection):

| Effective mode | Overrides allowed | Person switch | Own | Org account | Account a run uses |
|---|---|---|---|---|---|
| `org` | n/a | n/a | any | yes | org |
| `org` | n/a | n/a | any | no | refused `org_account_missing` (owner Needs me, the #419 item) |
| `personal` | n/a | n/a | yes | any | the person's |
| `personal` | n/a | n/a | no | any | refused `connection_missing` (person Needs me); the org account is never used |
| `hybrid` | no | any | any | yes | org |
| `hybrid` | yes | off | any | yes | org |
| `hybrid` | yes | on | yes | any | the person's |
| `hybrid` | yes | on | no | yes | org, and the run says "you have no connection for <provider>, so the workspace account was used" |
| `hybrid` | any | any | any | no | refused: names both missing accounts when the person had no own either |
| `unset` | n/a | n/a | n/a | n/a | today's behaviour: cards on workspace machines, chat on the asker's own machine |

Project over workspace, by example: workspace `personal`, project `org` runs on the org account; workspace `org`, project `hybrid` with overrides allowed lets people use their own account in that project only; a project with no record follows the workspace.

**Who may change it.** Only a signed-in workspace owner (not an agent token, not a machine credential) writes or clears a mode record; the change is audited (`models.mode`). Everyone who can read the project reads the effective mode and who it applies to, because every person must be able to see which account their next run will use. Non-owners who attempt a write get the same 404 the budgets routes give (`docs/system/cost-budgets.md`), so the setting's editability is not exposed.

## Whose account a run uses, and how a run shows it

**Who is "the person".** The requesting person of a start: the person who pressed start, or who posted the chat message. A start with no requesting person (a trigger, or a Coworker-config step) is attributed to the person who accepted the board's Coworker config version; that is always an owner today. If that person has no usable connection in `personal` mode the start is refused like any other (see the failure states). This rule is settled with a default in the open questions.

**Which machine.** Admission picks the machine from the account: in `org` mode only workspace machines (every machine that exists today); in `personal` mode, or when a person's override applies, only machines behind the requesting person's connections for the provider. A machine behind a person's connection is private to that person: it is removed from the workspace pool, never serves another person's start and never serves an org run. The pool a card start may choose from, as today, is also limited by the machine's grant (wings, projects, requesters), so the mode narrows what is allowed and never widens it.

**What the run record holds.** Additive columns on `runner_jobs` and `run_ledger` (the run's row, written in the start's own transaction like the model), NULL on every run that predates them:

| Field | Values | Notes |
|---|---|---|
| `mode` | `org`, `personal`, `hybrid`, `legacy` | the effective mode when the start was admitted; `legacy` for `unset` |
| `mode_source` | `project`, `workspace`, `person`, `legacy` | which record decided; `person` when a hybrid override applied |
| `account_kind` | `org`, `person` | whose account the run is billed to; NULL means a run from before this change, treated as `org` in every sum |
| `account_label` | text | the machine's label for an org account, the person's own label for a connection |
| `account_person` | user id | hub-internal; never returned except to that same person |

The mode, source and account are pinned when the start is admitted, like the model: a later mode change affects new starts only. A queued start is re-checked at lease for its own connection (revoked or offline) and for budgets; it is never re-routed to another account.

**What each reader sees.** The start reply, `card_start_status`, the card panel's Runs list, the run ledger and the project's "Runs on" line show: mode, mode source, model, model source and the account kind. The label is shown for an org account to everyone who can read the run. For a person's own account the label is shown only to that person; the workspace owner and other members see "a personal account" with no label and no name. The same applies to chat answers. No field of any reply, ledger row, audit entry or log line contains a credential, token or key value: the hub never holds one.

**Effective model per role** (extends `models_effective`): each role gets `mode`, `mode_source` and `account: {kind, label|null}` next to `provider`, `model`, `source`, computed for the caller as in the matrix. It reads, for example, "Builder: `<provider>/<model>` (chosen for this project) on your own account (hybrid, your choice)". A caller with nothing usable reads "No account for this role yet" with the reason and a button, never an empty field.

## Spend and budgets per mode

The cost ledger stays one append-only event store. Each event already belongs to a run; the run now carries `account_kind` and `account_person`, so every sum can be split without new events.

- **Org account runs** behave exactly as today: they count in the workspace, agent, project and card sums and against budgets, including the 80% warning and the hard stop.
- **Personal account runs** (personal mode, or a hybrid override) are on an account the workspace does not pay for. They are recorded with their tokens and cost like any run, but they do not count against workspace, agent, project or card budgets and are never stopped by them. They are listed apart in every sum as `personal_runs` with their own totals, so an owner sees that the work happened without the owner's budget being used up by someone else's subscription.
- **Who sees personal amounts.** A person reads their own runs' tokens and cost. The workspace owner sees the number of personal-account runs per scope and the share of work they carry, not the amounts and not whose they are beyond what the card already shows (the person who started it). This is the privacy default; the owner may decide otherwise (open question).
- **Unknown cost** keeps its rule: never zero, never against a limit, counted and listed apart. For a personal run the count appears in the person's own view and in the owner's `personal_runs` count.
- **Budget refusal.** An org-account start over its budget is refused with `budget_exceeded` as today. A personal start is not refused by an org budget (it costs the org nothing). The owner's words in the budgets page say this plainly.
- **Existing rows and budgets.** Rows without `account_kind` count as `org`, so every existing sum and budget keeps its value after the change; no backfill of old runs is needed or made.
- **Chat answers** follow the same rule through their `run_ledger` rows (`source='chat'`); the budget refusal reply is unchanged for org-account chat.

Chat spend and card spend from the same person's own machine are both personal spend: attribution is by the run's account, not by the surface.

## When a person has no connection

Never silent, never the org account. In `personal` mode (or a hybrid refusal where the org had none either):

1. **Inline, at the moment.** The start is refused with `connection_missing`; the reply names the project, the role, the provider and what to do ("Connect your own <provider> account in Settings, or ask the workspace owner to switch this project to hybrid"). For a chat message the person gets a plain reply in the chat (kind `answer`, code `connection_missing`), like `budget_exceeded`, and the message keeps its receipt.
2. **One Needs me item for that person**, per project and provider, with a button that opens their connection setup. It clears itself when the connection exists, and it appears only for the person concerned: the owner's Needs me never lists a member's missing connection. The owner may see an aggregate in the settings preview ("2 people have no connection yet"), with no names.
3. A start never waits silently in `todo`. The card stays as it was, a clear refusal is returned, and one item exists until it is resolved. Repeated refusals for the same person, project and provider reuse the item.

The owner's own #419 item stays the org case: no model and no org account for a role.

## Security

- **A person's connection is private to that person.** Reading, listing and revoking a connection works only for the same signed-in person. The workspace owner, other members, agents and machine credentials get the same 404 for a foreign or missing connection, so existence is not shown. The label is visible only to the person; run records show others "a personal account".
- **Credentials are never in the hub.** A connection record holds a provider, a label and a machine id. A label that looks like a credential is refused without a write. No endpoint, audit entry or log line carries a secret, and no cost event holds message text or keys.
- **Setting the mode is owner-only**, signed-in person only; agents and machines cannot set it. Reading a mode is open to members who can read the project. Writes are audited like budgets (`models.mode`, `models.mode_clear`).
- **A private machine serves one person.** It never takes another person's start, never an org start, and never appears in another person's machine options or in the owner's machine list beyond what the machine registry already shows for machines the owner enrolled; the owner cannot attach a member's connection to a run.
- **The owner cannot read personal accounts' content through the mode.** Personal accounts' provider traffic goes from the person's machine to the provider; the hub sees the run's ledger row, as for any run. Choosing `personal` does not grant the owner any new view of a run; card and wing permissions are unchanged.
- **Uniform refusals.** Every refusal for a hidden object uses one body and status regardless of whether the object exists or belongs to someone else.
- **Account export and deletion.** The new tables are classified in `account/tables.py`: connections and the person's switch by user and workspace, mode records by workspace. Deleting an account removes its connections and clears `account_person` on its runs; the run rows themselves stay as workspace work product.
- **Hosted chat front.** The operator-allowlisted key of the chat front counts as an org account. In `personal` mode it is not used to answer a person; in `hybrid` it is an org fallback; in `org` it is as today.

## Migration

- **Nothing is rewritten.** A workspace with no mode record is `unset`, which behaves exactly as today: card starts on workspace machines, chat on the asker's own machine, budgets over every run. The migration creates tables and nullable columns only; it writes no mode record and backfills no run. Runs without `account_kind` count as `org`.
- **Choosing a mode is a deliberate act.** The first save shows what changes in plain words (see below). Choosing `org` keeps chat as it is: a chat message still goes only to the asking person's own runner, because relaying a member's words to a workspace machine would change the chat privacy rule; a chat message for a member therefore still finds no runner until a later card decides how members' chat reaches a shared machine.
- **Rollback.** Deleting the mode records returns to `unset` and today's behaviour; the added columns are ignored by older code. Existing machines are all workspace machines until a person registers a connection for one that they own.
- **`unset` is not retired by this card.** Whether to ever require an explicit choice is an open question; the default is no.

## The effect preview in settings

The settings screen for the workspace and for each project shows the three modes with one sentence each and, for the selected one, the effect for the current state, in plain words and in Coworker terms, before anything is saved. It is computed by a read-only call with the proposed mode and shows facts, not guesses:

- **Org:** "Every Coworker run uses the workspace's machines and accounts: 3 machines are ready for the Anthropic provider. Spend counts against the workspace budgets. People's own accounts are not used."
- **Personal:** "Each person's runs use their own account and never the workspace's. 4 of 6 people have connected an account; the others see 'Connect your account' and their runs wait until they do. The workspace pays nothing for these runs and its budgets do not count them."
- **Hybrid:** "The workspace account is used unless a person chooses their own. People may switch to their own account: on / off. Their runs then do not count against the workspace budgets."

Every line carries the current numbers (ready machines, people connected, people who would be refused), never names, and says what stays the same. A change that moves chat for members, or makes some runs stop being counted against a budget, is called out as such. The preview never starts a run and never calls a provider. A failed read shows what failed and offers a retry, and the save button stays off until the preview has loaded.

## Limits

- One mode record per scope: one for the workspace and at most one per project, so never more than the workspace has projects.
- At most 5 connections per person per workspace, one per machine (a machine is one person's or the workspace's); a label is at most 60 characters.
- A person's switch is one boolean per workspace.
- The preview reads aggregate counts only and is bounded (it walks the workspace's machines and connections, never run history).
- Mode records, connections and run fields are small rows in the control database and the workspace's runner file; no unbounded text is stored.

## Failure states

Every failure has a reason, who sees it and a next step; none is silent.

| Code | When | Who sees it | Next step |
|---|---|---|---|
| `connection_missing` | personal mode (or a hybrid refusal), the person has no usable connection for the provider | the person: inline and one Needs me item | connect an account, or ask the owner to change the mode |
| `connection_offline` | the person's machine is not reporting; the start is queued, as today | the person, in the run line; chat answers `no_runner` | start the machine; nothing is re-routed to another account |
| `connection_revoked` | the connection or machine was removed after admission | the person | reconnect; the queued start is cancelled with this code at lease |
| `org_account_missing` | org mode (or hybrid fallback) with no model or no usable workspace machine | the owner (the #419 Needs me item); requesters see the refusal | add a model or machine, or choose another mode |
| `override_not_allowed` | a person switches to their own account where the mode does not allow it | that person, in settings | ask the owner to allow it in hybrid mode |
| `model_not_on_account` | the person's provider does not offer the role's model | the person: the run fails with the provider's reason and the model | pick another model for the role, or use another account |
| `escalation_unavailable` | the escalation model is not on the person's account | the person, in the run line (`model_reason`) | the run continues on the role's own model, never silently |
| `no_requesting_person` | a start has no person and no one accepted the config | the owner | accept the config version, or choose `org` |
| `budget_exceeded` | org-account start over its budget (unchanged) | requester | the owner changes the budget |
| `mode_unavailable` | the settings or connection store cannot be read | the caller, plain message and retry | retry; no start is admitted on a guess |

A start fails closed: if the mode cannot be decided, nothing is admitted and the reply says so; it never falls back to another account.

## Slice plan

One PR per slice, each against develop and under 300000 bytes of diff, regression tests red on the base and green on the head, no full suite.

**PR 1: server (mode store, resolver precedence, run record, REST and MCP).**

- Control tables `model_mode_settings(workspace_id, scope, mode, allow_person_override, updated_by, updated_at)`, `model_connections(id, workspace_id, user_id, provider, label, machine_id, created_at, revoked_at)` and `model_person_prefs(workspace_id, user_id, use_own)`, classified in `account/tables.py`; additive columns `mode`, `mode_source`, `account_kind`, `account_label`, `account_person` on `runner_jobs` and `run_ledger`.
- A resolver `commonplace/projects/model_modes.py`, one pure function over a snapshot of the control rows (the matrix), used by the start plan in `orch/runner_bridge.py`, by `runner/chat_relay.py` (`enqueue` and `online`), by `runner/sessions.py` (an `account_changed` fence like `model_changed`) and by the hosted chat front. The machine pool in admission follows the account.
- REST (owner-only write, 404 for others): `GET|PUT|DELETE /api/settings/model-mode` (a `board` key picks one project's record); person routes `GET|POST|DELETE /api/me/model-connections` and `PUT /api/me/model-use-own`; `GET /api/me/model-mode`; extended `models_effective` per role. MCP: a read-only `model_mode` tool that returns the caller's effective mode, source and account kind per role; there is no write tool (agents never change a mode).
- The never-silent rule for `connection_missing`: refusal reply, chat reply and the person's Needs me item; the owner's items unchanged.
- Acceptance:
  1. The precedence matrix above is a parametrized test, one row per case, including project over workspace and the `unset` rows; with no record every existing start, chat answer and ledger value is unchanged (a golden fixture from before the change).
  2. In a fixture workspace the three modes pick the right account and machine for the next start; each run's record shows mode, mode source, model, account kind and label.
  3. A non-owner, an agent token and a machine credential cannot write a mode (the same 404 as an absent route); another person's connection is invisible and unreadable (404, same body as a missing one).
  4. Personal mode with no connection refuses with `connection_missing`, writes one Needs me item for that person only, never starts on the org account, and a chat message gets the plain reply.
  5. No endpoint, audit row, ledger row or log line holds a credential; a credential-looking label is refused.
  6. Account export and deletion include the new tables (the existing table-coverage test passes).

**PR 2: spend attribution and budgets integration.**

- Cost sums gain the `personal_runs` split and per-account-kind totals; budgets and the hard stop count org-account runs only; the budget Needs me and `budget_exceeded` wording say so; owner sees counts of personal runs, a person sees their own amounts (`GET /api/costs`, `costs_summary`).
- Acceptance: (1) a run per mode lands in the right scope: org runs raise the workspace, agent, project and card sums, personal runs raise only the personal split; (2) a budget at 100% refuses an org start and never a personal one; the 80% warning ignores personal spend; (3) legacy rows (no account kind) count as org and every existing budget keeps its value; (4) unknown cost stays unknown for personal runs; (5) the owner's read shows no personal amounts or names, the person's read shows their own; (6) chat answers attribute like card runs.

**PR 3: settings UI with the effect preview.**

- A "Models" section in workspace and project settings (Classic and Deck): the three modes in plain words, the hybrid switch, the effect preview from a read-only preview endpoint, the effective account per role with the mode and source, a person's "Your own accounts" screen (add, label, revoke, the use-my-own switch) and the Needs me row for `connection_missing`. Phone layout included.
- Acceptance: (1) choosing a mode shows its effect, with live counts, before saving, and nothing is written before the confirm; (2) the app says which account the next run uses, per role, and why; (3) a person without a connection sees one clear message and a button, never an empty state; (4) a member sees the mode read-only and no owner controls; (5) every failure state above reads with its reason and next step; (6) screenshots desktop and phone, and the module-wide front-end harness checks that touch the shell all pass.

## Open questions, with the default taken

Needs the project owner (everything else below is routine and settled by its default):

1. **Who may own a machine.** Personal mode only helps members if they can enroll a private machine, which today is owner-only and involves machine grants and wings. *Default: PR 1 ships connections for any machine the person enrolled (owners today); a separate card makes member-owned private machines, and personal mode is announced for members only when it lands. Until then a member in personal mode reads the plain "connect your account" message.*
2. **Do personal runs ever count against org budgets, and may the owner see their amounts?** This is money and privacy policy. *Default: no and no: they are excluded from org budgets and the owner sees counts only; an owner who wants limits on personal use asks people for their own limits (not in this card).*

Routine, default taken:

3. A start with no requesting person (trigger, config step): attributed to whoever accepted the Coworker config version; refused with `no_requesting_person` when none exists.
4. Where the person's account lacks the role's model: the run fails with `model_not_on_account` and the reason; the hub keeps no per-account model list in this card.
5. Escalation model missing on the person's account: the run continues on the role's own model and records `model_reason: escalation_unavailable`.
6. Hosted chat front key in personal mode: not used; the person gets `connection_missing`.
7. The person's hybrid switch is per workspace, default off; the owner's per-project `allow_person_override` can switch it off for one project.
8. Mode applies to new starts only; queued starts keep their pinned mode and are re-checked for their own connection and budgets, never re-routed.
9. `unset` stays a valid state forever; no forced choice.
10. A person's own spending limits and a workspace default connection label are out of scope for these three slices.

## Slice 1 as built

Slice 1 (PR 1) follows the plan above with these settled details.

- **Tables and columns.** Control tables `model_mode_settings`, `model_connections`, `model_person_prefs` and `model_connection_needs` (classified in `account/tables.py`); additive columns `mode`, `mode_source`, `account_kind`, `account_label`, `account_person` on `runner_jobs` and `run_ledger`, added in place when a store opens. Nothing is backfilled: an older run keeps NULL, reads as no mode and counts as org. A machine can have only one active connection, which is what makes it one person's.
- **One resolver.** `projects/model_modes.decide` is the only place the matrix lives. Card starts (manual and config-driven), the chat relay (at queueing and again when a machine leases the message), the persistent board session and the hosted chat front all ask it, and each fails closed with `mode_unavailable` when the rows cannot be read.
- **A config-driven start in personal mode** swaps the config's workspace machine for the requesting person's own machine; a manual start must name the person's own machine (`machine_not_your_account` otherwise). A private machine is `machine not found` for everyone but its person, in every mode, including with no record.
- **Where it shows.** `card_start_status` carries `account` per run (`mode`, `mode_source`, `kind`, `label`; the label is the caller's own or an org machine's, never another person's). The start reply of a manual start keeps its fixed shape. The launch reply of a config-driven start carries the mode and account kind but no label. `GET /api/me/model-mode` and the `model_mode` tool give the caller's own account per role; the project view's `models_effective` carries the same.
- **Chat.** Org mode keeps today's chat rule (see Migration). In personal mode, or a hybrid override, the hosted front's key is not used: the message goes to the person's own runner or they are told why not. The chat ledger row records the mode and account pinned when the machine leases the message.
- **The decision window.** A card start's account is decided before the admission transaction. A concurrent mode change can therefore finish before a start is inserted with the previous decision. The run record shows the account actually pinned; atomic mode changes and card admission remain a follow-up. A queued chat message is checked again when a machine leases it, and a mode change never moves a started run.
- **Needs me during a store failure.** If either the initial missing-connection read or the account decision fails because a mode table is missing or locked, Needs me omits the model-connection group and its count. Other approvals and setup items remain available. A temporary failure does not clear the pending connection item, so it returns after the store can be read again. Start admission still fails closed.
- **Existing run fields.** The card-run contract retains `run_id`, `cancel_requested_at`, `stop_status`, `stop_code` and `stop_confirmed_at`: `jobs.status` already returned these before this slice. The contract test now names the existing fields as well as the new `account` field; no cancellation or stop behaviour changes here.
- **Board sessions** run for the person who accepted the config and never on another account than the mode gives that person; a session whose account no longer fits is fenced with the refusal's code.
- **Open for slice 2 and 3.** Spend attribution and budgets (PR 2) read `account_kind`; the settings screen and effect preview (PR 3) are not in this slice; member-owned private machines remain a later card.
