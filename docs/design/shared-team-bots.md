# Shared team bots: design note

Status: proposed. Card #462. Builds on the Bots layer (`docs/bots.md`, `commonplace/bots/`) and
relates to the chat-platform presence card (#455). Docs only; no code in this PR.

## 1. Problem and goal

- Today a bot and its threads belong to one person, and every tool call runs as that person.
  A team cannot keep one persistent bot (name, job, notes, history, what it may reach) that
  several members talk to.
- Goal: a bot owner can **share** a bot with chosen members of the same workspace and revoke
  it. The bot keeps one identity, configuration and history across restarts and member changes.
- The owner decides what the bot may reach (projects, wings, machines, tools) **independently of
  who talks to it**. Members see that list and its change log.
- A member can never exceed their own rights through the bot: a turn acts with the
  **intersection** of the bot's access and the talking member's access. Private threads stay
  private to their person, the bot's owner and the workspace owner included.
- Sharing is off by default, per workspace, and a freshly shared bot reaches nothing until the
  owner grants it something.

## 2. What exists and what changes

The base this design reuses, unchanged in spirit:

- `bots.sqlite3` per workspace (encrypted with the workspace), append-only verbatim
  `bot_messages`, tombstoned `bot_memory`, the action journal `bot_actions`, one-tap
  `bot_pending`, work and forward links, and turn leases.
- A bot is not a principal. A turn rebuilds the person's principal for every tool call
  (`Turn.principal()`, `sessions.person_principal`) and writes an audit row per write
  (`bot:<tool>`). Written text is only a verbatim span of the person's current message
  (`not_your_words`). Consequential tools need the person's tap.
- Every store method takes the user id and answers `Missing` for a bot that is not theirs, so a
  foreign id is indistinguishable from an unknown one.

What changes:

| Today | Shared bots |
|---|---|
| One thread per bot, owned by the bot's person | The bot has **threads**; each is private to one member or shared |
| `bots.user_id` is the only person who can reach it | `bots.user_id` is the **owner**; members reach it through a **share** row |
| A bot reads whatever its person reads | A shared bot reads **bot access ∩ talker ∩ owner** (section 5) |
| Memory notes are the bot's, read into every turn | Bot-level notes are the owner's; a member's notes are theirs alone |
| Work and forward events go to the bot's thread | They go to the thread of the person who filed them |

A bot that is not shared behaves exactly as today. Nothing in the existing routes, stores or
tests changes meaning for it.

Only `custom` bots can be shared. The per-person defaults (`project`, `general`) stay private
(their unique indexes are per person). Sharing a project's bot means creating a custom bot with
that `board` and sharing that.

## 3. Roles

| Role | Who | May |
|---|---|---|
| **Bot owner** | `bots.user_id`; a current workspace member | Share, change a member's permissions, revoke, set the bot's access, edit name/job/notes, archive, read **their own** threads and the shared threads |
| **Member with a share** | A workspace member listed in `bot_shares`, not revoked | Per permissions (below), always within their own scope |
| **Workspace owner** | `memberships.role = 'owner'` | Turn sharing on or off for the workspace; list every shared bot (name, owner, members, access, not threads); disable or unshare any; adopt an ownerless bot. Does **not** read anyone's private thread and gets no extra reach through a bot |
| **Anyone else** | Other members, other workspaces, agent tokens | 404, as for an unknown id |

Per-member permissions on a share (two flags, both default off):

- `talk`: open own private threads, open shared threads, and write in a shared thread the member
  can read (one they opened, or any when they also have `see_shared`).
- `see_shared`: read the bot's shared threads. Without `talk` it is read-only.

A share with both flags off is not storable (a share is either useful or removed).

## 4. Data model

All tables live in the workspace's `bots.sqlite3`. New columns are added by the existing
`_upgrade` path; new tables by `CREATE ... IF NOT EXISTS`. Ids are `uuid4().hex`, times are UTC
`YYYY-MM-DDTHH:MM:SSZ`.

```sql
-- 4.1 bots: ownership and the sharing state
ALTER TABLE bots ADD COLUMN shared INTEGER NOT NULL DEFAULT 0 CHECK (shared IN (0,1));
ALTER TABLE bots ADD COLUMN access_version INTEGER NOT NULL DEFAULT 0;   -- compare-and-set token
ALTER TABLE bots ADD COLUMN frozen_at TEXT;      -- owner left or workspace owner disabled it
-- invariant (store-enforced): shared=1 implies kind='custom'

-- 4.2 shares: who may reach a shared bot, and with what
CREATE TABLE bot_shares (
    bot_id TEXT NOT NULL,
    user_id TEXT NOT NULL,                       -- the member the bot is shared with
    talk INTEGER NOT NULL DEFAULT 0,
    see_shared INTEGER NOT NULL DEFAULT 0,
    shared_by TEXT NOT NULL,                     -- always the bot's owner at the time
    shared_at TEXT NOT NULL,
    history_from INTEGER NOT NULL,               -- first shared-thread message id this member may read
    revoked_at TEXT,
    revoked_by TEXT,
    revoke_reason TEXT CHECK (revoke_reason IN ('owner','member_removed','unshared','disabled')),
    PRIMARY KEY (bot_id, user_id),
    CHECK (talk = 1 OR see_shared = 1 OR revoked_at IS NOT NULL)
);
-- A revoked row stays (history, attribution). Re-sharing clears revoked_at and sets a new
-- shared_at and history_from; it never restores old history_from.

-- 4.3 threads: private to one member, or shared with the bot's see_shared members
CREATE TABLE bot_threads (
    id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    user_id TEXT NOT NULL,                       -- the person who opened it
    visibility TEXT NOT NULL CHECK (visibility IN ('private','shared')),
    title TEXT NOT NULL DEFAULT '',              -- set by its opener; not derived from messages
    created_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE UNIQUE INDEX bot_threads_private ON bot_threads(bot_id, user_id)
    WHERE visibility = 'private';                -- one private thread per member and bot
-- The thread a bot has today (messages with thread_id NULL) is the owner's private thread:
-- no row is created for it and no existing message is rewritten (messages are append-only).

-- 4.4 messages: which thread, who spoke
ALTER TABLE bot_messages ADD COLUMN thread_id TEXT;   -- NULL: the owner's original private thread
ALTER TABLE bot_messages ADD COLUMN author_id TEXT;   -- user messages: who wrote it (NULL: the owner)
-- Replace the request-id unique index so two members can never collide on, squat or replay each
-- other's request id:
DROP INDEX bot_messages_request;
CREATE UNIQUE INDEX bot_messages_request
    ON bot_messages(bot_id, COALESCE(thread_id, ''), request_id) WHERE role = 'user';
-- A thread is read by (bot_id, thread_id); the append-only triggers stay as they are.

-- 4.5 reads: unread counts are per person and thread (bots.read_upto stays for the owner's
-- original thread)
CREATE TABLE bot_reads (
    bot_id TEXT NOT NULL, thread_id TEXT NOT NULL, user_id TEXT NOT NULL,
    upto INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bot_id, thread_id, user_id)
);

-- 4.6 access: what the bot may reach. Set only by the owner. Default deny.
CREATE TABLE bot_access (
    bot_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('project','wing','machine','tool')),
    ref TEXT NOT NULL,                           -- project slug, wing name, machine id, tool name
    mode TEXT NOT NULL CHECK (mode IN ('read','write','use')),   -- project/wing: read|write; machine/tool: use
    set_by TEXT NOT NULL,
    set_at TEXT NOT NULL,
    PRIMARY KEY (bot_id, kind, ref)
);

-- 4.7 access log: append-only (triggers refuse UPDATE and DELETE); readable by owner and members
CREATE TABLE bot_access_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bot_id TEXT NOT NULL,
    version INTEGER NOT NULL,                    -- bots.access_version after the change
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('grant','change','remove','share','unshare','permissions','freeze','adopt')),
    kind TEXT, ref TEXT, mode_before TEXT, mode_after TEXT,
    member_id TEXT,                              -- for share, unshare and permissions
    at TEXT NOT NULL
);

-- 4.8 memory: bot-level notes (owner) versus a member's own notes
ALTER TABLE bot_memory ADD COLUMN owner_id TEXT;  -- NULL: bot-level note (written by the owner)
                                                  -- else: only that person's turns read it

-- 4.9 work and forwards: the person (and thread) who filed it gets the event
ALTER TABLE bot_work ADD COLUMN user_id TEXT;       ALTER TABLE bot_work ADD COLUMN thread_id TEXT;
ALTER TABLE bot_forwards ADD COLUMN user_id TEXT;   ALTER TABLE bot_forwards ADD COLUMN thread_id TEXT;

-- 4.10 pending confirmations already carry user_id; add the thread
ALTER TABLE bot_pending ADD COLUMN thread_id TEXT;
```

Workspace setting (control side, numbers and flags only, no conversation text):
`bot_sharing` in the workspace settings, `off` by default, changed only by the workspace owner.
Caps: at most 20 shared bots per owner, at most 50 shares per bot, at most 25 members with
`see_shared` per bot (the audience rule in section 6 evaluates each of them).

Every read and write of a thread goes through one store function,
`BotStore.reach(user_id, bot_id, thread_id) -> Reach | Missing`, that decides *owner*, *own
private thread*, *shared thread with permission*, or `Missing`. No route or tool reads
`bot_messages` any other way.

### Who may read and write each thing

| Data | Owner | Member `talk` | Member `see_shared` | Workspace owner | Other |
|---|---|---|---|---|---|
| Bot name, job, notes, access list, access log, member roster | read/write (notes, access, roster) | read (not other members' shares, threat 8) | read | read (list view) | 404 |
| Own private thread | read/write | read/write | no private threads without `talk` | no | 404 |
| Another member's private thread | no | no | no | no | 404 |
| Shared thread | read, write | write | read (from `history_from`) | no | 404 |
| Bot-level memory notes | read/write | read | read | no | 404 |
| A member's own notes | no | read/write (own) | no | no | 404 |
| Shares | read/write | read own row | read own row | read, disable | 404 |

## 5. Permission and privacy rules

**One effective-access function.** `commonplace/bots/access.py` (new) holds
`effective(bot, talker, owner) -> Access`, used by every path that reaches data for a turn:

```
effective reach for a shared bot =
      what the bot's access list grants        (bot_access rows, default none)
   ∩  what the talker may do right now          (their fresh person principal)
   ∩  what the owner may do right now           (owner's fresh person principal)
```

- The third term means an owner cannot lend what they have lost: when the owner loses a
  project, that grant goes dormant for everyone. A grant is also checked against the owner's
  rights when it is set (`403 outside_your_scope`), so the list never names something the
  owner could not reach themselves.
- For a bot that is not shared the access list is not consulted: the reach is the person's own,
  as today.
- The mode matters: `read` lets the bot's read tools see the project, `write` additionally lets
  its routine and confirm tools write there (file, comment, forward, answer). A `read` grant
  never allows a write even if the talker may write.
- Machines and tools use the same shape. `list_machines` returns machines in
  `bot access ∩ what the talker may use`. Today only workspace owners may start agents, so a
  member talking to a shared bot sees an empty list and the reason, even if the owner granted a
  machine. The bot never widens the runner's rule.
- Wings: the Bots layer has no tool that reads memory wings yet. The `wing` kind is part of the
  model and the API but is accepted for a grant only once a wing-reading tool is registered
  (until then `PUT access` answers `400 unsupported_kind` for it). The tool must go through the
  same function.

**Enforced in one place per layer, and made impossible to forget.**

- `Turn.readable()` and `Turn.board()` return the project list/slug after intersecting with
  `effective(...)`. Every project tool already goes through them.
- `tools.offered(turn)` drops tools the bot's access does not list (a tool name grant) and
  tools whose `reach` the effective access does not cover.
- `Tool` gets a required field `reach`: one of `none` (the built-in `remember`/`forget`, and
  `my_access`), `project:read`, `project:write`, `machine`, `wing:read`, `wing:write`.
  `register()` refuses a tool without it, the same way it refuses a free-text confirm tool
  without `check`. `TurnRun._tool` checks the call against the effective access immediately
  before `run`, re-resolved on every call, so a model naming a tool or project that was not
  offered is refused, as today.
- The prompt snapshot (`context._snapshot`) is built only for a project in the effective reach.
- The model is told the bot's granted categories in the system prompt (quoted names only) and
  gets a built-in read tool `my_access` that reports what the bot may reach for this asker and
  why a thing is outside it.

**Refusals with a clear reason, without leaking existence.** A refusal names its cause only as
far as the asker may know it:

| Asker's situation | Sentence (fixed text) |
|---|---|
| The project is readable by the asker but not in the bot's access | "This bot is not allowed to use that project. Its owner sets that." (`outside_bot_access`) |
| The project is not readable by the asker (in or out of the bot's access) | "I can't find that." (`not_found`, same as today's `Gone`) |
| A write on a project granted `read` only | "This bot may only read that project." (`bot_read_only`) |
| A tool or machine not granted | "This bot is not allowed to use that. Its owner sets that." (`outside_bot_access`) |
| Share revoked mid-turn | The turn ends with a stored `failed:revoked` reply with a fixed sentence |

The sentence is fixed text. It never carries project names the asker cannot read.

**Owner-only settings.** Only the bot's owner may change shares, permissions, access, name, job,
notes and archive state (`403 owner_only` for a member who can see the bot). Changing the
access list is a compare-and-set on `access_version` and writes (a) one row per changed entry to
`bot_access_log` and (b) one audit row in the control database (`bot_access:set`, ids and
`kind/ref/mode` only, never text). Members read the list and the log.

**Default off.** The workspace switch `bot_sharing` is off; `POST .../shares` answers
`409 sharing_off` until the workspace owner turns it on. Turning it off suspends all shares
without changing them: `reach` answers `Missing` for members while it is off, and they resume
when it is turned on.

**Seed.** When a bot is first shared and has a `board`, one `project:read` entry for that board
is seeded (logged as `grant`, actor = owner). Nothing else is seeded.

**Persona text grants nothing.** The owner's `instructions` shape tone only (quoted data, as
now). Members may read them in the bot's detail. This is stated in the UI so owners do not put
secrets there.

**Budgets.** A shared-bot turn counts against the **talker's** per-person daily budget and
per-minute rate (`bot_model_calls` keyed by the talker, as now). The workspace `daily_limit`
stays shared. Concurrency: one running turn per (bot, thread) instead of per bot, at most 4 per
shared bot overall; the per-person (4) and per-process (32) caps stay.

## 6. Threads and the audience rule

- **Private thread.** One per (bot, member). Only that person reaches it (`reach` checks
  `bot_threads.user_id` or the owner's legacy thread). A turn runs as that person with
  `bot access ∩ person ∩ owner`.
- **Shared thread.** Opened by a member with `talk` as `visibility = 'shared'`; readable by every
  member with `see_shared` (and the owner) from their `history_from`. A thread cannot be turned
  from private to shared or back; to share content, start a shared thread. The opener may
  archive it.
- **The audience rule.** A shared thread's replies are visible to people whose rights differ from
  the talker's. A turn in a shared thread therefore reaches only
  `bot access ∩ talker ∩ every current see_shared member ∩ owner`. Computed at turn start and at
  each tool call from fresh principals (at most 25 audiences). A member who cannot read a project
  is never shown content from it through a shared thread, and a refusal says only
  "I can't use that in a shared thread." (`audience_limited`).
- **Blocks.** Blocks are references hydrated with the **reader's** rights (`gone` when the
  reader cannot see the item), as they are today. In a shared thread, `event` messages use the
  fixed text "A task filed here changed." and carry only the `work` block; titles and notes are
  other people's text and are not copied into shared text.
- **No history for a new member.** A member shared in later reads shared messages from their
  `history_from` (the newest message id at the time of the share). Older shared messages stay
  hidden. Revoking and re-sharing sets a new `history_from`.
- **Who spoke.** Shared messages carry `author_id`; display shows the member's name. The bot
  reply is stored under the talker's request, exactly as today (one reply per user message).
- **Everyone writes only their own words.** The verbatim rule binds to the *current talker's*
  message. A member's earlier message in a shared thread can be context but can never be copied
  into a write for another member.

## 7. API and MCP surface (names only)

Person-only, same gateway checks as `/api/bots*` (`bots.auth.person`; agent tokens 404). All
membership checks run per request.

Roster and threads:

- `GET /api/bots` (entries add `mine`, `shared`, `owner:{id,name}`, `perms`, `frozen`; a member
  sees a shared bot only while their share is live)
- `GET /api/bots/<id>/threads`, `POST /api/bots/<id>/threads {visibility, title?}`,
  `PATCH /api/bots/<id>/threads/<tid> {archived}`
- `GET|POST /api/bots/<id>/messages?thread=<tid>`, `POST .../stop`, `.../confirm`, `.../read`
  (`thread` defaults to the caller's private thread)
- `GET|POST|DELETE /api/bots/<id>/memory` (bot-level notes: owner; `?own=1`: the caller's notes)

Sharing and access:

- `GET /api/bots/<id>/shares` (owner: all; member: their own row)
- `POST /api/bots/<id>/shares {user, talk, see_shared}`, `PATCH .../shares/<user>`,
  `DELETE .../shares/<user>` (owner only; idempotent)
- `GET /api/bots/<id>/access` (owner and members; includes `version`),
  `PUT /api/bots/<id>/access {version, entries}` (owner only; compare-and-set),
  `GET /api/bots/<id>/access/log`
- `GET /api/bots/shared` (workspace owner: every shared bot in the workspace, no threads),
  `POST /api/bots/<id>/disable`, `POST /api/bots/<id>/adopt` (workspace owner)
- `GET|PATCH /api/bots/settings {sharing}` (read: any member; write: workspace owner)

Apps: an app with `bots:chat` already acts as its person and so reaches shared bots only through
that person's live share. Sharing, access and adoption changes are **web-session only** in this
design (no app scope for them); an app asking gets 404.

MCP: **none.** Bots are person-only and agent tokens get 404; an agent cannot share a bot or
change its access.

Bounds: member id must be a current workspace member other than the owner; thread title 0 to
60; at most 200 access entries per bot; error bodies carry fixed text and codes (`invalid`,
`sharing_off`, `owner_only`, `outside_your_scope`, `conflict`, `limit`, `unsupported_kind`).

## 8. Failure and replay behaviour

- **Nothing runs twice.** The request id is unique per (bot, thread); a repeated request with
  the same text returns the stored message, with other text it is `conflict` (as today), and
  never touches another member's thread. Confirm is compare-and-set on `bot_pending` bound to
  the asking `user_id` **and** thread; another member's tap is 404. Interrupted turns keep the
  current lease-and-journal recovery (`failed:interrupted`, never rerun).
- **Share and access changes are idempotent.** `POST shares` for a live share with the same
  flags is a no-op `200`; with other flags it is a `permissions` change and one log row.
  `PUT access` carries the `version` it was based on; a stale version is `409 conflict` and
  nothing is written; a payload equal to the stored list writes no log row and no audit row. A
  retry after a lost response therefore never double-audits.
- **Revoke is immediate.** Every request re-reads the share and membership (no cache). An
  in-flight turn re-resolves the share before each model request, each tool call and before its
  reply is stored. If the share ended, the turn stops, nothing from a tool after that point
  runs, and the stored reply is a fixed `failed:revoked` sentence with no model text. In a
  private thread the person simply no longer reaches it.
- **Restart.** All state is in `bots.sqlite3` and the control database. Leases and journal behave
  as today. A hub restarted mid-turn marks the request interrupted on next read.
- **Roster of a revoked member** drops the bot at once; direct requests for it are 404.
- **A failing sharing store** (cannot read shares) fails closed: the member gets 404 for the
  shared bot and the turn ends with a fixed error; the owner's private bots are unaffected.
- **Events.** A task or forward a member filed notifies **that member's thread** only, after
  checking that member's live share and rights (existing `updates.py` rules); the event is
  dropped when either has ended.

## 9. Lifecycle: members and owners leaving

- **A member is removed from the workspace.** `person_principal` already returns `None`, so they
  reach nothing at once. A hook on membership removal sets `revoked_at` on their shares
  (`revoke_reason = 'member_removed'`). Their messages in shared threads stay (append-only,
  attributed by id); their private threads stay in the store, unreachable by anyone, and are
  removed only with the workspace. Re-adding them does not restore the share; the owner shares
  again.
- **A member's share is revoked.** Same effect, reason `owner` or `unshared`. Their private thread
  with the bot stays theirs but is unreachable while no share is live. A new share makes it
  reachable again; shared-thread history restarts at the new `history_from`.
- **The bot's owner is removed.** The bot is frozen (`frozen_at`): all turns stop, nobody can
  talk, and members see "paused: owner left". Nothing is deleted. A workspace owner may
  **adopt** it: the adopter becomes `bots.user_id`; the access list is kept but evaluated
  against the adopter's rights (the owner term of section 5), so it can only shrink; the
  adoption is logged. The old owner's private thread is not readable by the adopter.
- **Sharing switched off, or a bot disabled by the workspace owner.** Shares are suspended; no
  data is deleted; members and apps get 404 for the bot until it is on again.
- **The bot is archived.** Threads are read-only, shares stay, no new turns.
- **The workspace is deleted.** Everything goes with it (one file, one workspace).

## 10. Threat list

| # | Threat | Control |
|---|---|---|
| 1 | A member reads another member's private thread through the bot | Every thread access goes through `BotStore.reach`; the key is (bot, thread, person); other threads are `Missing` (404); test with two members |
| 2 | A member gets more than they may by asking the bot (the owner's rights are lent) | Turns run as the talker; reach is the three-way intersection; read-only grants cannot write; test an owner who reads a project the member cannot |
| 3 | Shared thread leaks a project to a see_shared reader who cannot read it | Audience rule (section 6); blocks hydrate per reader; event texts carry no titles in shared threads |
| 4 | One member's notes or messages leak into another's turn | Per-person notes (`owner_id`); private threads never enter another's context; shared-thread context only inside shared threads |
| 5 | Prompt injection by a member's message or the owner's persona text | All text is quoted data; persona grants nothing; writes are only verbatim spans of the **current talker's** message; confirm tap by that person |
| 6 | Request-id squatting or replay across members | Unique index per (bot, thread, request id); confirm bound to user and thread |
| 7 | Access survives revoke or removal | Per-request and per-tool-call re-resolution; check before storing the reply; membership removal hook; tests for the in-flight case |
| 8 | A member learns what exists (other members, bots, projects, owner's grants) | A member sees the bot's own detail, access and log, and their own share row, not other members' shares; refusals name only what the asker can already read; uniform 404 |
| 9 | Owner reads members' private conversations or abuses logs | Owner has no read path to them; audit rows and logs carry ids and codes, never text; the owner sees counts only if added later (not in this design) |
| 10 | Owner lends access they lost or never had | Grant checked against the owner's rights when set and at every turn (owner term) |
| 11 | Privilege change by a stale write or two owners' tabs | `access_version` compare-and-set; one audit row per real change |
| 12 | One member exhausts the model budget or the bot | Talker's own daily and per-minute budget; per-bot concurrency cap; share, audience and access caps |
| 13 | An app or agent token changes sharing silently | Sharing, access and adoption are web-session only; agent tokens 404; no MCP tool |
| 14 | Another workspace sees anything | One store file per workspace; every lookup keyed by the workspace's file; cross-workspace test |
| 15 | Sharing enabled by accident exposes a bot | Workspace switch off by default; an empty access list after sharing (only the board seed); every share and grant logged |
| 16 | A removed member's data is read by the owner or adopter | Their private threads stay unreachable; adoption never opens them |

## 11. Slice plan

One PR per slice. Each slice updates `docs/bots.md`, the `docs/modules/bots.md` fragment and a
short `docs/system/<slug>.md` pointer for its decision (no monolith edits). These are new
behaviour, so "RED on base" means the new tests fail against the pinned base and pass on the
head. Each slice runs only the test files it touches; the full suite runs per batch.

**S1. Store foundation (no routes, no behaviour change).**
Tables and columns of section 4, the `reach` function, thread-scoped request-id index, a
store-level `share`/`revoke`/`access` API, the upgrade of an existing file, and the workspace
setting (default off).
- Acceptance: an existing bots file opens and every existing bot test passes unchanged; a
  private bot's behaviour is identical; `reach` answers per the table in section 4; duplicate
  request ids across threads are allowed, within one thread are idempotent.
- Proof: new `tests/test_bots_shares_store.py` RED on base (no tables) and GREEN on head;
  existing `tests/test_bots_store.py`, `tests/test_bots_api.py` GREEN on both; a test that
  reopens an old-format file; a clean-workspace test with generic members (Avery owner, Blake and
  Casey members), no real names. Node harness: not touched.

**S2. Effective access and enforcement.**
`bots/access.py` (`effective`), `Tool.reach` and the registration guard, enforcement in
`Turn.readable/board`, `tools.offered`, `TurnRun._tool`, `machines`, the prompt snapshot, the
built-in `my_access` tool and the refusal sentences; access routes (`GET/PUT access`, `access/log`,
audit rows); a fixture-only way to mark a bot shared (no share routes yet).
- Acceptance: for a shared bot a talker reaches only `grant ∩ talker ∩ owner`; a read grant
  cannot write; a tool without `reach` fails registration; a project outside the grant is
  `outside_bot_access` for a talker who can read it and "can't find" for one who cannot; owner
  edits are compare-and-set with one log row and one audit row per change; unshared bots are
  unaffected.
- Proof: `tests/test_bots_access.py` RED/GREEN on a table of (grant, talker, owner) combinations;
  per-tool tests that the existing project and machine tools honour it; log hygiene (no text in
  audit rows or logs); clean-workspace test. Node harness: not touched.

**S3. Sharing and member private threads.**
Share routes, roster entries, member private threads (`?thread=`), per-person notes, per-person
unread, events routed to the filer's thread, the `bot_sharing` switch route, revoke, per-thread
concurrency, the in-flight revoke check. Shared threads are not yet creatable (`POST threads`
with `visibility: shared` answers `409 not_available`).
- Acceptance (card items 1, 2, 4 in part, 5): the owner shares and revokes; the roster shows the
  bot to the member only, with its owner; a member talks in their own private thread with the
  bot's access applied; member A cannot read B's private thread; revoke takes effect on the next
  request and in an in-flight turn; the owner cannot read a member's private thread; other
  workspaces see nothing; sharing off means 409 and 404.
- Proof: `tests/test_bots_shares_api.py` over real HTTP with a scripted model, RED/GREEN, with
  these named cases: cross-member isolation, revoke immediacy (including mid-turn), scope
  intersection end to end, other workspace, agent and app tokens, replay across members,
  confirm hijack, switch off, caps; clean-workspace test; `tests/test_bots_turn.py` unchanged
  GREEN. Node harness: not touched by the API. The web screens are a separate slice (below).

**S4. Shared threads and the audience rule.**
`visibility: shared` threads, `see_shared`, `history_from`, the audience rule in `access.effective`,
generic event text in shared threads, authorship in messages, caps.
- Acceptance: a see_shared member reads shared messages from their `history_from` and no more;
  a member with `talk` only writes in a shared thread they opened and reads nothing else (reading
  needs `see_shared`); a turn in a shared thread never reaches a project that any current see_shared member cannot
  read; blocks hydrate per reader.
- Proof: `tests/test_bots_shared_threads.py` RED/GREEN including the leak case (talker reads a
  project, audience member does not), a member added later, a member removed mid-thread, and
  events without titles; clean-workspace test. Node harness: not touched.

**S5. Lifecycle.**
Membership-removal hook, owner-removed freeze, adopt, disable, workspace list for the owner, the
`archived` interplay, plus the docs pass that folds the decisions into `SYSTEM.md` through the
`docs/system` fragments.
- Acceptance (card item 4): removing a member ends their access and keeps the bot and its shared
  history; removing the owner freezes the bot and a workspace owner can adopt it with the access
  list re-capped; nothing is deleted.
- Proof: `tests/test_bots_shares_lifecycle.py` RED/GREEN, including re-adding a removed member
  (no share), adopt re-capping and the adopter unable to read the old owner's thread;
  docs-obligations and fragment tests GREEN. Node harness: not touched.

**UI (not counted, separate thread).** The screens (roster badges, share sheet, access editor,
log, thread picker, frozen state) belong to the design thread, which owns all visuals. The API
of slices S3 to S5 is the contract. That slice touches `app.js`/`rail.js` and must run all of the
node-harness wrapper files, not just its narrow tests.

Order: S1, S2, S3 ship the safe core (private threads only). S4 waits for the owner's answer to
question 2. S5 can run in parallel with S4 once S3 is in.

### Card acceptance mapping

| Card item | Slice |
|---|---|
| 1 share and revoke; roster to members only with owner | S3 |
| 2 per-member permissions; intersection; private stays private | S2, S3, S4 |
| 3 access set by owner only, shown, audited; clear refusals | S2 |
| 4 persistence; member removal rules | S1, S3, S5 |
| 5 tests (isolation, revoke, intersection, other workspaces, clean workspace); docs | every slice |

## 12. Open product questions for the owner

1. **Who may share?** Recommendation: a per-workspace switch the workspace owner turns on (off by
   default); once on, any member may share their own custom bots; the workspace owner can
   disable any. Alternative: workspace owner approves each share.
2. **Shared threads in the first release or private-only first?** The audience rule makes shared
   threads deliberately restrictive (they reach only what everyone in the audience can read).
   Recommendation: ship S1 to S3 (private threads) first, decide on S4 after using it.
3. **Whose budget does a shared-bot turn use?** Recommendation: the talker's own daily budget, as
   now, plus (later) an optional per-bot cap the owner sets.
4. **Can members read the bot's persona notes?** Recommendation: yes, read-only, with a notice
   that notes must hold no secrets (members should know how the bot was told to behave).
5. **When the owner leaves, freeze and adopt or auto-transfer?** Recommendation: freeze, then a
   workspace owner adopts; the access list is only re-capped, never widened. Auto-transfer to a
   member would hand over reach without a decision.

## 13. Relation to other cards

- **#429 Bots layer:** this adds the threads, shares and access model on top; the single-person
  contract is unchanged for unshared bots.
- **#455 chat-platform presence:** a bot present in a channel is a shared bot whose askers are
  platform users. The adapter must map each platform user to a workspace member and run the turn
  as that member under the same intersection; an unmapped asker reaches nothing. This design
  gives that card its permission core (`effective`, shares, the audience rule for channel
  threads) and does not decide the adapter.
- **Learning memory card:** wing grants (kind `wing`) become usable once a wing-reading bot tool
  exists; it must register with `reach` like every other tool.

## 14. What this design does not build

- Guests, external askers or bots with their own rights, tokens or identity. A bot is never a
  principal.
- Any way for an owner, adopter or workspace owner to read someone else's private thread, or an
  export of one.
- Cross-workspace sharing, public bots, or sharing the per-person defaults.
- Teams, groups, roles beyond the two flags, or per-member overrides of the bot's access list.
- Editing or deleting messages, flipping a thread between private and shared, or moving
  messages between threads.
- Bot-to-bot calls, bots started by agents, and sharing changes through an app scope or MCP.
- Per-member billing or cost reports, and an owner view of members' usage.
- A copy of a bot's history for a new member; a new member starts at their `history_from`.
- Ownership transfer initiated by the owner (adoption by a workspace owner only).
- The web screens themselves (design thread).
