### Coworker live log and working-on steps

`commonplace/chat/coworkers/livelog.py` projects existing per-job launch output; `steps.py` projects conversation and run-ledger states. Routes contain only dispatch hooks. There is no new database, file tail, model call or migration.

#### API contract

A coworker view uses its existing conversation ID. `GET /api/coworkers/<id>/log?cursor=&limit=` returns a JSON page. `GET /api/coworkers/<id>/log/stream` uses the console's immediate cursor-poll pattern, with the same response. Both also accept the existing `/api/coworkers/conversations/<id>/...` prefix. Omit `cursor` for the initial tail; an empty or malformed cursor is invalid. `limit` is an integer from 1 through 200, default 200.

```json
{
  "lines": [{"id": "opaque-line-id", "at": "2026-11-14T00:00:00Z", "text": "Reading the change"}],
  "cursor": "opaque-cursor",
  "more": false,
  "reset": false,
  "poll_after_ms": 1000,
  "working_on": [
    {"id": "bot-id", "parent_id": null, "display_name": "Helper", "action_summary": "Working on the task", "state": "working", "at": "2026-11-14T00:00:00Z"},
    {"id": "run:1", "parent_id": "bot-id", "display_name": "Run", "action_summary": "Working on the task", "state": "working", "at": "2026-11-14T00:00:00Z"}
  ]
}
```

Lines are oldest first within the last 200 lines from the newest 20 conversation jobs. The initial page contains the newest `limit` lines. Subsequent cursors return strictly later retained lines, up to `limit`; `more` means another forward page is available. An empty page retains the cursor. Cursors bind the workspace and conversation and convey no read authority. If a previously returned line leaves the window or its source snapshot is replaced, `reset: true` tells the client to replace its displayed tail with this page. Clients should replace existing content after any access refusal as well. IDs and cursors are opaque; clients must not construct them.

The existing conversation detail also adds `working_on`. Its live projection shares the tail/stream rate budget and reader pool. If either limit is reached, the authorized detail still returns 200 with its existing conversation, messages, jobs and memories, plus `limited: true`; `working_on` and log fields are omitted. This fallback rechecks current viewer/session, conversation membership and project read authority without opening the live output sources. Authority refusals still return 404. Under the limits, `working_on` is present and `limited` is omitted.

Parent links form a flat tree: a root per attached bot and a child per recorded, exactly bound run. Display names come from the bot and run connection, with `Run` for an unknown connection name. Actions use the sanitized canonical status line (at most 160 characters) or a plain server phrase derived from recorded state. Running, leased, starting, started and doing map to `working`; done/completed to `done`; uncertain, needs-me and review to `waiting`; queued, stopped, failed and unknown states to `idle`. A run ending does not mark its board task complete. No synthetic subagent steps are invented.

The short aliases match only generated conversation IDs (`conv_` followed by 32 lowercase hexadecimal characters). The existing collection GET routes remain `/api/coworkers`, `/api/coworkers/bots` and `/api/coworkers/conversations`; reserved or malformed single-segment paths do not enter conversation dispatch. The explicit `/conversations/<id>` routes keep their existing ID validation.

#### Privacy and bounds

Only signed-in cookie sessions may read, with current conversation membership and project read permission. Tokens are refused even alongside a session cookie. Source jobs must retain their original session, audience, bot and visibility binding. Each runner read matches the original task, board, requester and launch request key; another attempt cannot supply output. Authority is fenced before and after source reads. Members who can read a shared conversation and its project may read its output; private conversations and other workspaces stay private.

Every complete output snapshot is decoded and stripped of markup, entities, ANSI and control characters before the same shared redactor used by the web feed runs. Absolute, home and drive/UNC paths are masked. Only then are lines capped at 500 characters, preserving whole redaction markers. Whole-snapshot redaction protects multiline private keys. Oversized sources fail closed. The retained window is a 200-entry deque; source candidates are capped at 20 jobs. Live projections across detail, tail and stream share 120 reads per minute per person/workspace, a bounded 1,024-entry limiter and eight concurrent readers. A denied live projection does not open its output sources. Stream requests return immediately: there are no retained subscribers or waiting worker threads.

#### Source limits

The feed shows only output already present on the existing job row, currently launch diagnostics when reported. No output means an empty log. It does not open a runner's local files or dereference a ledger log pointer. Run state changes remain live even when no diagnostic text was reported.

#### Validation

`tests/test_coworker_livelog_contract.py` is the browser-free base/head regression. `tests/test_coworker_livelog.py` covers both routes, secret/markup/escape sentinels, path and multiline redaction, isolation and revocation, source binding, bounded pages/reads, and state mapping.

`tests/test_coworker_livelog_limits.py` covers shared rate consumption, exhausted quota, concurrent reader exhaustion and ownership, graceful detail recovery, authority changes during admission, and the existing GET collection inventory.
