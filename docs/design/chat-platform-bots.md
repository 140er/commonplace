# Chat-platform bots

Card #455 (one card for chat-platform presence; builds on the Bots layer, `docs/bots.md`).
Status: design note; slices 1 (foundation), 2 (identity), 3 (intake and turns) and 4 (confirmation) are built, the rest is not. Slack is the first platform; Teams and Discord follow
through the same adapter interface.

## Problem and goal

- People already live in a chat platform. Today they relay between it and Commonplace by hand,
  or through some other assistant.
- Goal: a person talks to their Commonplace bots from a chat platform, in a direct message or
  in a channel the owner mapped, and gets threaded replies that act on real work.
- The bot never has more reach than the person asking; in a shared channel it has even less.
- Nothing is on by default: an owner installs, tests, maps and enables, and each person links
  their own account.
- Platforms are one adapter interface. The first implementation is Slack; the platform
  vocabulary stays inside the adapter, so Teams and Discord add adapters, not new rules.

## Decisions

- **One Commonplace turn engine.** A chat message becomes a message to the asker's own bot
  (their project bot for a mapped channel, their General bot for a direct message). The
  Bots-layer turn engine, tools, limits, verbatim rule, confirm chips, redaction and model
  budget all apply unchanged. The bridge only adds intake, identity, a surface policy and
  delivery. A bridge adds no second place where model work happens.
- **The bot is not a principal there either.** It posts as the platform app identity. It holds
  no Commonplace token. Every Commonplace action runs as the linked person
  (`sessions.person_principal`), re-resolved on every event, exactly as in the web app.
- **A platform identity is not a person until the person links it.** Display names, e-mail
  addresses and workspace-admin status on the platform grant nothing. Only an explicit link
  made from a signed-in web session counts (section "Identity and linking").
- **Strict surface policy.** Bots in the web app treat some writes as routine (filing the
  person's own words). In a chat platform nothing writes with the person's identity without
  their explicit tap (section "Confirmation"), and a channel turn is narrower than a web turn
  (section "Scope in channels").
- **Install = one platform team (or tenant, or server) bound to one workspace.** A platform team
  can be installed into at most one workspace on a hub at a time. Events name the team; an
  event for a team with no install, or a disabled one, does nothing.
- **No conversation text outside the workspace stores.** Threads stay in `bots.sqlite3`; bridge
  tables hold references and platform ids. The control database holds counters and audit rows
  without text, as the Bots layer does.
- **Third-party code.** None is assumed. Should any be adopted, it keeps its licence header, the
  repository `NOTICE` gets a line, and no third-party name or logo appears in product text.

## Data model

New workspace file `chat_bridge.sqlite3`, opened like `bots.sqlite3` (encrypted with the
workspace when workspace encryption is on). Ids are `uuid4().hex`; times are UTC
`YYYY-MM-DDTHH:MM:SSZ`; platform ids are opaque text and always the platform's immutable ids,
never names.

```sql
CREATE TABLE bridge_installs (
  id           TEXT PRIMARY KEY,          -- also the unguessable part of the intake URL
  platform     TEXT NOT NULL,             -- adapter name: 'slack', later 'teams', 'discord'
  ext_team     TEXT NOT NULL,             -- the platform's team / tenant / server id
  label        TEXT NOT NULL,             -- owner-visible name, owner-typed, never trusted
  secrets      BLOB NOT NULL,             -- sealed (below); write-only, never read back by any API
  enabled      INTEGER NOT NULL DEFAULT 0,-- default off; an install that never passed `test` cannot be enabled
  tested_at    TEXT,                      -- last successful adapter test
  daily_cap    INTEGER NOT NULL DEFAULT 200, -- bridge turns per day, whole install
  installed_by TEXT NOT NULL,             -- owner user id
  created_at   TEXT NOT NULL, disabled_at TEXT,
  UNIQUE (platform, ext_team)
);
CREATE TABLE bridge_channels (            -- explicit owner mapping; a DM needs none
  install_id TEXT NOT NULL REFERENCES bridge_installs(id),
  ext_channel TEXT NOT NULL,
  board       TEXT NOT NULL,              -- the one project this channel may talk about
  disclosure  TEXT NOT NULL DEFAULT 'titles' CHECK (disclosure IN ('titles','full')),
  enabled     INTEGER NOT NULL DEFAULT 1,
  mapped_by   TEXT NOT NULL, mapped_at TEXT NOT NULL,
  PRIMARY KEY (install_id, ext_channel)
);
CREATE TABLE bridge_links (               -- a platform identity bound to a person
  id         TEXT PRIMARY KEY,
  install_id TEXT NOT NULL REFERENCES bridge_installs(id),
  ext_user   TEXT NOT NULL,
  user_id    TEXT NOT NULL,
  confirm_ok INTEGER NOT NULL DEFAULT 0,  -- person's separate opt-in to confirm from the platform
  created_at TEXT NOT NULL, revoked_at TEXT,
  UNIQUE (install_id, ext_user), UNIQUE (install_id, user_id)
);
CREATE TABLE bridge_link_codes (          -- shown once in the web app; only the hash is stored
  code_hash TEXT PRIMARY KEY, install_id TEXT NOT NULL, user_id TEXT NOT NULL,
  expires_at TEXT NOT NULL, used_at TEXT, tries INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE bridge_events (              -- intake journal and idempotency: one row per platform event
  install_id TEXT NOT NULL, ext_event TEXT NOT NULL,
  state      TEXT NOT NULL,               -- received | running | replied | refused | failed:<code> | ignored
  user_id    TEXT, bot_id TEXT, message_id INTEGER,  -- the stored bots.sqlite3 user message
  ext_channel TEXT, ext_thread TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (install_id, ext_event)
);
CREATE TABLE bridge_outbox (              -- every post the bridge makes; nothing is posted without a row
  id TEXT PRIMARY KEY, install_id TEXT NOT NULL,
  ext_event TEXT NOT NULL, part INTEGER NOT NULL,       -- reply part (long replies are split)
  target TEXT NOT NULL,                   -- 'thread' | 'private' (ephemeral or DM)
  ref TEXT NOT NULL,                      -- reference to the stored reply or pending id, not a copy of text
  state TEXT NOT NULL,                    -- queued | sending | sent | unknown | cancelled
  ext_post TEXT, attempts INTEGER NOT NULL DEFAULT 0, next_at TEXT,
  UNIQUE (install_id, ext_event, part, target)
);
CREATE TABLE bridge_prompts (             -- one row per posted confirm button
  pending TEXT PRIMARY KEY,               -- bot_pending.id
  install_id TEXT NOT NULL, ext_user TEXT NOT NULL, ext_channel TEXT NOT NULL, ext_post TEXT
);
```

**Sealed secrets.** `secrets` holds the platform credentials (bot token, signing secret or
public key, and any app id), as one authenticated-encryption blob with the install id as
associated data. The sealing key is a per-workspace key from the hub's existing key store, not
the repository, the environment of agents, or the control database. It is write-only: no route
and no MCP tool returns it, and no log line, audit row or error string carries it or any part of
it. Rotating means writing a new blob. If the key store cannot serve a workspace (for example
encryption keys not available), the install is refused rather than stored in the clear.

**Who may read or write what.**

| Data | Owner | Linked person | Anyone else, agents, tokens |
|---|---|---|---|
| install row (without `secrets`), channel mapping, link list (ext id, person, times; no text) | read, write | own link only | none (404) |
| `secrets` | write only | none | none |
| `bridge_links` row | revoke any | create (via code), revoke own | none |
| `bridge_events`, `bridge_outbox`, `bridge_prompts` | none (no text, internal) | none | none |
| conversation text | none | own thread via the Bots routes | none |

The owner sees that a person is linked, never what that person said. Owners are not special
readers of anyone's bot (Bots layer rule).

## Surface

Only names here.

**Intake (public, no session)**

- `POST /api/bridge/in/<install_id>/events`: events, mentions, direct messages, one URL per install.
- `POST /api/bridge/in/<install_id>/interact`: button taps, commands.
- `GET /api/bridge/in/<install_id>/oauth`: optional one-click install callback (adapter-provided).

**Owner (signed-in owner, same-origin writes)**

- `GET`/`POST /api/bridge/installs`, `PATCH`/`DELETE /api/bridge/installs/<id>` (`enabled`, `label`,
  `daily_cap`, new `secrets`), `POST /api/bridge/installs/<id>/test`.
- `GET`/`PUT`/`DELETE /api/bridge/installs/<id>/channels/<ext_channel>`, `GET /api/bridge/installs/<id>/links`,
  `DELETE /api/bridge/installs/<id>/links/<link>`.

**Person (signed-in member)**

- `POST /api/bridge/installs/<id>/link-code`, `GET /api/bridge/links`, `PATCH`/`DELETE /api/bridge/links/<id>`
  (`confirm_ok`, revoke).

**Agents and MCP.** One read-only owner tool, `bridge_status` (installs, enabled, mapped channel
count, last intake time). Nothing that installs, maps, links or reads secrets: agents never administer a bridge.

**CLI.** `commonplace bridge status` and `commonplace bridge disable [--all]` (the kill switch).
Secrets are never accepted on a command line.

**Adapter interface (`commonplace/chat_bridge/adapter.py`; the package is not named `bridge` because `commonplace/bridge.py` is the MCP client bridge).**

```
Adapter.platform: str
Adapter.caps: Caps(threads, buttons, private_reply, edit, find_posted, max_text, transport)
Adapter.verify(install, headers, body) -> Verified | Challenge | Reject(reason)
Adapter.normalize(install, verified) -> Inbound | Interaction | None
Adapter.post(install, target, outbound, idempotency_key) -> PostResult
Adapter.post_private(install, ext_user, ext_channel, outbound) -> PostResult
Adapter.update(install, ext_post, outbound) -> PostResult          # optional (caps.edit)
Adapter.find_posted(install, ext_channel, idempotency_key) -> ext_post | None   # optional
Adapter.test(install) -> TestResult                                # checks credentials and team id
```

`Inbound` carries: `ext_event, ext_user, ext_channel, ext_thread, ext_message, text, kind`
(`dm | mention | other`), `from_bot`, `shared_channel` and nothing else. `Outbound` carries
plain text plus optional buttons `[(label, payload)]`; each adapter renders and escapes it for its
own platform and splits it to `caps.max_text`. A `FakeAdapter` (in tests) implements the whole
interface in memory and records every post, so the engine is tested with no platform at all.

## Identity and linking

1. A signed-in member opens the bridge page and asks for a link code for an install. The code is
   shown once, valid 10 minutes, single use, stored only as a hash, bound to that person and
   that install, with at most 5 wrong tries.
2. They send the code to the bot **in a direct message**. The bridge looks the code up for this
   install, creates the `bridge_links` row binding the sender's immutable platform id to the
   person, and replies in the DM. A code sent in a channel is refused and reported as spent
   (it is a secret that was shown to others).
3. The direction is deliberate: the secret travels from the person's signed-in session to their
   platform account. An attacker who only controls a platform account has nothing to present;
   with the reverse flow (a link the bot sends) they could trick a signed-in person into binding
   the attacker's platform account to the victim.
4. An unlinked platform user who messages the bot gets one fixed sentence and a pointer to the
   bridge page. Their message text is dropped (not stored, not sent to the model).
5. On every event the hub re-checks: install enabled, link not revoked, person still a member of
   the workspace (`person_principal` is not `None`). Removing a member, revoking a link or
   disabling the install takes effect on the next event; open confirmations of that link are
   cancelled.
6. Linking is per install. `confirm_ok` (confirming from the platform) is a separate opt-in on
   the link, off by default, like `bots:confirm` for apps.

## One turn

1. **Verify.** The adapter checks the platform signature or token against the sealed secrets,
   and a freshness window (5 minutes) against replay. Failure: 401, no work, no row.
2. **Filter.** Ignore (state `ignored`, still answered 2xx so the platform does not retry or
   disable the app): events from bots (including this app, so no bot loops), edited and deleted
   messages, join events, files, shared or external channels (`shared_channel`), channels not
   mapped, and any channel message that is not a mention of the app. Mentions are the only
   channel trigger in v1.
3. **Journal.** Insert `bridge_events (install, ext_event)`. A duplicate id means a platform
   retry: answer 2xx at once, run nothing. The 2xx goes back inside the platform's reply
   deadline (about 3 seconds); all model work happens on the turn engine's own thread.
4. **Identity.** Look up the link; no link means the linking reply; otherwise build the person's
   principal.
5. **Store and run.** Strip the app mention, store the text as a user message of the person's
   bot (project bot for a mapped channel; General bot for a DM) with
   `request_id = "bridge:" + <ext_event hash>`, and start a turn with `surface = 'dm'` or
   `'channel'`. The bots store's unique key on `(bot_id, request_id)` is the second
   idempotency layer.
6. **Deliver.** The finished reply (and any `confirm` chips, section below) is queued in the
   outbox and posted by one worker, into the thread of the triggering message. A DM answers in
   the DM. Channel replies never start a new top-level post.
7. **Interrupted.** A hub restart mid-turn leaves `running`; the Bots layer already resolves that
   (stored `failed:interrupted` reply, never rerun). The bridge posts that single fixed
   sentence once.

Idempotency summary: event id unique per install; bot message unique per `(bot, request_id)`;
one reply per user message (existing unique index); outbox unique per `(event, part, target)`.

## Scope in channels

A DM turn is the person's web turn: their full reach, General bot included. A **channel turn** is
narrower, because everyone in the channel reads the reply, and some of them may not be allowed
to read what the asker is:

- **Intersection, then channel.** Reachable content = what the asker may read (their principal,
  re-resolved) **and** the mapped project only. Anything else is refused with a fixed sentence
  suggesting a DM. A person can never read through the bot what they cannot read in the web app.
- **Disclosure level per mapping.** `titles` (default): the bot may state work titles, statuses,
  counts, who is blocked on whom and the existence of open decisions, but not card bodies,
  comments, decision text or chat. `full`: bodies and comments of the mapped project, as the
  asker reads them. The owner sets it per channel, with a short warning that channel members who
  cannot read the project will see replies.
- **No private context.** A channel turn gets no personal memory notes, no persona
  instructions beyond the bot's name and job, no other projects, no General-bot tools, no machine
  list. The system prompt says channel replies are public to the channel.
- **The channel is not context.** Only the mention message itself goes to the model. Other
  channel messages, earlier thread messages and attachments are not read and not requested from
  the platform (the app is installed without history scopes). Thread history is an opt-in
  later feature (open question 5); if added it is quoted as other people's data and can never
  be a write argument, because writing tools only accept verbatim spans of the asker's own
  current message.
- **Shared channels refused.** Channels shared across organizations are ignored (`ignored`),
  not mapped, and the owner is told why when mapping is attempted.

## Confirmation

The Bots layer files the person's own words as routine and puts consequential tools behind a
chip. Over a chat platform an action runs with the person's permissions but the person is not
at the web app, so the rule is strict: **no action that writes with the person's identity runs
without their explicit confirmation**, and read tools run freely.

- On the `chat` surface every writing tool is a confirm tool (`create_work`, `comment_work`,
  `tell_orchestrator`, `prioritize_work`, `answer_decision`, `approve_work`, `create_project`,
  `start_work`, `stop_work`, `remember`, `forget`), except in a channel where only the mapped
  project's work tools exist at all (no memory, no machines). The Bots confirm machinery is
  reused as is: frozen arguments, server-built label, 10 minute expiry, item `version` check,
  compare-and-set consumption, `not_your_words` check before any chip exists.
- A chip is posted as a **private** message to the asker when the platform supports it (an
  ephemeral message or a DM), else as a DM; never as a button in a channel others can tap. The
  label is server-built from the frozen arguments; the model never authors button text.
- A tap arrives as a signed interaction. It runs only if: the signature and freshness verify,
  the tapper's platform id is linked to the person who owns the pending action, the link has
  `confirm_ok`, the install is enabled, the person is still a member, and the pending action
  belongs to the install and the origin channel. Anything else answers with a fixed sentence and
  runs nothing. A double tap meets `409 conflict` from the existing compare-and-set and shows
  "already done".
- `confirm_ok` off: the bot says the action needs confirming in the web app and leaves the
  existing web confirm chip in the person's thread.
- The three consequential classes (start an agent, approve, create a project) can be limited to
  web confirmation by the owner (open question 2).
- The app identity itself needs no confirmation: replying and posting chips is what the owner
  installed it for. Replies in the channel are posted as the app, never as the person.

## Permission and privacy rules

- Default off at three levels: no install exists until the owner creates one; a new install is
  disabled until `test` succeeded and the owner enables it; each person is unlinked and has
  `confirm_ok` off.
- Install, secrets, enabling, mapping and disclosure level are owner-only. Link and `confirm_ok`
  are the person's own. A member cannot map a channel.
- A person's reach is always the intersection with their own scope; the channel narrows it
  further (section "Scope in channels"). The bot never states what the asker cannot read.
- Platform content is used only where the app was mentioned or addressed directly (DM). The app
  asks the platform only for the minimum scopes (mentions, direct messages, posting, button
  interaction); history and user-listing scopes are not requested.
- Replies are plain text and platform-safe buttons built by the server. Model output is escaped
  for the platform's markup, and platform-wide mentions (`@channel`, `@everyone`, group pings)
  in a reply are neutralized by the adapter; the bot cannot page a channel.
- Redaction applies to model input, stored replies and the text posted (Bots layer rule).
- Platform tokens never appear in the repository, in logs, in audit rows, in agent
  environments or in any API response. Fixtures and tests use fake tokens only.

## Threats and controls

| Threat | Control |
|---|---|
| Forged intake request | Per-install signature or token verification with sealed secrets; constant-time compare; unguessable install id in the URL is an extra, not the control. |
| Replay of a valid event | Freshness window plus the `(install, ext_event)` primary key. |
| Confused deputy across workspaces | One platform team binds to one install; the event's team id must equal `ext_team`; a mismatch is rejected. |
| Someone else's platform account used as a person | Only the web-issued link code binds an identity; links are per install and revocable by the person and the owner; membership re-checked per event. |
| Link code theft or guessing | 10 minutes, single use, hashed, 5 tries, DM-only; a code seen in a channel is burned. |
| Leak to the channel of content the asker may read but others may not | Channel mapping, `titles` default, no private context, intersection with the asker, shared channels refused; owner warning on `full`. |
| Prompt injection from other people's text (work items, channel text) | Only the asker's own current message is writable (verbatim rule); other text is quoted data; chips are server-built; channel history is never fetched. |
| Bot posts or acts without the person's say | Strict surface policy: all writes confirm; chips private; tap bound to the person and the link. |
| Button tapped by someone else in the channel | The tap's platform id must equal the link bound to the pending action's person; chips go private anyway. |
| Bot loops or two bots amplifying each other | Events from bots, including this app, are ignored; per-install and per-person rate limits; daily cap. |
| Mass pings or phishing through the bot | The adapter neutralizes broadcast mentions and strips links the model produced unless they point to the hub. |
| Token disclosure | Sealed at rest, write-only, never logged; error strings from platforms never reach people or logs unredacted; rotation by rewriting. |
| Denial of service on the public route | Body cap (256 KiB), signature check before any parsing beyond headers, per-IP and per-install token buckets (existing `ratelimit`), 2xx for ignored events. |
| Model cost abuse by a chat flood | Bridge turns draw on the asker's shared model budget and the install `daily_cap`; the Bots per-person concurrency limits apply. |
| Stale access after removal | Link, membership and `enabled` checked per event and per tap; disabling the install cancels the outbox and open prompts. |
| Cross-posting private thread content | A reply posts only what the turn produced for this event; nothing is posted from the person's other threads. |
| Platform text breaks the UI or the store | Text length bounds (Bots bounds apply), control characters stripped, platform markup stripped from the mention. |

## Failure and replay

- **Platform retry.** A duplicate `ext_event` returns 2xx at once and runs nothing. A retry that
  arrives while the first turn runs is the same row.
- **Crash between steps.** `received` with no bot message: the hub never started the turn; the
  next sweep marks it `failed:interrupted` and posts the fixed sentence (the person re-asks). A
  stored user message with no reply: the Bots layer's interrupted logic. Nothing re-runs a turn
  or a tool.
- **Posting is at most once.** The worker marks `sending` before the call and `sent` after. A
  crash in between leaves `sending`, which the next sweep turns into `unknown` and never retries
  blindly. Where the adapter can look up its own message by idempotency key (`caps.find_posted`),
  `unknown` is resolved to `sent` or retried; otherwise one fixed "the previous reply may not have
  arrived" line goes out, once. A definite platform error before delivery (rate limit, server
  error) retries with backoff and a cap, `attempts` bounded.
- **Platform down or rate-limited.** The turn still completes and is stored; the reply stays in
  the outbox. The web thread shows it anyway. Backoff honors the platform's retry hint.
- **Confirm replays.** The tap consumes the pending action by compare-and-set; a second tap or
  retried interaction event finds it consumed and posts nothing new.
- **Disable mid-flight.** Queued outbox rows become `cancelled`; a running turn finishes and is
  stored but is not posted.
- **Bad configuration.** `test` fails with a plain reason (credentials, team mismatch, missing
  permission); the install cannot be enabled until a test passes; a platform "token revoked"
  response disables the install and audits it.

## Slice plan

Built-slice notes (assumptions the note left open): a wrong code has no row to count against, so
wrong tries are counted per platform sender and install (`bridge_link_tries`, five tries, then a
15-minute lock); the five-try cap also kills a code that was presented five times and could not
finish. Only text that is entirely a code (12 characters, spacing, dashes and markup ignored)
counts as a presentation. The person route `GET /api/bridge/links` also lists the enabled
installs (id, platform, label) so a member can pick one; `confirm_ok` is stored but its route comes
with slice 4.

Slice 3 assumptions: the Bots request id is `bridge-` plus 32 hex characters of a hash of install and
event (the Bots layer allows no colon, so not `"bridge:" + ...`). The `chat` surface of the
confirmation section is split in two for now: a direct message (`dm`) is the person's web turn
without routine writes, a mention in a mapped channel (`channel`) is read-only for the mapped
project, with `titles` meaning `project_status` and `list_work` only; routine writes become taps in
slice 4. Channel mapping routes (owner) ship here because the channel policy needs them; whether a
channel is shared is the adapter's call at intake, so the owner is not told at mapping time. An
outbox row that retries out of attempts (5) ends `failed` (a state the note's list lacks); an
exception from an adapter's `post` is treated as ambiguous and settled like a crash between send and
record. The unlinked sentence and the other fixed sentences live in `outbox.FIXED`. `/interact` and
`/oauth` stay without function until slices 4 and 5.

Slice 4 assumptions: a chip is posted through `post_private` to the platform id and channel of the
direct message that asked; a channel turn is read-only (slice 3), so no chip can exist for a channel.
`confirm_ok` off follows the note: the routine writes are not offered, a confirm tool leaves its web
chip and one fixed sentence says so (a refused write leaves no pending action). Open question 2 follows
its recommendation: `start_work`, `approve_work` and `create_project` are web-only unless the owner
sets `consequential_taps` on the install (default off, `PATCH /api/bridge/installs/<id>`); the tools
that run a command or change what a bot learned are never confirmed by tap. The two button payloads are
`confirm:<pending>` and `cancel:<pending>`, a tap is journaled as `tap:<event id>`, and the answer to a
tap, a denial included, is a private post to the tapper.

Six slices, one PR each, in order. The settings screen is designed by the UI owner on top of the
owner and person routes; no slice touches `app.js`, `rail.js` or shell files, so the node-harness
wrappers are unaffected. If a UI PR follows, it runs all node wrapper files.

Common proof for every slice: the new tests run alone through the lean test route (never the
whole suite), plus the docs obligations and fragments tests. Proof style is RED then GREEN on
the same tests: each slice's tests are written first, shown failing on the base (missing module
or refusal) and then passing on the head. A clean-workspace
test per slice uses a fresh workspace with generic fixture names only and asserts the feature is
absent or off until configured.

1. **Foundation: store, sealed secrets, installs, kill switch.** `chat_bridge.sqlite3` schema,
   sealing, owner install routes, `test` through the adapter interface, `commonplace bridge
   status|disable`, audit rows, `bridge_status`. Acceptance: no install means every `/api/bridge`
   route and the intake 404 and nothing is created; secrets are write-only and never in any
   response, log or audit row (a test greps all three); a never-tested install cannot be
   enabled; the unique platform team per hub. Proof: RED = routes missing on base; GREEN = same
   tests on head; leak test with a sentinel token; clean-workspace test.
2. **Identity: link codes and links.** Code issue/redeem (DM-only, hashed, single use, tries),
   person and owner link routes, membership re-check helper. Acceptance: a code redeemed once;
   an expired, reused or channel-sent code fails; a non-member or removed member resolves to no
   principal; owner sees links but no text; links are per install. Proof: RED/GREEN, a
   clean-workspace test, tests for revocation on the next event.
3. **Intake and turns over the fake adapter.** Adapter interface, `FakeAdapter`, intake routes,
   verify/filter/journal, surface policy (channel mapping, disclosure, no private context),
   outbox and worker, bots-store idempotency link. Acceptance (the card's list): mention gives a
   threaded reply; DM works; a channel reply uses only what the asker may read and only the
   mapped project; unmapped, shared, bot-sent, edited and unlinked cases are ignored or get the
   fixed sentence; a duplicate event runs nothing; a disabled install does nothing; default off.
   Proof: RED/GREEN on fake-platform tests; a replay test (same event twice, hub restart
   mid-turn, outbox crash between send and record); clean-workspace test.
4. **Confirmation on the chat surface.** Writing tools become confirm tools on the `chat`
   surface, private chips, signed interaction handling, `confirm_ok`, optional web-only classes.
   Acceptance: no write without a tap; a tap by another platform user, a link without
   `confirm_ok`, an expired chip and a double tap each run nothing or run once; chips never in a
   channel. Proof: RED = on base a routine tool writes from a bridge-origin turn; GREEN = it
   asks; a test that the label is server-built; clean-workspace test.
5. **Slack adapter.** Signature verification and freshness, URL verification, events (mention,
   direct message), interaction payloads, Block Kit rendering and escaping, ephemeral and thread
   posting, broadcast-mention neutralizing, message splitting, rate-limit handling, optional
   one-click install callback. Acceptance: the slice-3 and slice-4 test suites pass unchanged
   against the Slack adapter with recorded, hand-written generic payload fixtures; a forged or
   stale signature is rejected; a bad team id is rejected. Proof: contract tests run for both the
   fake and the Slack adapter (the same parametrized suite); no live network in tests.
6. **Teams and Discord adapters (two PRs).** Each implements the same interface and passes the
   same contract suite. Teams: token validation against the platform's published keys,
   tenant and service-URL checks, no history scope. Discord: interaction signature check,
   an outbound gateway transport for mentions (`caps.transport = 'socket'`), minimal intents.
   Acceptance: same contract suite; platform quirks stay inside the adapter. Proof: contract
   tests plus per-adapter fixtures.

Follow-ups, not slices: proactive "updates between turns" pushed to the person's DM (opt-in),
thread follow-ups without a mention (needs history scope), a Slack socket transport for hubs
without a public URL.

## Open questions for the owner

1. **How does a person link their platform account?** Options: web-issued code sent in a DM
   (section "Identity and linking"), or sign-in with the platform. *Recommendation:* the code.
   It needs no platform sign-in feature, works the same for every platform, and the secret flows
   from the signed-in session outward.
2. **Is a platform button tap enough for the consequential three (start an agent, approve,
   create a project)?** *Recommendation:* routine filing and answering confirm by tap in
   platform, but those three default to web-only confirmation, switchable by the owner per
   install. A chat account is a weaker credential than a signed-in session.
3. **What may a channel reply say by default?** *Recommendation:* `titles` (titles, statuses,
   counts, existence of open decisions), `full` per channel only after the owner's explicit
   choice. The asker's read right is not the audience's.
4. **Hub reachability.** HTTP events need a publicly reachable hub URL. *Recommendation:* HTTP
   first (it is what the hosted hub has), with a socket transport as a follow-up for self-hosted
   hubs behind a firewall; Discord needs the socket transport anyway.
5. **Continue a thread without mentioning the bot again?** *Recommendation:* no in v1. It needs
   history scopes and brings other people's text into context. Re-mention is explicit consent
   per message; revisit once the channel policy has run for a while.

## What we would not build

- Acting with a user token or impersonating a person on the platform; the bot always posts as
  the app.
- Reading channels in bulk, indexing platform history into memory, or learning from channels
  (that is an import with its own consent, not a bot feature).
- Joining channels on its own, messaging people who did not message it, or any proactive post in
  v1.
- One shared bot answering in a channel with a single identity for everyone (the team-bot card
  owns that); here every turn is one linked person's.
- Cross-organization shared channels, guests and anonymous users.
- Voice, huddles, file and image intake, or platform-native workflow builders.
- Summarizing or rewriting stored text: replies are the turn's reply, stored verbatim as always.
- A hosted marketplace listing or a store-submission process; installs are per-workspace by the
  owner.
- Platform tokens in environment variables or the repository, or any API that reads them back.
- A second model path, model picker or separate budget for chat platforms.
- Platform-specific logic outside an adapter.
