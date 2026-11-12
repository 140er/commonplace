# Bots

A bot is a private chat thread that belongs to one person. Each person gets one bot per
project they can read, a General bot, and any custom bots they create. A bot is not a user
and has no token or rights of its own: whatever it reads is read as the person it belongs to,
and what that person may not see, it cannot show.

This page covers the storage, the read and management routes, and talking to a bot: a turn
streams the reply of the workspace's chat model, runs tools as the person and stores the reply.

## Routes

All routes need a signed-in person session, or that person's own app holding a granted scope
(see [Using bots from your own app](#using-bots-from-your-own-app)). Any other agent token or
machine credential gets 404, exactly like a path that does not exist; an anonymous caller gets 401. Writes pass the
gateway's same-origin check. A bot id that is unknown or belongs to someone else is 404.

| Route | Body, result |
|---|---|
| `GET /api/bots` | `{bots, model:{ready,label,code}, can_configure}`; creates the default bots on first use |
| `POST /api/bots` | `{name, job?, instructions?, board?}`, 201 `{bot}` |
| `PATCH /api/bots/<id>` | `{name?, job?, instructions?, archived?}`, `{bot}` |
| `GET /api/bots/<id>/messages?after=&before=&limit=` | `{bot, messages, waiting, running}` |
| `POST /api/bots/<id>/messages` | `{text, request_id}`, a `text/event-stream`; with `?stream=0`, `{message, reply}` when the turn ends |
| `POST /api/bots/<id>/stop` | `{request_id}`, `{ok}` (true when that turn was running) |
| `POST /api/bots/<id>/confirm` | `{pending, accept}`, `{message}` (an `event` message with the action block) |
| `POST /api/bots/<id>/read` | `{upto}`, `{ok}` |
| `GET /api/bots/<id>/memory` | `{memories:[{id,text,at}]}` |
| `POST /api/bots/<id>/memory` | `{text}`, `{memory}` |
| `DELETE /api/bots/<id>/memory/<mid>` | `{ok}` |
| `GET /api/bots/<id>/access` | `{version, entries:[{kind,ref,mode,set_at}]}`: what a shared bot may reach (its owner, and members with a live share) |
| `PUT /api/bots/<id>/access` | `{version, entries}`, `{version, entries}`: the owner replaces the list; web session only; `409 conflict` for a stale `version` |
| `GET /api/bots/<id>/access/log?after=&limit=` | `{log}`: one row per change, ids and codes only |
| `GET /api/bots/<id>/shares` | `{shares:[{user:{id,name},talk,see_shared,shared_at}]}`: the owner sees every live share, a member only their own |
| `POST /api/bots/<id>/shares` | `{user, talk, see_shared}`, `{share}` (201 new, 200 same flags again): owner only, web session only, a current member other than the owner; `409 sharing_off`, `409 limit` |
| `PATCH` / `DELETE /api/bots/<id>/shares/<user>` | `{talk?, see_shared?}` → `{share}`; `{ok}` (revoking again is a no-op); owner only, web session only |
| `GET /api/bots/<id>/threads` | `{threads, own}`: the threads the caller reaches and the id of their own private thread (`null` for the owner, whose thread has no id) |
| `POST /api/bots/<id>/threads` | `{visibility, title?}`, `{thread}`: opens the member's private thread (once); `visibility: shared` is `409 not_available` for now |
| `PATCH /api/bots/<id>/threads/<tid>` | `{archived}`, `{thread}`: the person who opened it |
| `GET` / `PATCH /api/bots/settings` | `{sharing, can_configure}`; any member reads, a workspace owner changes it (web session only) |

Messages, stop, confirm, read and memory also take `?thread=<id>` (memory takes `?own=1` for a
member's own notes). Without it a request acts on the caller's own thread: the owner's original
thread, or a member's private one.

The learning memory (`docs/design/bot-learning-memory.md`, first slices: the store, the switches,
forgetting and the extractor) adds person-session routes. They are off by default and
answer 404 to an app token, an agent token or another person's id:

| Route | Body, answer |
|---|---|
| `GET`, `PATCH /api/bots/<id>/learning` | `{mode?: off|ask|auto, pause?: 1..30 days or null}`, `{learning}` (mode, paused, `active`, counts) |
| `GET /api/bots/<id>/learned`, `GET /api/learned` | `?scope=&state=&kind=&limit=&offset=` (limit at most 100), `{items, counts}`; `remember` notes appear as `kind` `note` |
| `GET`, `PATCH`, `DELETE /api/learned/<id>` | edit `{text?, pinned?}` (a secret is 400 `refused`), `{item}`; delete is real and a second delete is 404 |
| `POST /api/bots/<id>/learned/forget-all` | `{}`, `{forgotten, notes, floor}`; the bot ignores every message up to `floor` from then on |
| `GET`, `PATCH /api/learning/settings` | `{enabled?, shared_enabled?}` (workspace owner), `{enabled, shared_enabled, can_configure}` |
| `GET`, `PATCH /api/projects/<slug>/learning` | `{shared_enabled}` (workspace owner), `{board, shared_enabled, can_configure}` |

Nothing is learned unless the owner turned learning on for the workspace and the person turned
it on for that bot (`ask` or `auto`, not paused). After a finished turn the hub may then read the
person's own recent messages with the chat model the owner configured and keep a few lasting
preferences, instructions, facts and corrections. Each kept item is the person's own words, a
verbatim span checked against the stored message and the secret guard; anything the model
invents or rewrites is dropped. A repeated sentence is counted, new words for a known topic
replace the old item, a sentence the person forgot is not learned again, and `ask` mode only
proposes (proposals expire after 14 days). It uses no new service and no extra model call
unless every switch allows it. Each later turn then reads the person's own items that match the
new message and offers up to 8 of them to the model as quoted, labelled data ("learned"); the
person's current message always wins over them. Nothing is used when a switch is off or the
person paused. Showing the items in the thread and sharing come in later slices.

A bot is `{id, name, job, instructions, kind, board, archived, last, unread, waiting, state}`.
`kind` is `project`, `general` or `custom`. `last` is the newest message (`role`, `text`
shortened to 200 characters, `at`) or null. `unread` counts bot and event messages after the
read mark. `waiting` is the number of the person's open decisions for the bot's project, or
across every readable project for General and for a custom bot without a project. `state` is
`needs_you` while something waits, `working` while a turn runs, else `idle`. The thread
listing's `running` is true while a turn of this bot runs.

Defaults: one `project` bot for each non-archived project the person can read, named after
it, plus one `general` bot. An archived default is never created again. The roster is sorted
by last activity; bots that were never used come last.

`model` reports whether the workspace has a usable chat model. `code` is empty when it does,
otherwise `needs_configuration`, `needs_turn_on` (the project inherits a hosted setting it has
not been turned on for) or `key_missing`. `can_configure` is true for the workspace owner.

Bounds: name 1 to 60 characters, job at most 200, instructions at most 4000, message text 1
to 8000, memory note 1 to 2000, request id 8 to 64 of `A-Za-z0-9_-`, at most 50 live bots per
person (409 `limit`), `limit` at most 100 (default 50). Out-of-bound input is 400 `invalid`.

## Blocks

A message may carry blocks: small references that are filled in when the thread is read,
with the reader's own permissions. A `work` block shows a task's title, a status word
(`queued`, `working`, `needs_you`, `ready`, `blocked`, `done`), a note and links; a `decision`
block shows the open question and its options, or the answer. An `action` block is one line
for a tool the bot used (`tool`, `label`, `ok`, and a `code` when refused). A `confirm` block
adds the server-built `label` and `state` (`open`, `done`, `cancelled`, `expired`) of an action
waiting for the person's tap. A `setup` block says no model is usable and whether the reader
can configure one. An item the reader cannot see, or that no longer exists, comes back as
`gone`. Tasks are always named by their number.

## Talking to a bot

`POST /api/bots/<id>/messages` stores the person's message, starts a turn on its own thread
and relays that turn as server-sent events. Each frame is `event:` plus a JSON `data:` line and
is flushed at once; a `: ping` comment goes out every 10 seconds.

| Event | Data |
|---|---|
| `ack` | `{message}`: the stored user message; sent before any model work |
| `delta` | `{text}`: reply text, in order, while the model is still sending; whole words only, redacted over the whole reply so far, and together exactly the stored reply text |
| `action` | `{step, tool, label, state, code?}`: `running`, then `ok` or `refused` |
| `block` | a hydrated block a tool returned (a work item, a `confirm` chip) |
| `done` | `{message, timing:{first_token_ms, total_ms}}`: the stored bot message |
| `error` | `{code, text, message}`: `needs_configuration`, `limit`, `rate`, `steps`, `too_long`, `model_error`, `timeout`, `stopped` or `interrupted`, a fixed sentence and the stored bot message |
| `running` | `{}`: a replay of a request whose turn is still running; read the thread later |

Before the stream starts, errors are plain HTTP `{error, code}`: 400 `invalid`, 404 for an
unknown or foreign bot, 409 `busy` while another turn of this bot runs, 409 `conflict` for a
request id sent again with other text, 429 `busy` when the person already has 4 turns running
or the process 32. Once the message is stored, every outcome is a stream event and a stored
bot message.

**A turn.** The model is the one the owner configured in chat settings for the bot's project
(the workspace setting for a bot without one). Without a usable model the reply is a stored
`setup` block and `error needs_configuration`; no request is sent. The model reads a fixed
system prompt with the bot's name, job and persona notes (quoted; they grant nothing), up to 20
memory notes, a short snapshot of the bot's project, and the last 20 messages, all redacted and
quoted as data. A turn takes at most 6 tool steps, 30 seconds per model request, 60 seconds in
total, 800 output tokens per request and 48,000 input characters; a turn that needs more tool
steps ends with `error steps`.

**Input budget.** Every model request fits one budget of 48,000 characters, measured the way
the transport measures it (system prompt, tool schemas and all messages). The system prompt,
the tool schemas, the new message and the turn's own tool rounds always go in; then, while
they fit, memory notes newest first (whole notes only; the model is told how many were left
out), the project snapshot, and earlier messages newest first. A message that cannot fit even
alone is refused before any request with `error too_long`. Each tool result fed back is cut to
4,000 characters, and later requests of a turn drop older context first to stay in budget.

The reply is stored in one
write, redacted, with the model label and a state: `ok`, `stopped` or `failed:<code>`. Partial
text is kept when a turn stops or fails. Provider error text never reaches the person.

**Streamed text.** The reply so far is redacted as a whole each time text arrives, and only
the part that can no longer change is sent: whole words, and a line that carries a label a
secret may follow (a header, a key with `:` or `=`, a quoted value, a key block) only once the
line is complete. So a secret split across chunks never reaches the client unredacted, and at
the end the client normally holds exactly the stored reply text. Should redaction ever change
text that was already sent, the stream sends no further text for that turn (the hub logs
`code=redaction_shift`). The reply the client shows is always `done.message.text` (or
`error.message.text`), which is the stored reply; deltas are a preview of it.

**Replay.** Sending the same `request_id` with the same text again never runs anything twice:
a finished request answers `ack` and its stored `done` or `error`; a running one answers `ack`
and `running`. A running turn holds a lease in the workspace store that it renews every 5
seconds, so a turn running in another hub process counts as running too. A request that has a
stored message but no reply and no running turn (no fresh lease for 20 seconds, for example
because the hub restarted mid-turn) is answered with `error interrupted` and a stored `failed:interrupted` reply listing what the
turn had journaled; an action that had started but not finished reads "Outcome unknown". The
same report is written when the thread is next read. It is never rerun. Should a turn still
finish after its request was closed this way, its reply is not stored a second time: the
stream reports the stored outcome and the hub logs `code=late_reply`.

**A dropped client.** The turn does not depend on the connection. A client that disconnects,
or a socket write blocked for 10 seconds, only ends the relay; the reply is stored and shows
when the thread is read again.

**Stop.** `POST /api/bots/<id>/stop {request_id}` ends that turn. A tool call in flight
finishes first (a write is never cut off); the partial text is kept and stored with state
`stopped`.

**Limits.** Bots and project chat share one model budget. One unit is one model request sent;
a bot takes its unit just before sending, in one step with the daily check (so concurrent turns
cannot pass the limit together), and gives it back when the request never went out. Bots count
theirs per person and day, and project chat's daily count includes them, so both
draw on the same workspace `daily_limit` and per-person `user_daily_limit`, and on the same
20-requests-per-minute rate per person. A used-up budget is `error limit` (or `rate`).

**Tools.** The model may call the tools offered for the turn. Arguments are checked against
each tool's schema first. Every tool runs as the person, re-resolved for each call, so what
the person may not do the bot cannot do either; each write gets an audit row like a REST write
(tool `bot:<name>`, no text). Text a tool writes must be a verbatim part of the person's
current message, at least 2 characters after trimming, or the tool refuses with
`not_your_words`. Every call is journaled before it
runs and after it finishes; its result goes back to the model as quoted data. Built in:
`remember(text)` saves a memory note (the person's own words) and `forget(id)` removes one.

**Confirm.** A consequential tool never runs from a model's tool call. The turn stores a
pending action with its arguments frozen (including the item's `version` when the call names
an item by `ref`) and a label the server builds, valid for 10 minutes, and shows a `confirm`
block. When the item named by `ref` cannot be read, nothing is created and the call is refused
(`unreadable`). `POST /api/bots/<id>/confirm {pending, accept:true}` moves it from `open` to `done`
once and runs exactly the stored action as the person; the result is an `event` message with
the action block. `accept:false` cancels it. A second confirm is 409 `conflict`, an expired one
409 `expired`, another person's 404. The tool's `check` runs before the action is consumed:
when it refuses for a reason the person can fix (the item changed), the event message says so
and the same chip can be tapped again; text that is not the person's words closes it. Only an
attempted action is consumed, by the compare-and-set, so it still runs at most once. A confirm tool receives the frozen `version` among its
arguments.

**Writing a tool.** `commonplace/bots/tools.py` defines `Tool` (name, description, a closed
JSON Schema, kind `read` / `routine` / `confirm`, `run`, `label`, scope, `available`),
`Result` and `register()`. An optional `check(turn, args)` (the last field) runs after
argument validation and before anything is labelled, stored or written: a confirm tool that
writes the person's text calls `turn.words()` there, so model text is refused before a
pending action or chip exists. `/confirm` rebuilds the turn with the message that asked for
the action (even when newer messages exist) and calls `check` again before `run`; a routine
tool's `check` also runs before `run`. A confirm tool whose schema takes free text (a string
without an `enum` or `const`, including type lists, `anyOf`/`oneOf` branches, untyped values and open nested objects) must have `check`, or `register()` refuses it. A successful `Result` may
carry `say`, a plain outcome sentence that becomes the text of the confirmed action's event
message (redacted, at most 500 characters); without it the text is "Done.". A tool call
naming a tool that was not offered is refused with `invalid` and the turn goes on. A `project`-scope tool acts on the bot's own project; for a bot
without one the model must name a readable project in a `project` argument, and
`Turn.board(args)` returns it. Tool modules are listed in `bots.TOOL_MODULES` and imported at
install; a missing module is skipped.

## Project tools

A project's bot (or the General bot, when you name the project) can run the project for you
in plain language. You never open a board: the bot calls the same services you would, as you,
so it can do nothing you may not do yourself. When a service refuses, the bot says so in one
plain sentence with what to do next.

What the bot can do:

- **Look.** `project_status` (counts by status, who is working, what waits and why, whether the
  orchestrator is connected and woken), `list_work` (up to 20 tasks, filtered by status),
  `work_detail` (one task with its latest notes and links) and `list_decisions` (the open
  questions waiting for you).
- **File and order work.** `create_work` files your message as a new task, `prioritize_work`
  changes a task's priority (project owner only) or moves it before or after another task, and
  `comment_work` adds a comment.
- **Steer.** `tell_orchestrator` passes your words to the project's orchestrator and says
  honestly whether it is connected and woken, or will only see them when it next checks. New
  work goes through `create_work` only, never also forwarded. One message that does both uses
  the span arguments: one part is filed, another is forwarded.
- **Answer and approve, with one tap.** `answer_decision` (an offered option, or your own
  reply), `approve_work` and `create_project` never run from the bot's own call. The bot shows
  a confirm chip whose text the server builds from the frozen arguments; your tap runs exactly
  that action once. An answer or approval is tied to the item's version: if it changed after
  the chip was shown, the tap is refused and the bot asks you to look again.

**Your words only.** Every text a writing tool uses (a title, body, comment, forwarded
message, decision reply or project name) must be copied verbatim from your current message,
or the tool refuses (`not_your_words`) and nothing is written. Without arguments the whole
message is used (the title is its first non-blank line, cut to 200 characters). The bot
chooses which tool, which item and which part of your message, never what gets written, so
text it read in other people's work cannot end up in a task, the project chat or an answer.

Filing work is routine: on a project whose owner configured starts for new tasks, a filed
task starts an agent exactly as when you file it by hand. Each filed task and each forwarded
post is remembered for the bot (`bot_work`, `bot_forwards`) so later slices can tell you when
it needs you or is done. A post forwarded by a bot is made inside `chat.front.skipping()`, so
project chat adds no receipt, answer or fallback card for it.

A new project is created through the same service the web app uses, with its permission
rules. The project's own bot appears in the roster on the next read.

## Machines

A bot can list the machines its person may use and start or cancel an agent on one. It does
this as the person, through the same checks the runner applies to a person who starts an
agent from a task: nothing is widened. Today that means workspace owners; a member gets an
empty list and a short reason.

- **What is listed.** Enrolled machines that are switched on and not revoked: the machine's
  name, when it was last seen (empty if no contact is recorded), what it may do (start an
  agent; run a command) and what the owner granted it (projects, topics, areas, the highest
  run mode). No credential, token or host name is ever shown. A machine that has not been
  attested yet is listed as `unbound` and cannot start anything.
- **Attested, not verified.** `owner_attested` means the workspace owner confirmed the machine
  and its setup. Commonplace does not check the machine itself, so every machine carries
  `verified: false`.
- **Starting takes one tap.** The bot has three machine tools: `list_machines`, `start_work`
  and `stop_work`. Starting an agent is a confirmed action: the bot proposes it with a button
  whose text the server builds (`Start an agent on “<task>”`, plus the machine's name when one
  is named) and nothing starts until the person taps. Asking twice with the same request key
  creates one start. When no machine is named and only one can take the task, that one is
  used; when several can, the bot asks which. `stop_work` cancels a start that has not
  launched yet, also behind a tap; an agent that has launched cannot be stopped from here, and
  the bot says so. While a start is in flight the task's work block reads `working`.
- **Refusals** keep the runner's stable code (for example `not_todo`, `outside_worker_wing`,
  `active_start`) and come with a plain sentence saying what to change.
  A start the runner would refuse is refused before any button is shown (only owners start
  agents, the task's state, the machine's area, a named machine that cannot take the task),
  and checked again at the tap.
- **Running commands is not available** until a provider offers it. The `run_command` tool is
  only offered to the model while some machine reports that it can run commands; without one
  the bot says so.
- **Other providers.** `register_provider` adds a provider; machines from all providers are
  merged and each machine states its own capabilities.

## Updates between turns

When something happens to work a bot filed, or the project's orchestrator answers a message a
bot forwarded, the person sees it in that bot's thread without asking: one short message with
the role `event`, and the roster follows (`unread` counts it, `state` changes).

| Happens | The thread gets | Blocks |
|---|---|---|
| A question for the person on a task the bot filed | `A question about “<title>” needs you.` | `decision` |
| The task is ready for review | `“<title>” is ready for you to look at.` | `work` |
| The task is done | `“<title>” is done.` | `work` |
| The orchestrator replies to a post the bot forwarded | the reply, word for word | `orchestrator` |

- Only the bot that filed the task or forwarded the post gets the message. Other bots, and
  other people's bots, get nothing.
- Only while the person can still see the item: their access is checked again for every
  event, with the same rules as project chat. No access, no message.
- Exactly once: a task posts when its status changes into one of the three above, so the
  same event twice, two events for one change, or events handled out of order add one
  message each, in order. A forwarded post is
  answered by the first reply from the project's orchestrator after it (the rule project
  chat uses); later replies are not attached to it.
- A title is other people's text: it is redacted and shortened for the message and never
  interpreted. The `orchestrator` block lets a client label the message as the
  orchestrator's own words; display redaction applies as for every message.
- `state` on a bot is `needs_you` while a question waits for the person, else `working` while
  a task it filed is in progress, else `idle`.
- Not covered: a start that fails to launch. The hub emits no event for it, and nothing here
  polls, so the bot says nothing until the person asks.

## Privacy and storage

- Threads live in the workspace file `bots.sqlite3`, next to the other workspace stores, and
  are encrypted with the workspace when encryption is on. Nothing from a conversation goes
  into the control database, logs, audit reasons or error messages.
- Nobody reads another person's bot, the workspace owner included.
- Messages are stored exactly as typed and never changed or deleted (the store refuses both).
  Each user message has one reply at most, and a repeated request id with the same text
  returns the stored message instead of a second one. Display text is redacted; the stored
  text is not.
- A memory note is removed by blanking it: it disappears from the list, the row stays so ids keep
  working, and its text is gone from the file.
- Sharing a bot with other members (design: `docs/design/shared-team-bots.md`) is being built
  in slices. The storage for it is in place and unused: a bot that is not shared works as
  above. Sharing is off for every workspace until its owner turns it on, a share reaches
  nothing until the bot's owner grants it something, and a member never reaches another
  member's private thread.
- What a shared bot may reach (slice S2): a turn of a shared bot acts with the bot's access
  list, the talking member's own rights and the owner's rights, all intersected and read afresh
  on every call. The list holds `project` entries (`read` or `write`), `machine` entries (a
  machine id) and `tool` entries (a tool name, always needed for a tool other than `remember`,
  `forget` and `my_access`); `wing` entries are refused (`unsupported_kind`) until a tool reads
  wings. A `read` grant never lets the bot write. A project the asker cannot read is "can't
  find that", one the asker reads but the bot may not use is refused with a fixed sentence that
  names nothing. Only the bot's owner edits the list (`403 owner_only` for a member, 404 for
  anyone else and for an app or agent token), only with something the owner reaches
  themselves (`403 outside_your_scope`), by compare-and-set on `version`; each real change writes
  one log row per entry and one control-database audit row (kind, ref and mode only). Members
  read the list and the log. A bot that is not shared ignores the list. Machines and tools are
  listed only within the list, and the runner's own rule still decides who may start an agent.
- Sharing and members' private threads (slice S3): the owner of a custom bot shares it with
  chosen workspace members (`talk`: open their own private thread and write in it; `see_shared`:
  read shared threads, which are not available yet) and revokes at any time. Sharing is off
  until a workspace owner switches it on (`PATCH /api/bots/settings`); while it is off a share
  is refused and members get 404. A member sees the bot in their roster with its owner and
  talks to it in their own private thread, which no one else reaches: not the bot's owner, not
  a workspace owner, not another member (404 on every route). Revoking takes effect on the
  next request and stops a turn in flight: no tool runs after it, and the stored reply is a
  fixed sentence. A member's memory notes are theirs; the owner's notes are bot-level and every
  turn reads them. A task a member's turn filed, or a post it forwarded, reports to that
  member's thread. Each shared bot runs one turn per thread and at most four at once; a turn
  counts against the talker's own daily budget.
- Bots use the model the owner configured in chat settings. Without a usable model there is
  no canned answer.

## Using bots from your own app

A native or mobile app of yours can use your bots as you, through its own connection and two
scopes that you grant yourself:

- `bots:chat` lets the app use `/api/bots*` as you: the roster, threads, memory, sending a
  message, stopping a reply and marking a thread read. It reaches only your own bots, with only
  your own permissions (a member's app can do what a member can, nothing more).
- `bots:confirm` additionally lets the app confirm what a bot proposes
  (`POST /api/bots/<id>/confirm`), the same as tapping the confirm button yourself. It is
  useless without `bots:chat` and is granted separately. Without it that route answers 403
  `scope_required` and nothing runs. Buttons on a question use the separate `cards:decide`
  scope described in the connections guide.

**How to grant.** Any signed-in member can do it for their own app. Owners: Settings → Agents,
**Allow using my bots** on the app's row (and **Allow confirming** if you want that too), or
`PATCH /api/connections/<id>` with `{"bots_chat": true}` and optionally `{"bots_confirm": true}`.
An app that signs in with OAuth asks for the scopes and you tick the boxes on its authorization
form; an unticked box grants nothing. Members use the same PATCH for their own connection, or
the authorization form.

**What never qualifies.** Child agents, runner connections, project orchestrator seats, board
coordinators, connections that belong to someone else, and revoked or expired connections. They
are refused when granting and again on every request, even if a stored scope claims otherwise;
they get the same 404 as any other token. Assigning an app as a board coordinator removes its
grants. The command line cannot grant these scopes.

**How to revoke.** **Stop using bots** (or `{"bots_chat": false}`, which withdraws confirming as
well), or revoke the connection. It takes effect on the next request: a withdrawn scope answers
404 and a revoked or expired connection 401.

**Limits of this release.** There is no member-scoped list of connections, so a member grants
through the OAuth authorization form or by `PATCH` on their own connection (the web Settings →
Agents list is for owners). There is no create-time option on `POST /api/connections`: create the
connection, then grant.

**Audit.** Granting and withdrawing each scope is recorded with the connection. Every write the
app makes (creating or renaming a bot, memory, sending, stopping, read marks, confirming) adds
one record with the connection id, the route and the outcome (`ok`, `refused` or `error`; a refused
confirm is recorded too); reads add none, and
no message text is ever recorded.

## Using bots in the web app

This part describes what the screens do; the server side is in [Routes](#routes),
[Blocks](#blocks) and [Privacy and storage](#privacy-and-storage).

**Bots** is where you land after signing in (until a first project exists, the first-run screen
comes first). It is the first entry in the left rail and the first tab on a phone; **Memory** is
the only other primary entry. The number next to Bots counts the questions waiting on you.

**Where the rest went.** Projects, Needs me and Console sit under **Workspace** in the account
menu (your name at the bottom of the rail, the menu button at the top left on a phone), next to
Rules, Workspace settings and, for owners, Admin. Nothing was removed: every project, task,
chat, Needs me and Console link keeps working, and the project chat dock still opens on a
project screen.

Every bot you can talk to is listed:
one per project, a General bot, and any you made yourself. The list is in the order the server
gives it, most recently used first.

**The roster.** Each row shows the bot's initial, its name, the last line of the conversation (or
its job before you have said anything), how long ago that was, and the number of messages you have
not seen. One dot at the right says what needs attention: a solid accent dot means something is
waiting on you, a green pulsing dot means work is running. No dot means nothing is going on.
**New bot** asks for a name, what the bot is for, and an optional note on how it should work.

**A thread.** Tap a bot to open its conversation (on a phone it takes the whole screen; the back
arrow returns to the list). Type and press Enter to send; Shift+Enter starts a new line. Your
message appears at once and the bot's reply fills in as it is written. While the bot is answering,
Send turns into **Stop**; stopping keeps what was already written.

Inside the reply you may see:

- a small grey line of **actions** ("Filed “Fix login”", "Told the orchestrator"); several actions
  share one line, and a refused one is shown in red;
- a **work** block with the task's title and a status chip (Queued, Working, Needs you, Ready,
  Blocked, Done). Tap it to read the latest note and open its links;
- a **question** with a button for each answer and a field for an answer in your own words. Your
  tap is your own answer and goes straight to the task;
- a **confirmation** for the few actions that need your tap before they run. Confirm runs what
  the line says; Cancel drops it;
- a **message from the orchestrator**, labelled "Orchestrator", when the project's orchestrator
  replies to something the bot forwarded (see [Updates between turns](#updates-between-turns));
- a **setup** note when no chat model can answer yet. Workspace owners get a link to the settings;
  members are told to ask the owner.

Above the message box a single line, **Waiting on you (n)**, collects every open question for this
bot so you never have to scroll for them. Tap it to answer from there.

**Memory and settings.** Tap the bot's name above the thread. The sheet lets you rename the bot,
change its job and its working note, read what it remembers, add a note or remove one, and archive
the bot. Notes are kept word for word.

**If a message does not go through.** Your text stays in the box and a line says why (the bot is
still busy, too many messages, no connection). Press Send again: the same message is sent under
the same id (see [Routes](#routes) for what the server does with a repeated message). If the
connection drops while the bot is answering, the page keeps checking and shows the reply when it
is stored.

**Refresh rhythm.** The list refreshes every 15 seconds and an open thread every 5 seconds, faster
while a reply is being written. Nothing refreshes while the tab is hidden.
