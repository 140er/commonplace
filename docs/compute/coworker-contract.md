# Coworker vertical-slice integration contract

Implementation coordination, 2026-11-09. Product requirements are in the
Compute docs workspace. This contract fixes interfaces for concurrent builders;
it does not certify source or release review.

## Ownership

- Storage/API builder: `commonplace/chat/coworkers/__init__.py`, `store.py`,
  `routes.py`, `commonplace/chat/__init__.py` installation glue and
  `tests/test_coworker_store.py`, `tests/test_coworker_api.py`.
- Runtime builder: `commonplace/chat/coworkers/runtime.py` and
  `tests/test_coworker_runtime.py`. Do not edit another builder's files.
- UI builder: `commonplace/web/static/coworkers.js`, `coworkers.css`, app route
  and navigation glue, static UI tests. Existing chat remains functional.
- Parent owns shared SYSTEM.md/MODULES.md changes, integration and grouped checks.

## Identity and storage

Use existing authenticated workspace users, connections, board/task services,
configured model clients and encrypted SQLite connection facilities. Never put
conversation text into the global control database. Add workspace-local
conversation metadata/messages/jobs and verbatim conversation-scoped memory to
`coworkers.sqlite3`, opened through the workspace supervisor path and SQLite
crypto machinery. This is conversation storage, not a new task/memory engine.
Messages and memory revisions are append-only; removing a memory hides it via a
tombstone without rewriting original words. Private DM and group membership is
checked before every read/write and again before dispatch/model context.

A bot is persistent presentation/configuration bound optionally to an existing
authorized connection, not a new independently privileged principal. Owner-only
bot creation/configuration. Creating a persona grants no tools or worker seat;
unbound personas must not be represented as registered agent connections. No
tokens are created/revealed for this slice. A display name cannot confer authority.

## JSON REST surface

Cookie person authentication required; writes enforce the existing approved
Origin/session boundary with CSRF handling appropriate to the gateway. Do not
permit agent PATs to impersonate users or approve themselves. Foreign and
nonmember conversation/object IDs are concealed consistently. Validate bounded
text, names, IDs, pagination and exact allowed fields; serialize explicit fields.

- `GET /api/coworkers`: `{bots:[], conversations:[], user_id, can_manage_bots}`.
- `GET /api/coworkers/bots`: `{bots:[]}`.
- `POST /api/coworkers/bots`: `{name,instructions?,connection_id?}` -> `{bot}`.
- `GET /api/coworkers/conversations`: `{conversations:[]}`.
- `POST /api/coworkers/conversations`:
  `{kind:"dm"|"group",title?,member_ids:[],bot_ids:[],board?}` -> `{conversation}`.
  Creator is always a member; all invited users/connections must belong to the
  same workspace. DM has one human and one bot for this initial slice; groups
  support multiple humans and bots. Board, if supplied, must be readable and
  writable for the creator through existing services.
- `GET /api/coworkers/conversations/<id>` -> `{conversation,messages:[],jobs:[],memories:[]}`.
- `GET .../<id>/messages?after=<id>&limit=<n>` -> `{messages:[],jobs:[]}`.
- `POST .../<id>/messages`: `{text,request_id,bot_id?}` -> `{message,job}`.
  Request ID is required for idempotency. Same request returns stored result;
  different text/target under the same key conflicts. Single-bot conversations
  auto-address that bot; multiple-bot groups require a selected bot or explicit
  unambiguous mention, and ordinary unaddressed group messages do not start all bots.
  Persist message and pending response job atomically, then return promptly.
  No provider call or long execution occurs in the HTTP request.
- `GET .../<id>/memory` -> `{memories:[]}`.
- `POST .../<id>/memory`: `{text}` -> `{memory}`.
- `DELETE .../<id>/memory/<memory_id>` -> `{ok:true}` via tombstone.
- `POST /api/coworkers/jobs/<id>/cancel` -> `{job}` after membership checks.

Bots: `{id,name,instructions,connection_id,created_at}`. Conversations:
`{id,kind,title,member_ids,bot_ids,board,created_at}`. Messages:
`{id,conversation_id,actor,bot_id,text,created_at,request_id,reply_to?}`; model
answers record model/provider metadata without secrets. Memory:
`{id,conversation_id,text,actor,created_at}`. Jobs:
`{id,conversation_id,message_id,bot_id,user_id,state,code,created_at,updated_at,task?}`.
UI must not label pending/running as completed or claim real model responses
when no provider is configured. Existing BoardService remains canonical task truth.

## Store/runtime interface

`open_store(gateway, workspace_id) -> Store`. Route-level service validates current
workspace identities and membership; store independently enforces conversation
membership and bounds for caller-facing methods. Constructor tests can use an
isolated explicit path. Expected interface, keyword arguments preferred:

```
Store(path)
store.list_bots()
store.create_bot(user_id, name, instructions="", connection_id=None)
store.list_conversations(user_id)
store.create_conversation(user_id, kind, title="", member_ids=(), bot_ids=(), board=None)
store.get_conversation(user_id, conversation_id)
store.list_messages(user_id, conversation_id, after=0, limit=100)
store.post_message(user_id, conversation_id, text, request_id, bot_id=None)
store.list_jobs(user_id, conversation_id)
store.list_memory(user_id, conversation_id)
store.add_memory(user_id, conversation_id, text)
store.remove_memory(user_id, conversation_id, memory_id)
store.cancel_job(user_id, job_id)
```

`post_message` returns `{message,job}`. Runtime-only methods must not become
public routes and must not bypass current caller access:

```
store.claim_job() -> dict | None
store.runtime_job(job_id) -> internal dict with exact session digest (never REST)
store.finish_job(job_id, state, code="", task=None)
store.add_bot_reply(job_id, text, model=None) -> dict
store.recover_jobs()  # stale in-flight => interrupted/uncertain; never blind replay
```

`claim_job` atomically claims pending work without admitting another running job
for the same conversation+bot, while other conversations/bots remain claimable.
`add_bot_reply` commits reply and terminal job transition together, refuses
cancelled/terminal jobs, and is idempotent on job ID. Output bounds apply.

Runtime exports `CoworkerWorker(gateway, *, max_workers=4, start=True)` with
`wake()`, `stop()`, `run_once(workspace_id)` for deterministic tests. Installation
creates one worker on `gateway.coworker_worker`; routes call `wake()` after commit.
Discover workspaces through existing gateway/workspace lifecycle or a bounded
explicit pending-workspace set, not by scanning user directories. The HTTP path
only schedules/wakes; a blocking provider in one worker must leave other
conversations and HTTP message persistence usable. Limit concurrency and provider
input/output/time through existing settings; never silently enable external API.

Build model context from the authenticated poster, the addressed persona,
conversation members' visible messages, and that conversation's visible verbatim
memory only. Revalidate sessions/membership/bot association before calling the
provider and before publishing results. Use configured local/approved front model
through existing `chat.settings`/`chat.model` where supported; disabled/unavailable
provider produces explicit `needs_configuration`/unavailable state, not a canned
model answer. Worker task dispatch must use existing BoardService with authentic
user permissions and explicit intent; do not execute free-form model output.

## Verification

Meaningful integration tests: persistence/reopen; private/group membership and
cross-workspace denials; valid versus expired/revoked identities; request replay
and conflicts; one selected bot rather than group-wide loops; escaped output;
private memory isolation and tombstones; cancelled/late results; restart without
duplicated answers/effects; slow provider in conversation A while B answers and
HTTP posts still return; actual task references if dispatch is supported.

Run syntax and lint checks during development, then a grouped focused pytest
selection and required checks after integration. Record actual commands and
results; do not claim success for tests or fixtures that were not run.
