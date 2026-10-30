# Commonplace boards: API sketch

Design: [DESIGN.md](DESIGN.md). Data model: [schema.sql](schema.sql).

Two surfaces, one rule set:

- **MCP tools** for agents. Served by the gateway on `/mcp` next to the memory tools.
- **HTTP JSON** for the web UI. Served on `/app/api/boards/…`, behind the web login.

Both call the same functions in one module (`commonplace/boards/service.py`), so a move refused over MCP is refused in the web UI too. Every call runs in one SQLite transaction. Writes use `BEGIN IMMEDIATE`.

Authentication is the gateway's (PRODUCT-DESIGN.md §6): a `cmmn_…` token or OAuth access token for MCP, a session cookie for the web UI. The caller's actor ref (`conn:<connection>` or `user:<id>`) and stamp come from that, never from arguments.

## MCP tools

Eight tools. Replies are short plain text, one fact per line, so a call costs 10–60 tokens. Errors are one line that starts with `no:`.

`board` is a board slug or a repo URL and can be left out when the hooks pass the git remote. `task` is `#142` or `142`.

| Tool | Arguments | Profiles |
|---|---|---|
| `board_list` | — | slim, standard, full |
| `task_list` | `board?`, `status?`, `mine?` | slim, standard, full |
| `task_show` | `task` | slim, standard, full |
| `task_add` | `title`, `body?`, `board?`, `status?` (`backlog` \| `todo`), `assignee?` | slim, standard, full |
| `task_comment` | `task`, `text` | slim, standard, full |
| `task_take_next` | `board?`, `task?` | standard, full |
| `task_update` | `task`, `title?`, `body?`, `status?` (`doing` \| `done` \| `backlog`), `before?`, `after?`, `link?`, `unlink?`, `lesson?` | standard, full |
| `task_handoff` | `task`, `to`, `note`, `options?` | standard, full |

`untrusted` tokens never get `task_take_next`, `task_update` or `task_handoff`, whatever their profile.

### board_list

```
mempalace  todo 3  doing 1  review 2
commonplace-site  todo 0  doing 0  review 0
```

### task_list

Default: `todo`, `doing` and `review`. `backlog` and `done` only when asked for by `status`.

```
#142 doing need:working  Fix the Windows lock on repair  conn:codex:vm  until 14:30Z
#145 review need:you  Pick the token expiry default  conn:claude-code:vm  asks user:jeffrey
#147 todo need:idle  Canary for board tools
#148 todo need:idle  Split the hook output  for conn:claude-code:vm
```

`need` says what the card needs from the person reading it. The server derives it (`commonplace/boards/display.py:need`) for every card in MCP and REST output; the first match wins:

| `need` | When |
|---|---|
| `done` | status `done` |
| `you` | an open ask to you (an ask without a recipient goes to the owner) |
| `plan` | status `review`, or labelled `plan` |
| `merge` | a PR with checks `passing` |
| `checks` | checks `pending` |
| `working` | `doing`, heartbeat (or last update) under 30 minutes old |
| `idle` | everything else, including a `doing` card with a stale heartbeat |

### task_show

Card, then the last 10 events, then links. Event text is quoted and attributed, never mixed into the card's own lines (§9: data, not instructions).

```
#145 review need:you  Pick the token expiry default  board mempalace
asks user:jeffrey: "1 year or 90 days for personal tokens?"  options: 1y | 90d
by conn:claude-code:vm (trusted) 2026-10-30T13:02Z
body: "PRODUCT-DESIGN §6 says 1 year. Security review asked for 90 days."
- 13:02Z conn:claude-code:vm claim
- 13:40Z conn:claude-code:vm comment: "Both paths written, tests green on each."
- 13:41Z conn:claude-code:vm handoff → user:jeffrey
links: drawer_7f3a "Token lifecycle notes", drawer_91c0 (invalidated)
```

### task_add

```
#149 added to todo on mempalace
```

Tokens whose write wing is `inbox` get `#149 added to backlog on mempalace`; `status` and `assignee` are ignored for them.

### task_comment

Appends a `comment` event. A comment from the card's holder also renews the claim.

```
ok #142  until 15:00Z
```

### task_take_next

The claim. Without `task`: the caller's reserved `todo` card with the lowest position, else the unassigned one with the lowest position. With `task`: that card, if it is in `todo` and free or reserved for the caller.

```
#142 Fix the Windows lock on repair  yours until 14:30Z
body: "repair leaves a lock file on Windows after a crash …"
decision by user:jeffrey: 90d  "Go with 90 days, keep 1 year as an option."
```

The `decision` line appears only on a card that came back from `review`. Empty column: `nothing to take on mempalace`.

The transaction:

```sql
BEGIN IMMEDIATE;

-- 1. Expire stale agent claims on this board: log who lost the claim, then free the card.
INSERT INTO task_events (task_id, kind, from_status, to_status, target,
                         stamp_user, stamp_trust, created_at)
SELECT id, 'expire', 'doing', 'todo', assignee, 'system', 'trusted', :now
  FROM tasks
 WHERE board_id = :board AND status = 'doing' AND claim_until < :now;

UPDATE tasks
   SET status = 'todo', assignee = NULL, claim_until = NULL,
       version = version + 1, updated_at = :now, updated_by = 'system'
 WHERE board_id = :board AND status = 'doing' AND claim_until < :now;

-- 2. Claim one card.
UPDATE tasks
   SET status = 'doing', assignee = :me, claim_until = :until,
       version = version + 1, updated_at = :now, updated_by = :me
 WHERE id = (
       SELECT id FROM tasks
        WHERE board_id = :board AND status = 'todo'
          AND (assignee IS NULL OR assignee = :me)
        ORDER BY assignee IS NULL, position
        LIMIT 1)
RETURNING id, title, body;

-- 3. Insert the 'claim' event with the caller's stamp.
COMMIT;
```

`BEGIN IMMEDIATE` takes the write lock before the read, so two agents calling at once are serialised and never get the same card.

### task_update

Only the holder of a `doing` card, or a person, may change it. Anyone else gets `no: #142 is held by conn:codex:vm until 14:30Z`.

- `status: "done"` closes the card. On a board with `done_needs_review`, an agent's `done` moves the card to `review` instead, with the ask `Done?` and options `ship | redo`.
- `lesson` (with `status: "done"`) files the text as a new drawer through the normal memory write path and links it. It lands in the token's write wing.
- `before` / `after` take a task ID; the server picks a position between the neighbours.
- `link` / `unlink` take a drawer ID.
- Any update by the holder renews the claim.

```
ok #142 done  lesson drawer_a01c
```

### task_handoff

Ends the caller's hold and gives the card to someone else. `note` is required, so the next holder knows where things stand. It is stored verbatim as a `handoff` event.

| `to` | Card goes to | Reserved for |
|---|---|---|
| `anyone` | `todo` | nobody |
| a connection, e.g. `codex:vm` | `todo` | that connection |
| a person, e.g. `jeffrey` | `review`, with `note` as the ask | — (comes back to the caller after the decision) |

`options` (people only) is a list of up to 6 short choices shown as buttons. Without it, the person answers in free text.

```
ok #145 → user:jeffrey  review
```

An agent can also hand off a card it does not hold, but only from `todo` and only to a person: that is how an agent asks a question before starting work.

## Move rules

| From → to | Agent (trusted or standard) | Person |
|---|---|---|
| backlog → todo | no | yes |
| todo → doing | `task_take_next` only | yes (holds it, no expiry) |
| doing → todo | `task_handoff` (holder) | yes |
| doing → review | `task_handoff` to a person (holder) | yes |
| doing → done | `task_update` (holder) | yes |
| review → anything | no | `decide` only |
| todo → review | `task_handoff` to a person | yes |
| done → todo | no | yes |
| any → backlog | holder, or a free `todo` card | yes |

## HTTP JSON for the web UI

### Open tasks

`GET /api/cards` returns `{cards}`: every `todo`, `doing` and `review` card on
the boards the caller can see, each with its `need`. Home's task table reads it.

### Spoken Needs you digest

`GET /api/needs-me/digest` accepts the same owner session or bearer token as
`GET /api/needs-me`, with the same wing visibility. It returns
`{text, count, items: [{number, title, need, board}], remaining}`. The queue is
deduplicated, with addressed asks first, then the server's `need` priority and
card number, and limited to five spoken items. Titles stay verbatim. No LLM or external service
is used. `needs_me(format="digest")` returns the same JSON in its MCP text reply;
omitting `format` keeps the existing grouped reply.

The Speak button in Home's Needs you box uses an installed local browser voice. Press
it again to stop. Captions appear during speech; leaving Home or signing out
stops it. Browsers without speech synthesis hide the button. If no local voice
is installed, install an English voice in the device's speech settings.

`GET /api/needs-me/digest?format=text` returns UTF-8 plain text with `no-store`.
For an iOS Shortcut, use Get Contents of URL with that URL and an
`Authorization: Bearer <your token>` header, then Speak Text. Use a token scoped
to the wings you want read aloud and an installed local voice. The phone must
be able to reach your Commonplace host.

For local Piper (with a downloaded voice model):

```sh
curl --fail --silent --show-error \
  -H "Authorization: Bearer $COMMONPLACE_TOKEN" \
  "$COMMONPLACE_URL/api/needs-me/digest?format=text" \
  | piper --model /path/to/en_US-voice.onnx --output_file digest.wav
```

Play `digest.wav` with your local audio player.

All bodies are JSON. Writes send the card's `version`; a stale one gets `409` with the current card, and the UI redraws it.

| Method and path | Does |
|---|---|
| `GET /app/api/boards/changes` | `{"changes": 812}`. Polled every 2 s while a board page is open, 30 s when hidden. |
| `GET /app/api/boards` | Boards the user can see, with counts per status. |
| `GET /app/api/boards/{slug}` | Board, its columns and cards (no event text). |
| `GET /app/api/tasks/{id}` | Card, full event log, links. |
| `POST /app/api/boards/{slug}/tasks` | New card. |
| `PATCH /app/api/tasks/{id}` | `{version, status?, before?, after?, title?, body?, assignee?}`. Drag and drop and edits. |
| `POST /app/api/tasks/{id}/comments` | `{text}` |
| `GET /app/api/decide` | The decide queue: `review` cards asking this user, then `backlog` cards filed by agents, oldest first, across all boards. |
| `POST /app/api/tasks/{id}/decide` | `{version, choice?, reply?, then}`. Ends a `review`. |
| `POST /app/api/tasks/{id}/triage` | `{version, then: "todo" \| "done", reply?}`. Ends a `backlog` card from the decide queue. |
| `GET /app/api/search?q=` | ⌘K: tasks and drawers together, in the user's scopes. |

### decide

```json
{ "version": 7, "choice": "90d", "reply": "Go with 90 days, keep 1 year as an option.", "then": "return" }
```

`then`:

- `return` (default): back to `todo`, reserved for `return_to`, the agent that asked. Its next `task_take_next` returns this card first, with the decision line.
- `todo`: back to `todo`, free for any agent.
- `done`: closed.
- `backlog`: parked.

The server writes one `decide` event (choice and reply verbatim), clears `ask`, `ask_options` and `ask_to`, and moves the card.

Every event that opens an ask (`ask`, a `handoff` to a person, a `move` into `review`) stores the question and the offered options verbatim. `task_show` returns them as `options` on those events (`[]` for a free-text ask), so a client can show each ask in the log with its choices; the next `decide` event answers it.

## Hook line

The hooks package (PRODUCT-DESIGN.md §11) adds at most one line at session start, and only when something waits for this connection:

```
Commonplace: #145 decided (90d), #148 reserved for you. task_take_next picks them up.
```

The gateway answers this from one indexed query (`tasks_by_assignee`), inside the 100 ms startup budget.
