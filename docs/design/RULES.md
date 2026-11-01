# Rules

Every connected agent publishes the rules it follows: standing instructions, permissions, hard limits and style. The owner reads them in one place, edits or clarifies them, and accepts or retires each one. Agents read the current set, including the owner's edits, at the start of a session.

## Decisions

- **Owner edits win.** An owner's edit takes effect at once. If the agent later republishes its own wording, that wording is kept as a proposal next to the rule. It never overwrites the edit, and the owner decides.
- **Clarify path B is desktop only.** The web page asks the agent only through the desktop bridge (below). Without the bridge it offers path A, copy the prompt. There is no server relay.
- **Own store, same pattern as boards.** Rules need a status, a version history and a scope per item, and none of that fits a Memory drawer. They live in `rules.sqlite3` next to `boards.sqlite3` in each workspace's palace directory. The file uses the board conventions: server stamps from the verified token, an append-only history, verbatim text and a change counter. Agents get the rule set back as Markdown (`format=md`), ready to drop into a skill or instructions file. That covers the skills-as-.md model without a second copy on disk.

## Data model

```sql
-- rules.sqlite3, one per workspace
CREATE TABLE meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);  -- 'seq': bumped on every rule change
CREATE TABLE rules (
  id          INTEGER PRIMARY KEY,
  agent       TEXT NOT NULL,         -- who the rule applies to: actor ref "conn:<name>", or "*" for every agent
  origin_agent TEXT,                 -- the agent the rule first applied to (NULL for "*" until it first moves to an agent)
  agent_text_by TEXT,                -- the agent that wrote agent_text (a moved rule can hold another agent's wording)
  key         TEXT NOT NULL,         -- stable per agent; matches [A-Za-z0-9._-]{1,100}, else derived from the text
  text        TEXT NOT NULL,         -- effective text: what the agent must follow
  agent_text  TEXT,                  -- the agent's latest published wording (NULL for owner-written rules)
  kind        TEXT NOT NULL CHECK (kind IN ('permission','limit','instruction','style')),
  scope       TEXT NOT NULL CHECK (scope IN ('global','board','repo')),
  scope_ref   TEXT,                  -- board slug, or normalised repo "host/owner/name"; NULL for global
  status      TEXT NOT NULL CHECK (status IN ('proposed','accepted','edited','retired')),
  source      TEXT NOT NULL CHECK (source IN ('agent','owner')),  -- who wrote `text`
  pending     INTEGER NOT NULL DEFAULT 0,  -- 1: the agent republished wording that differs from an owner edit
  retired_by  TEXT CHECK (retired_by IN ('agent','owner')), -- NULL unless retired
  version     INTEGER NOT NULL DEFAULT 1,
  seq         INTEGER NOT NULL,      -- meta.seq at the last change
  created_at  TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL,
  UNIQUE (agent, key)
);
CREATE TABLE rule_versions (        -- append-only (triggers refuse UPDATE and DELETE)
  id INTEGER PRIMARY KEY, rule_id INTEGER NOT NULL REFERENCES rules(id),
  version INTEGER NOT NULL, agent TEXT, -- who the version applied to; NULL for versions written before this column
  text TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL, scope_ref TEXT,
  status TEXT NOT NULL, source TEXT NOT NULL CHECK (source IN ('agent','owner','suggestion')),
  note TEXT,                          -- the owner's reason or the agent's change note, verbatim
  stamp_user TEXT NOT NULL, stamp_connection TEXT, stamp_trust TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE rule_suggestions (     -- clarify replies brought back through the desktop bridge
  id INTEGER PRIMARY KEY, rule_id INTEGER NOT NULL REFERENCES rules(id),
  text TEXT NOT NULL, from_agent TEXT NOT NULL,       -- the connection the owner asked
  base_version INTEGER NOT NULL,                     -- rule version when suggested
  status TEXT NOT NULL CHECK (status IN ('open','accepted','dismissed')),
  stamp_user TEXT NOT NULL, created_at TEXT NOT NULL, decided_at TEXT
);
```

Rules are never deleted. Retiring keeps the row and its history.

### Status flow

| Event | Result |
|---|---|
| Agent publishes a new key | `proposed`, `source=agent`, `text=agent_text`. The default read includes it. It enters the enforcement view only after owner approval. "Proposed" means the owner hasn't reviewed it yet. |
| Owner accepts a `proposed` rule | `accepted`; other states are refused |
| Owner edits the text, kind or scope | Requires an active rule; retired rules return 400 "restore the rule first". Sets `edited`, `source=owner`. `agent_text` stays, so the UI can show the diff. |
| Agent republishes an unchanged rule | On `source=agent`, a full no-op requires matching text, kind, scope and scope reference. On `source=owner` or an owner-retired rule, matching `agent_text` is enough; pending stays as it is. No version, sequence or history change. An agent-retired key revives as below. |
| Agent republishes changed text, kind or scope on an active rule | If `source=agent`: updates the fields, returns to `proposed` and bumps the version. If `source=owner`: only changed wording updates `agent_text`; `pending=0` when it agrees with the owner's text, otherwise `pending=1`. Agent kind and scope changes are ignored: the owner's classification wins. |
| Owner takes the agent's wording (`accept_agent`, requires `pending=1`) | `text=agent_text`, `source=agent`, `accepted`, `pending=0`, `retired_by=NULL`; also restores an owner-retired rule |
| Owner keeps the edit (`keep_edit`, requires `pending=1`) | `pending=0`; retirement, if any, stays in force. Repeating that agent wording remains a no-op. |
| Agent publishes with `replace=true` and leaves out an active `source=agent` key | `retired`, `retired_by=agent` (note: "dropped by agent"). Never retires owner edits or owner-retired rules. |
| Agent republishes an agent-retired key | Revives as `proposed`, `source=agent`, `text=agent_text`, `pending=0`, `retired_by=NULL`, even for identical wording |
| Owner changes who a rule applies to (`set_agent`) | Moves it to `*` or another `conn:<name>`. Text, status, source and key stay; the version bumps and history gets the note `Applies to: <old> → <new>`, followed by the owner's note if given. Retired rules return 400 "restore the rule first"; the current target returns 400 "nothing to change". When the target already has a rule with the same key (retired ones included, since keys are unique per agent), it returns 400 "<target> already has a rule with this key" and changes nothing: there is no merge, the owner retires or edits one of the two. With `accept=true` it also accepts a `proposed` rule in the same version ("Accept for all agents"); other states are refused. A `*` rule that moves to `conn:x` for the first time gets `origin_agent=x`, so x's later publishes keep finding it after it moves back. |
| Agent republishes a key the owner moved away | The publish still finds the rule through `origin_agent`, so no second proposal appears under the agent. If the rule now applies to `*` and the agent can see it, it is handled like an owner edit: changed wording becomes `agent_text` with `pending=1`, kind and scope changes are ignored, and `replace=true` never retires it. If the agent can no longer see it (moved to another agent, or a board it cannot read), the key is skipped. |
| Agent publishes a key of a rule the owner moved to it from another agent | Handled like an owner edit too: its wording becomes `agent_text` with `pending=1`, kind and scope changes are ignored, and its `replace=true` never retires the rule, because the agent did not publish it first. |
| Owner retires a non-retired rule | `retired`, `retired_by=owner`. Agents stop following it. Retiring again is refused. |
| Agent republishes an owner-retired key | Stays retired. Different wording updates only `agent_text` and `pending`; it never restores the rule. A pending proposal appears in the active review list. |
| Owner restores a retired rule | When `retired_by=agent`, sets `accepted`, `source=owner`, `retired_by=NULL`: the owner explicitly keeps it, so agent replacement cannot retire it and later wording becomes a pending proposal. When `retired_by=owner`, restores `accepted`, or `edited` when `source=owner`, and clears `retired_by`. Other states are refused. |
| A suggestion is stored or dismissed | No rule, version, sequence or history change |
| A suggestion is accepted | Requires the supplied version and stored `base_version` to match the current rule. Stale versions return 409 with the current rule. Retired rules are refused. Otherwise applies an owner edit, with `source=suggestion` in history. |

Every actual rule change writes one `rule_versions` row and bumps `meta.seq`. An edit with unchanged text, kind and scope returns 400 "nothing to change". A publish transaction is refused atomically when its final set exceeds 500 non-retired rules or 2000 total rows per agent.

After the rules transaction closes, each changed agent emits `rule.changed`; a moved rule emits it for both the agent it left and the agent it moved to, so both refresh with `{agent, ids, keys}` and no rule text. These events carry rule keys and only reach receivers that can read every wing. No-op publishes emit nothing. Storing or dismissing a suggestion emits `rule.suggestion` with `{rule_id, suggestion_id, action}` (`stored` or `dismissed`) without moving the rule sequence. Suggestion acceptance emits `rule.changed`.

### Visibility

- An agent sees its own rules, plus the `*` rules, filtered by scope: a `board` rule only when it can read that board's wing, and a `repo` rule always (repos are not secret on their own).
- Agents see `agent_text`, `agent_text_by` and `pending` only when they wrote that `agent_text` themselves, and a `conn:` `updated_by` only when it is their own; otherwise these come back as `null`, `null`, `0` and `null`. A moved rule can hold another agent's wording, and one agent's unreviewed wording never reaches another agent. Republishing wording another agent wrote, word for word, records the publisher as its writer.
- People in the workspace see everything. Only the owner (`principal.role == "owner"`) can edit, accept, retire, restore, suggest or decide.

## MCP tools

All tools are registered from a new extension module, `commonplace.rules` (added to `extensions.MODULES`), and are in every profile. Agents cannot write any other agent's rules: the agent is always `principal.actor`, never an argument.

| Tool | Scope | Kind | Arguments | Reply |
|---|---|---|---|---|
| `rules_publish` | `memory:write` | write | `rules: [{key?, text, kind, scope?, board?, repo?}]` (max 200), `replace?: bool` (default false) | One line per rule (`key status version`), then `seq N`. With `replace=true` the call is a bulk replace: keys it leaves out are retired. |
| `rules_get` | `memory:read` | read | `board?`, `repo?`, `format?: "text" \| "md"`, `include_retired?: bool`, `effective?: bool` | The rules that apply to me: my own plus `*`, global plus the given board and repo, with `status`, `source`, `version` and the effective text. `format=md` gives a Markdown rules file grouped by kind, with retired rules left out. The last line is `seq N`. |
| `rules_changed` | `memory:read` | read | `since: int` | `seq N` plus `key status own\|all` lines for changes after `since` (`own` for the caller's rules, `all` for `*` rules). If `since` exceeds the current sequence, returns `reset` then `seq N`. It does no text work; meant to run on every session start. |

Untrusted tokens may publish: what they publish starts as a proposal for the owner, and nothing else is written. Rule text is stored verbatim, and replies quote it as data the way board tools do.

Agent loop, as written in the skill: at the start of a session call `rules_changed since=<last seq>`. Whenever `seq` advanced, call `rules_get` and follow `text`: owner edits win, and retired rules no longer apply. When your own rules change, call `rules_publish` with `replace=true`.

## REST (web app, cookie auth)

| Method | Path | Body | Who |
|---|---|---|---|
| GET | `/api/rules` | — | any person. Returns `{rules: [...], suggestions: [...open], agents: [...connection names], seq}`, with every rule from every agent, retired ones included. Connection names are returned only when `can_edit` is true; members get `agents: []`. Also returns `can_edit`, server-computed `waiting` and `waiting_by_agent`. |
| GET | `/api/rules/count` | — | any person. Returns only `{waiting, seq}` for badges outside the Rules screen. |
| GET | `/api/rules/:id/history` | — | any person. Returns the `rule_versions` rows, oldest first. |
| POST | `/api/rules` | `{agent: "*" or "conn:x", text, kind, scope, scope_ref?}` | owner. A rule written by the owner starts as `accepted` with `source=owner`. |
| PATCH | `/api/rules/:id` | `{version, text?, kind?, scope?, scope_ref?, note?}` | owner. Retired rules return 400 "restore the rule first", including unchanged edits. Active unchanged edits return 400 "nothing to change". Answers 409 with the current rule when `version` is stale. |
| POST | `/api/rules/:id/agent` | `{version, agent: "*" or "conn:x", note?, accept?}` | owner. Changes who the rule applies to; `accept=true` also accepts a proposal. 409 with the current rule when `version` is stale. There is no MCP tool for it. |
| POST | `/api/rules/:id/action` | `{version, action: accept\|retire\|restore\|accept_agent\|keep_edit}` | owner |
| POST | `/api/rules/:id/suggestions` | `{text, from_agent}` | owner. This is where the web page stores a clarify reply that came back through the bridge. |
| POST | `/api/rules/suggestions/:sid` | `{action: accept\|dismiss, version?}` | owner; `version` is required for accept |

Rule JSON: `{id, agent, key, text, agent_text, agent_text_by, kind, scope, scope_ref, status, source, pending, retired_by, version, seq, updated_at, updated_by}`.

## Owner UI

The UI uses look B: the tokens, Geist Mono, tiles on the 6px gap and the v2 block logo from `app.css`/`ui.css`. No new colours.

- **Nav:** "Rules" in the sidebar under Memory and in the phone tabs (`#/rules`). Its count is the number of rules that wait on the owner (`proposed`, or `pending=1`, excluding retired rows except owner-retired rows with pending proposals), shown the same way Home shows its count.
- **Screen:** the title is "Rules". There is one tile per agent (the connection name, with "All agents" for `*`), ordered by how many rules wait on the owner. The header row holds the agent's name, its waiting count and a "Help me clarify" button for the whole set.
- **Rule row:** a kind chip, a scope chip (only when it isn't global, e.g. `board commonplace`), the rule text and a status chip. Orange marks only what waits on the owner, the same as on the board. Proposed rows show Accept, Edit and Retire. Edited rows show the diff between the agent's wording and the owner's text: removed words struck through in the quiet step, added words in ink. Rows with `pending` show the agent's new wording as a diff (headed with the writing agent's name when the rule now applies to someone else) with "Use agent's" and "Keep mine". Owner-retired rows with pending proposals appear in the active list; other retired rows sit folded at the bottom of each tile.
- **Applies to:** active rows have an "Applies to…" menu that moves the rule to All agents or another agent's tile. Proposed rows also show "Accept for all agents" next to Accept. History shows the move as its note.
- **Inline edit:** the text turns into a textarea, with selects for kind and scope, then Save and Cancel. Nothing opens in a modal.
- **Help me clarify** (on a row and on a tile) opens a small inline panel:
  - **Copy prompt** (always there): copies the prompt below.
  - **Ask an agent** (only for owners when `window.commonplaceDesktop` exists): pick a connected agent and press Ask. While it waits the panel shows "Asking <agent>…". The reply is stored as a suggestion and shown under the rule as a diff with Accept and Dismiss. A suggestion whose `base_version` differs from the current rule version has an "outdated" status chip and only Dismiss. Retired rules also show their open suggestions with Dismiss only. For a whole set, the reply is parsed as `key: text` lines and becomes one suggestion per matching rule. A reply that can't be parsed is shown as text, with Copy.
- **Phone (390px):** tiles stack, chips wrap above the text, and actions are quiet text buttons that wrap.

People who are not owners see no write controls or Ask an agent; Copy prompt stays available. The shell fetches full rule data only on `#/rules`, uses `/api/rules/count` elsewhere, and refreshes the Rules screen and badge for `rule.*` events. Background renders wait while a form, history, retired list, Ask picker or reply waiting to be copied is open; closing the busy control refreshes any deferred changes. Schema initialization runs once per store path per process and repeats if the file was removed.

UI language: the app is English, so the step is called "Rules" and the button "Help me clarify". These are the English names for "Regels" and "Help me verduidelijken".

### Clarify prompt (path A and path B use the same text)

```
Rewrite this rule for an AI agent so it has exactly one reading.
Keep its meaning and its strength (must / may / never). Don't add new obligations.
Name the trigger, the action and any exception. One to three sentences.

Agent: <agent>    Kind: <kind>    Scope: <scope>[ <scope_ref>]
Rule (quoted, verbatim):
"""
<text>
"""

Return only the improved rule text, nothing else.
```

For a whole set, the prompt lists every rule that is not retired as `<key>: <text>`. It asks for the same kind of rewrite, plus a check for rules that conflict or overlap, and asks for the answer as `<key>: <improved text>` lines only.

The prompt is built in the browser (`rules.js`, a pure function with a node test). It never contains tokens, emails or hostnames.

## Desktop bridge contract (path B)

The Commonplace desktop app hosts the web app and injects this object before page scripts run:

```ts
window.commonplaceDesktop = {
  version: 1,
  // Connected agents the app can ask right now (local CLIs or threads).
  agents(): Promise<Array<{ id: string; name: string }>>,
  // Send the prompt to the agent and resolve with its final reply text.
  // Reject with an Error whose message the page shows as is.
  ask(request: { agent: string; prompt: string; purpose: "rule-clarify" }): Promise<{ text: string }>,
};
```

- The page checks `window.commonplaceDesktop?.version === 1` together with `typeof ask === "function"`. When either check fails, path B stays hidden. Without the bridge the web page offers path A only.
- The bridge runs the agent locally. The page sends only the prompt it built, and the reply comes back to it as text. The page then stores the reply with `POST /api/rules/:id/suggestions`, where `from_agent` is the name of the agent that was asked. Nothing is applied until the owner presses Accept.
- The hub has no endpoint for this and does no relaying.

## Enforcement

### Shared rules core

`rules_get effective=true` returns the connection's full authorized set of
accepted or edited rules, including owner edits and excluding retired rules.
It accepts only `format=text|md`; people use the Rules screen instead. Global
rules apply everywhere. Board rules require a readable, unarchived board.
Repo rules require a link to such a board; trusted connections also see rules
for repos with no board links. Private and workstation wings retain their
normal policy checks. The default `rules_get` selection remains unchanged.

The first line, `effective rules: N`, gives the total authorized count. Replies
quote rule text with JSON escaping, including controls, formatting characters,
`<`, `>`, `&` and `@` (`\u0040`). Scoped Markdown lines include their board or
repo label; labels outside the safe character set are quoted too. Publishing
or editing text that exceeds 6,000 bytes after escaping is refused. The whole
effective reply is capped at 24,000 UTF-8 bytes. Only whole rules fit; an
incomplete reply states how many are missing and ends with `rules_seq too_large`,
including when checking is off. It never proves the connection read the set.

The existing global `seq` remains the cursor for `rules_changed`. A separate
random nine-digit `rules_seq` combines the connection's token with the shared
`*` token. Effective changes bump affected targets in the same transaction;
proposals and pending agent wording do not. Fetching a complete effective reply
records `served` for that connection at that version. An over-cap reply records
`too_large` at its version. Default reads, change polls and publishing never
record served. All threads sharing one connection share its served state.

Board and repository mutations update a random visibility value through SQLite
triggers. Before a version read, reconciliation compares it with the last seen
value in the rules store. A difference bumps `*` and global `seq` once while
any scoped rule exists. This also covers archived boards and restored older
boards files. It deliberately makes every connection stale, including those
whose rules did not change. Private board creation therefore exposes a small
metadata signal through version changes, without revealing board names or text.
Future policy changes must also invalidate the affected rules versions.

Both stores carry stable random identities, bound in the control database.
Every rules path checks them before opening a store that could create a file.
The guard's own identity probes are read-only. A missing or replaced bound
store yields only `rules_seq store_lost`; publication is refused, and owner
routes return 409 with `store_lost: true`. `PATCH /api/rules/checks` and
`POST /api/rules/store/accept` remain available. Acceptance binds present files
and clears missing ones; it does not invent missing rules. An unavailable
complete view returns no rules or count and ends with `rules_seq unavailable`.
Clients must keep their last complete copy on either marker.
Each guarded request while a store is lost emits an ids-only loss event until
owner acceptance. These repeated alerts are intentional; instruction injection
emits none. An over-cap event is emitted when the recorded state or `rules_seq`
changes and the reply is `too_large`.

At serving startup, present unbound stores are migrated and bound together.
This also retries an unbound slot after an earlier partial failure, even when
another store already has a binding. Bound stores remain untouched. Failures
are logged and retried next start. The complete read view defaults to a 100 ms
timeout, within the reconciliation writer's 200 ms ceiling; callers may pass a
shorter remaining deadline.
Before the first deployment, confirm each workspace's rules on the Rules
screen: a file lost before binding, or a control-database rollback to before
that file existed together with its loss, cannot be distinguished from an
earlier empty workspace. Restores of the same store are normal restores.
Store-generation switches copy both identities; switching back to a retained
generation is a normal restore too.

Check modes live in the control database: a connection's `off`, `warn` or
`enforce` overrides the `*` default; no row means off. `GET /api/rules` includes
`checks: {agent: mode}`. Owners set `{agent, mode}` through
`PATCH /api/rules/checks`; `default` removes a connection override. Enforce is
refused when that connection cannot call `rules_get`. Mode changes record an
ids-only `rules.check` audit entry and emit `rule.check`. The server reads
`COMMONPLACE_RULES_CHECK=off` and `COMMONPLACE_RULES_INJECT=off` at startup.
This core stores settings without checking writes yet. MCP calls and board,
memory and fact REST writes already discard an optional `rules_seq` field.
Internal gateway calls use a thread-local `internal_call()` context, so one
request cannot exempt another request on a different thread.

Rollback is additive: the previous code ignores the extra tables, identities,
visibility triggers and internal `pr_repo` column. After a later redeployment,
a file recreated by the older code is detected as replaced and requires owner
acceptance. Public card shapes keep `pr_repo` internal.

### Connection injection

The connection-instruction integration is delivered separately and uses the
shared authorized view and renderer.

### Session-start hook

The session-start integration is delivered separately and preserves the last
complete local rules file on refresh failure.

### Write sequence checks

Write enforcement is delivered separately using the shared version, served
state, control settings and internal-call marker.

### Reviewer checks

Review blocks and review attestations are delivered separately. An orchestrator
must fetch its own complete effective rules before posting attestations when
write checks are enforced.

## Out of scope

- Card moves and board permissions. A future change can map rules of kind `limit` onto board settings.
- The desktop app itself (only the contract is defined here).
- Mirroring rules into Memory.
