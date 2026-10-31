# Rules

Every connected agent publishes the rules it follows: standing instructions, permissions, hard limits and style. The owner reads them in one place, edits or clarifies them, and accepts or retires each one. Agents read the current set, including the owner's edits, at the start of a session.

## Decisions

- **Owner edits win.** An owner's edit takes effect at once. If the agent later republishes its own wording, that wording is kept as a proposal next to the rule. It never overwrites the edit, and the owner decides.
- **Clarify path B is desktop only.** The web page asks the agent only through the desktop bridge (below). Without the bridge it offers path A, copy the prompt. There is no server relay.
- **Own store, same pattern as boards.** Rules need a status, a version history and a scope per item, and none of that fits a Memory drawer. They live in `rules.sqlite3` next to `boards.sqlite3` in each workspace's palace directory. The file uses the board conventions: server stamps from the verified token, an append-only history, verbatim text and a change counter. Agents get the rule set back as Markdown (`format=md`), ready to drop into a skill or instructions file. That covers the skills-as-.md model without a second copy on disk.

## Data model

```sql
-- rules.sqlite3, one per workspace
CREATE TABLE meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);  -- 'seq': bumped on every change
CREATE TABLE rules (
  id          INTEGER PRIMARY KEY,
  agent       TEXT NOT NULL,         -- actor ref "conn:<name>", or "*" for an owner rule that applies to every agent
  key         TEXT NOT NULL,         -- stable per agent; given by the agent, else derived from the text
  text        TEXT NOT NULL,         -- effective text: what the agent must follow
  agent_text  TEXT,                  -- the agent's latest published wording (NULL for owner-written rules)
  kind        TEXT NOT NULL CHECK (kind IN ('permission','limit','instruction','style')),
  scope       TEXT NOT NULL CHECK (scope IN ('global','board','repo')),
  scope_ref   TEXT,                  -- board slug, or normalised repo "host/owner/name"; NULL for global
  status      TEXT NOT NULL CHECK (status IN ('proposed','accepted','edited','retired')),
  source      TEXT NOT NULL CHECK (source IN ('agent','owner')),  -- who wrote `text`
  pending     INTEGER NOT NULL DEFAULT 0,  -- 1: the agent republished wording that differs from an owner edit
  version     INTEGER NOT NULL DEFAULT 1,
  seq         INTEGER NOT NULL,      -- meta.seq at the last change
  created_at  TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL,
  UNIQUE (agent, key)
);
CREATE TABLE rule_versions (        -- append-only (triggers refuse UPDATE and DELETE)
  id INTEGER PRIMARY KEY, rule_id INTEGER NOT NULL REFERENCES rules(id),
  version INTEGER NOT NULL, text TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL, scope_ref TEXT,
  status TEXT NOT NULL, source TEXT NOT NULL CHECK (source IN ('agent','owner','suggestion')),
  note TEXT,                          -- the owner's reason or the agent's change note, verbatim
  stamp_user TEXT NOT NULL, stamp_connection TEXT, stamp_trust TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE rule_suggestions (     -- clarify replies brought back through the desktop bridge
  id INTEGER PRIMARY KEY, rule_id INTEGER NOT NULL REFERENCES rules(id),
  text TEXT NOT NULL, from_agent TEXT NOT NULL,       -- the connection the owner asked
  status TEXT NOT NULL CHECK (status IN ('open','accepted','dismissed')),
  stamp_user TEXT NOT NULL, created_at TEXT NOT NULL, decided_at TEXT
);
```

Rules are never deleted. Retiring keeps the row and its history.

### Status flow

| Event | Result |
|---|---|
| Agent publishes a new key | `proposed`, `source=agent`, `text=agent_text`. The agent follows it right away. "Proposed" means the owner hasn't reviewed it yet. |
| Owner accepts | `accepted` |
| Owner edits the text, kind or scope | `edited`, `source=owner`. `agent_text` stays, so the UI can show the diff. |
| Agent republishes the same key and the text differs | If `source=agent`: the text updates, the status goes back to `proposed` and the version is bumped. If `source=owner`: only `agent_text` updates and `pending=1`. The owner's text stays in force. |
| Owner takes the agent's wording (`accept_agent`) | `text=agent_text`, `source=agent`, `accepted`, `pending=0` |
| Owner keeps the edit (`keep_edit`) | `pending=0` |
| Agent publishes with `replace=true` and leaves out a key it published before | `retired` (note: "dropped by agent") |
| Owner retires the rule | `retired`. Agents read it as "stop following this". |
| Owner restores the rule | back to `accepted`, or `edited` when `source=owner` |
| A suggestion is accepted | same as an owner edit, with `source=suggestion` in the history |

Every change writes one `rule_versions` row and bumps `meta.seq`.

### Visibility

- An agent sees its own rules, plus the `*` rules, filtered by scope: a `board` rule only when it can read that board's wing, and a `repo` rule always (repos are not secret on their own).
- People in the workspace see everything. Only the owner (`principal.role == "owner"`) can edit, accept, retire, restore, suggest or decide.

## MCP tools

All tools are registered from a new extension module, `commonplace.rules` (added to `extensions.MODULES`), and are in every profile. Agents cannot write any other agent's rules: the agent is always `principal.actor`, never an argument.

| Tool | Scope | Kind | Arguments | Reply |
|---|---|---|---|---|
| `rules_publish` | `memory:write` | write | `rules: [{key?, text, kind, scope?, board?, repo?}]` (max 200), `replace?: bool` (default false) | One line per rule (`key status version`), then `seq N`. With `replace=true` the call is a bulk replace: keys it leaves out are retired. |
| `rules_get` | `memory:read` | read | `board?`, `repo?`, `format?: "text" \| "md"`, `include_retired?: bool` | The rules that apply to me: my own plus `*`, global plus the given board and repo, with `status`, `source`, `version` and the effective text. `format=md` gives a Markdown rules file grouped by kind, with retired rules left out. The last line is `seq N`. |
| `rules_changed` | `memory:read` | read | `since: int` | `seq N` plus the keys that changed after `since`, with their new status. It does no text work; meant to run on every session start. |

Untrusted tokens may publish: what they publish starts as a proposal for the owner, and nothing else is written. Rule text is stored verbatim, and replies quote it as data the way board tools do.

Agent loop, as written in the skill: at the start of a session call `rules_changed since=<last seq>`. When something changed, call `rules_get` and follow `text`: owner edits win, and retired rules no longer apply. When your own rules change, call `rules_publish` with `replace=true`.

## REST (web app, cookie auth)

| Method | Path | Body | Who |
|---|---|---|---|
| GET | `/api/rules` | — | any person. Returns `{rules: [...], suggestions: [...open], agents: [...connection names], seq}`, with every rule from every agent, retired ones included. |
| GET | `/api/rules/:id/history` | — | any person. Returns the `rule_versions` rows, oldest first. |
| POST | `/api/rules` | `{agent: "*" or "conn:x", text, kind, scope, scope_ref?}` | owner. A rule written by the owner starts as `accepted` with `source=owner`. |
| PATCH | `/api/rules/:id` | `{version, text?, kind?, scope?, scope_ref?, note?}` | owner. Answers 409 with the current rule when `version` is stale. |
| POST | `/api/rules/:id/action` | `{version, action: accept\|retire\|restore\|accept_agent\|keep_edit}` | owner |
| POST | `/api/rules/:id/suggestions` | `{text, from_agent}` | owner. This is where the web page stores a clarify reply that came back through the bridge. |
| POST | `/api/rules/suggestions/:sid` | `{action: accept\|dismiss}` | owner |

Rule JSON: `{id, agent, key, text, agent_text, kind, scope, scope_ref, status, source, pending, version, seq, updated_at, updated_by}`.

## Owner UI

The UI uses look B: the tokens, Geist Mono, tiles on the 6px gap and the v2 block logo from `app.css`/`ui.css`. No new colours.

- **Nav:** "Rules" in the sidebar under Memory and in the phone tabs (`#/rules`). Its count is the number of rules that wait on the owner (`proposed`, or `pending=1`), shown the same way Home shows its count.
- **Screen:** the title is "Rules". There is one tile per agent (the connection name, with "All agents" for `*`), ordered by how many rules wait on the owner. The header row holds the agent's name, its waiting count and a "Help me clarify" button for the whole set.
- **Rule row:** a kind chip, a scope chip (only when it isn't global, e.g. `board commonplace`), the rule text and a status chip. Orange marks only what waits on the owner, the same as on the board. Proposed rows show Accept, Edit and Retire. Edited rows show the diff between the agent's wording and the owner's text: removed words struck through in the quiet step, added words in ink. Rows with `pending` show the agent's new wording as a diff with "Use agent's" and "Keep mine". Retired rows sit folded at the bottom of each tile.
- **Inline edit:** the text turns into a textarea, with selects for kind and scope, then Save and Cancel. Nothing opens in a modal.
- **Help me clarify** (on a row and on a tile) opens a small inline panel:
  - **Copy prompt** (always there): copies the prompt below.
  - **Ask an agent** (only when `window.commonplaceDesktop` exists): pick a connected agent and press Ask. While it waits the panel shows "Asking <agent>…". The reply is stored as a suggestion and shown under the rule as a diff with Accept and Dismiss. For a whole set, the reply is parsed as `key: text` lines and becomes one suggestion per matching rule. A reply that can't be parsed is shown as text, with Copy.
- **Phone (390px):** tiles stack, chips wrap above the text, and the actions become full-width blocks.

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

## Out of scope

- Card moves and board permissions. Rules only describe what an agent does; they don't enforce anything. A future card can map rules of kind `limit` onto board settings.
- The desktop app itself (only the contract is defined here).
- Mirroring rules into Memory.
