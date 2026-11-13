### Person-consented phone actions

S1 of card #497 covers capability consent, card decide/approve, project chat and
phone chat-list summaries. The per-person event feed and fresh web sign-in
account-deletion token remain later slices. Existing account safeguards remain
unchanged.

`COMMONPLACE_PHONE_ACTIONS=on` is an instance opt-in. Enable it before service
startup to install the private card prefix. Consent, mint and use check the live
switch. Every other value is off. Existing clients and tokens gain no authority;
a new consent creates a new connection and capability association. Disconnecting
revokes the OAuth tokens. Machinery conversion withdraws phone capabilities.

The phone requests and receives exactly `memory:read`. OAuth metadata, policy
scopes, default scopes and web session policies remain unchanged. The following
are separate consented capabilities, never OAuth scopes:

- `cards:decide:person`: answer card questions and completion reviews using the person's existing web rights.
- `cards:approve:person`: approve or reject proposals and plans using those same rights.
- `chat:person`: read project chats and post the person's own messages where their web account may do so.

Only the exact native callback may obtain these capabilities through the existing
PKCE consent. Each has its own initially unticked checkbox. The exact strings are:

- `cards:decide:person`: "Allow this app to answer card questions and completion reviews as you in this workspace. It can answer only items you may answer on the website. Each answer records your identity and this app's name. Disconnect this app to stop access."
- `cards:approve:person`: "Allow this app to approve or reject card proposals and plans as you in this workspace. It can act only on items you may review on the website. Each action records your identity and this app's name. Disconnect this app to stop access."
- `chat:person`: "Allow this app to read your project chats and send your own messages as you in this workspace. It can reach only chats you may read and write on the website. Each message records your identity and this app's name. Disconnect this app to stop access."

The owner must approve the final capability names and consent strings before merge.

Card actions use protected `POST /api/person/cards/<number>/decide` and
`.../approve`, require a positive `version` and `Idempotency-Key` (1–128 ASCII
letters, digits or `._~-`), and use the existing board operations. Exact retries
return their original receipt; changed arguments with the same key return 409.
Receipts and card changes commit together. Current bearer, capability binding and
card visibility checks apply to retries.

Chat, decide and approve enter the shared checked REST write wrapper with the
current person's context before the board operation. Its existing person rule
policy remains unchanged. The wrapper emits one `tool.call` row with OAuth
connection and client attribution before the board commit; audit failure rolls
back the write. The existing checked `space_post` inventory entry covers both
chat implementations through one method dispatch. Inventory count and
unique-route assertions remain unchanged.

The chat contract is:

| Route | Consented phone | Ordinary OAuth | Web session |
| --- | --- | --- | --- |
| `GET /api/chat` | Person's readable projects plus phone-only summaries | 403 | Existing response |
| `GET /api/chat/<slug>?last=50` | Same person-readable feed as the web | 403 | Existing feed |
| `POST /api/boards/<slug>/chat` with `{text}` | Person's own message; 201; no `memory:write` on the bearer | Existing policy refusal or connection reply | Existing person post |

The phone capability never makes the authenticated OAuth principal a person
globally. Only these route-local operations resolve the current person. Settings,
model probes, status/search subroutes, other aliases, account data and the events
feed gain no authority. Existing gateway rate limits apply before dispatch.

For phone-capability callers only, every `projects` item keeps `slug`, `name` and
nullable `orchestrator: {name}`, and gains:

- `status`: `"unused" | "working" | "idle" | "quiet" | null`. This is exactly the recorded connection-activity classifier used by web chat, subject to the same person's console visibility. Missing, invisible or invalid recorded activity is null.
- `last_message`: `{text: string, at: string, author_kind: "person" | "bot"} | null`. It selects the latest readable shared board message or the current person's private front reply, including visible front notices. Private bot threads are not project chat inputs. `text` is redacted with the feed's redactor, stripped of HTML tags and truncated to 240 Unicode characters; `at` is UTC ISO 8601 ending in `Z`.

Example (recorded values, not default activity):

```json
{"projects":[{"slug":"work","name":"Work","orchestrator":{"name":"project-helper"},"status":"working","last_message":{"text":"The change is ready for review.","at":"2026-11-13T19:00:00Z","author_kind":"bot"}}]}
```

Unreadable boards are removed before any summary lookup. The summary projection
batches its source reads across the visible list, with no query in the per-project
loop; a 50-project query-count regression pins this bound. Other people's private
front replies and private bot conversations cannot influence content, count or
ordering. Ordinary web callers retain their exact previous list shape.

New public routes: none. The private card prefix separates capability authority
from general session authority. The three chat routes already exist. Public-route
pins and disabled baselines are unchanged. The control grant table is classified
by person/workspace/connection for account export and deletion. Literal SQL table
names are used throughout. History and audit retain the person and client name;
no bearer is persisted or logged.
