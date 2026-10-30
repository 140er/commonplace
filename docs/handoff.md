# Context handoff

## What it does

- When a thread nears its context limit, handoff saves it in Memory.
- Every message, tool call and tool result stays verbatim.
- A fresh agent starts with a short handoff and a pointer to that thread.
- It can retrieve the earlier words when it needs them.

## Turn it on

Handoff is off by default. With it off, existing behaviour and normal
compaction stay unchanged.

Create a web sign-in link for an existing workspace member:

```bash
commonplace login-link you@example.com --workspace personal --base-url http://127.0.0.1:8765
```

Open the printed `/app/login?t=…` link, then `/app/settings/memory`.
The link works once and expires after 15 minutes by default. Sign-in sets
the web app's `cmmn_session` cookie. Forms require a CSRF token derived
from that session and reject cross-site submissions.

Workspace owners can turn on **Handoff** and change the threshold and
agent or task overrides. Other members can view the settings. Signed-in
people can read stored threads in their workspace.

From the CLI, use your workspace and connection names:

```bash
commonplace handoff enable --workspace personal --threshold 0.80
commonplace handoff status --workspace personal
commonplace handoff disable --workspace personal --agent codex:local
commonplace handoff enable --workspace personal --task '#142'
```

Each field inherits separately: task overrides agent, agent overrides
workspace, and workspace overrides the default. A task can stay on while
its workspace is off. Remove that task override with:

```bash
commonplace handoff clear --workspace personal --task '#142'
```

Set these in the environment that starts the agent:

```bash
export COMMONPLACE_URL=http://127.0.0.1:8765
export COMMONPLACE_TOKEN=cmmn_REPLACE_WITH_YOUR_TOKEN
export COMMONPLACE_TASK='#142'
```

`COMMONPLACE_TASK` is optional. The hook needs `handoff:write` to request a
handoff and register its successor. The successor needs `threads:read`
and access to the stored thread.

The API uses the workspace attached to the bearer token. For example:

```bash
curl --request PUT "$COMMONPLACE_URL/api/settings/handoff" \
  --header "Authorization: Bearer $COMMONPLACE_TOKEN" \
  --header 'Content-Type: application/json' \
  --data '{"scope":"workspace","ref":"","enabled":true,"threshold":0.80}'
```

For an agent override, use `"scope":"agent"` and its connection name as
`ref`. For a task override, use `"scope":"task"` and `"ref":"#142"`.
An omitted or `null` setting inherits. Owners control workspace and agent
settings and launchers. Members may change only a task's `enabled` field.

To force handoff off for one run, regardless of saved settings:

```bash
COMMONPLACE_HANDOFF=off claude
```

## When it triggers

The default threshold is `0.80`. The allowed range is `0.50` to `0.95`.
The hook compares the last model call's context use with the model's window.
It triggers when the fraction reaches or exceeds the threshold.

For Claude Code, usage includes input, cache-read, cache-creation and output
tokens. For Codex, it comes from `token_count` events. The hook reads the
transcript tail rather than the whole file for this check.

Windows come from a model-name table; the default is 200,000 tokens. A
Claude model runs with a 1,000,000-token window when its name ends in
`[1m]` or `-1m`, is on the known list (add more with
`COMMONPLACE_1M_MODELS=name,prefix`), or is the model Claude Code was told
to use (`ANTHROPIC_MODEL`, or `model` in `.claude/settings.json`) and that
has the marker. A transcript that already uses more than the table window
is on 1M too. `--window` on `handoff enable` (or `context_window` through
the API) always wins.

Unknown usage does not trigger a threshold handoff. `PreCompact` is the
backstop: when handoff is enabled, it requests a handoff even if the
threshold has not been reached or usage is unavailable.

The hook caches effective settings. A saved setting change may take up to
60 seconds to reach a running hook. `COMMONPLACE_HANDOFF=off` takes effect
locally without a settings request.

## Set up the hook

Install Commonplace in the Python environment used by the hook. Set
`COMMONPLACE_PYTHON` to that interpreter's absolute path if `python3` uses
a different environment. Set the gateway URL and token before starting
the agent. Configure a successor command as described below.

### Claude Code

Merge this `hooks` block into the project's `.claude/settings.json`.
Replace `/absolute/path/to/repo` with this repository's path. Keep the
escaped quotes if the path contains spaces. Keep any existing handlers.

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"/absolute/path/to/repo/hooks/commonplace_handoff_hook.sh\" --client claude-code",
            "timeout": 60
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"/absolute/path/to/repo/hooks/commonplace_handoff_hook.sh\" --client claude-code",
            "timeout": 60
          }
        ]
      }
    ]
  }
}
```

The wrapper passes the hook JSON from stdin to
`python3 -m commonplace.handoff.hook --client claude-code`.
After a handoff, the hook returns `{"continue":false,"stopReason":"…"}`
to stop the old agent.

### Codex

Use the repository's Codex hook mechanism: `.codex/hooks.json`, with a
nested `hooks` object and handler groups. Register the hook for `Stop`
(the check), `PreCompact` (the backstop) and `UserPromptSubmit` (where the
handoff happens).

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"/absolute/path/to/repo/hooks/commonplace_handoff_hook.sh\" --client codex",
            "timeout": 60
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"/absolute/path/to/repo/hooks/commonplace_handoff_hook.sh\" --client codex",
            "timeout": 60
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash \"/absolute/path/to/repo/hooks/commonplace_handoff_hook.sh\" --client codex",
            "timeout": 60
          }
        ]
      }
    ]
  }
}
```

Codex must trust the project configuration. Then review each definition
with `/hooks`; project trust alone does not approve hook commands. Use
either JSON or TOML in a config layer so hooks do not run twice. See the
[Codex hook guide](../website/guide/hooks.md#install--codex-cli) for the
existing configuration and trust flow. `mempalace status` checks project
trust; it cannot confirm that each definition was reviewed.

A Codex hook can't stop a running turn. So over the threshold the hook
only marks the session and shows "Context is nearly full. It hands off at
the start of the next turn." When your next message arrives
(`UserPromptSubmit`), it stores the thread, writes the handoff and starts
the successor with your message passed on, word for word. The old session
refuses that message with "Context handed off. Successor: …". If the hub
can't be reached, the mark stays and the next turn tries again.

## Choose the successor command

The default launcher is `client`: the hook starts the successor on the
agent's machine. Put one command in `~/.commonplace/handoff-client.json`.
For Claude Code:

```json
{
  "successor_argv": ["claude", "-p", "{prompt}"]
}
```

For a T3 project:

```json
{
  "successor_argv": ["t3", "thread", "start", "{cwd}", "{prompt}"]
}
```

`{cwd}` must identify a project known to T3. This uses
`t3 thread start <project> <message>` and starts a fresh thread. Give that
project's agent access to the Commonplace thread tools.

Each JSON array element is one argument. Placeholders are replaced inside
that argument. No shell evaluates the command: shell quotes, `~`, pipes
and redirections are not expanded.

| Placeholder | Value |
|---|---|
| `{prompt}` | The handoff Markdown and retrieval instructions |
| `{prompt_file}` | Path to a file containing that prompt |
| `{handoff_id}` | This handoff's ID |
| `{thread_id}` | The stored thread ID, not the successor's client ID |
| `{cwd}` | The source thread's working directory |

The launched process inherits the environment and receives
`COMMONPLACE_HANDOFF_ID`. Keep that value so its next handoff can link
back to this one. The server's `command` launcher also supplies
`COMMONPLACE_THREAD_ID` with the stored thread ID.

Owners can instead configure a `command` launcher on the gateway machine,
or a `webhook` launcher at their own URL. The command launcher also uses
an argument array without a shell. A webhook launcher receives the prompt
and must return an `agent_ref`, with optional `url` and `thread_id`.
Event webhooks, described below, do not receive the prompt.

Without `successor_argv`, the hook starts nothing and names the prompt
file in its stop message, so you can start the successor yourself.

The hook registers the successor as `pid:<pid>` of the command it ran.

The successor prompt ends with `commonplace-handoff-id: hof_…`. A
successor whose environment has no `COMMONPLACE_HANDOFF_ID` (a T3 thread:
`t3 thread start` can't pass environment variables to the thread's agent)
gets it from that line: its own hook reads it from the start of its
transcript, so its next handoff links into the chain right away. The hub
checks that the parent is in the same workspace.

## What the successor gets

The new agent receives the handoff and a stored thread pointer. It does
not load the full old conversation into its context.

The handoff quotes the first user message as the goal, the last assistant
message as the status, unfinished todo items as open points, and later
user messages as decisions. It includes available task, branch and PR
references, an optional verbatim agent note, and earlier thread pointers.
No model rewrites the thread. Long quotes may end with `…` and a
`thread_get` range for the full words. Stored and retrieved messages
remain verbatim.

### Thread tools

The three tools are gateway extension tools. They stay hidden until a
workspace enables handoff at any scope or has stored threads. Turning
handoff off keeps the tools available for threads already stored.

Tokens need `threads:read`, an allowed tool name, and permission to read
the stored thread. The `standard` and `full` profiles include the names;
explicit `--tools` lists can name them too. Untrusted tokens cannot use
them. Hidden tools are absent from `tools/list` and answer `Unknown tool`.

Handler errors return `isError: true` with text starting `no:`. Missing
and inaccessible threads both return `no: thread not found`.

Use the stored ID (`thr_…`) as `thread_id` in all three tools.

`thread_recall` searches the thread and, by default, its predecessors.

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `thread_id` | string | required | Stored thread to search |
| `query` | string | required | Words or facts to find |
| `limit` | integer | `8` | Maximum hits; 1–50 |
| `follow_chain` | boolean | `true` | Include readable earlier threads |

Each hit identifies its thread, chunk and inclusive message range, with
a score and verbatim text. Use the range to fetch more context.

`thread_get` returns messages in source order.

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `thread_id` | string | required | Stored thread to read |
| `start` | integer | `0` | First message number, inclusive |
| `end` | integer | omitted | Last message number, inclusive; omit to read up to the call limit |
| `include_raw` | boolean | `false` | Include each message's original source record |

Message numbers start at zero. Each call returns at most 200 messages.
Request further ranges to read a longer thread. For example, `start=20,
end=24` reads five messages.

`thread_chain` returns the requested thread followed by its predecessors.

| Argument | Type | Default | Meaning |
|---|---|---|---|
| `thread_id` | string | required | Stored thread at which to start |

The order is newest first. Traversal stops at the first thread the token
cannot read and stops safely if it encounters a cycle.

## REST API

Send `Authorization: Bearer cmmn_…`. Request and response bodies are JSON.
The token selects the workspace. Thread and handoff resources outside its
access return `404` with `{"error":"not found"}`, just like missing IDs.

| Method | Path | Access | Body or result |
|---|---|---|---|
| POST | `/api/handoff` | `handoff:write` | Handoff request → action, record, launch flag and prompt |
| GET | `/api/handoffs?task=%23142` | `threads:read` | Handoffs for the task; task filter is optional |
| GET | `/api/handoffs/<id>` | `threads:read` | Handoff record |
| POST | `/api/handoffs/<id>/successor` | `handoff:write` | `{agent_ref, url?, thread_id?}` |
| POST | `/api/handoffs/<id>/launch-failed` | `handoff:write` | `{error}` |
| POST | `/api/handoffs/<id>/relaunch` | owner | Retry a failed launch |
| GET | `/api/threads/<id>` | `threads:read` | Stored thread metadata |
| GET | `/api/threads/<id>/messages?start=0&end=99` | `threads:read` | Verbatim messages in the inclusive range |
| POST | `/api/threads/<id>/recall` | `threads:read` | `{query, limit?, follow_chain?}` → search hits |
| GET | `/api/threads/<id>/chain` | `threads:read` | This thread and readable predecessors |
| GET | `/api/settings/handoff?agent=&task=` | authenticated token | Saved rows and effective settings |
| PUT | `/api/settings/handoff` | owner; members: task `enabled` only | `{scope, ref, enabled?, threshold?, context_window?, launcher?}` |
| DELETE | `/api/settings/handoff?scope=&ref=` | owner | Remove an override |

Encode `#` as `%23` in URL query values. An unencoded `#` starts a URL
fragment and never reaches the server.

A handoff request has these fields:

| Field | Meaning |
|---|---|
| `meta` | Required object with the source `thread_id`; optional `agent`, `client`, `model`, `task_ref`, `branch`, `pr_url`, `cwd`, `parent_handoff_id` |
| `transcript` | Required full transcript as a string |
| `transcript_format` | `auto` (default), `claude-code`, `codex`, or `messages` |
| `usage` | Optional `{used_tokens, window_tokens, model}` |
| `note` | Optional agent words, preserved verbatim |
| `idempotency_key` | Optional retry key; defaults to `thread:<source thread_id>` within the workspace |

The `messages` format is a JSON list of `{role, content}` records, encoded
in the `transcript` string. The other formats are the clients' JSONL.

An `action` of `compact` leaves the client on its normal path. `handoff`
tells the old agent to stop. Only the response with `launch:true` tells
the client to start a successor; other launchers start it on the server.

## Events and webhooks

Handoffs write audit entries and append to the hub's event log. The hub's
existing webhook service delivers matching events in the background.
As a workspace owner, subscribe to the `handoff` preset:

```bash
commonplace webhook add http://127.0.0.1:9000/handoff --workspace personal --events handoff
```

Or call the `webhook_add` extension tool with:

```json
{"url":"http://127.0.0.1:9000/handoff","events":["handoff"]}
```

The tool needs `memory:read`, an allowed tool name and a connection that
is not untrusted. Both methods return the signing secret once; store it
at the receiver. The hub also accepts these arguments at
`POST /api/webhooks`; its existing list, remove, test and delivery routes
manage subscriptions. Without an event filter, a subscription receives
all permitted event kinds.

| Event | Meaning |
|---|---|
| `handoff.started` | A handoff began |
| `handoff.stored` | The full thread and handoff are saved |
| `handoff.completed` | The successor was registered |
| `handoff.failed` | Storage failed; use normal compaction |
| `handoff.launch_failed` | The thread is saved but successor launch failed |
| `handoff.settings_changed` | A saved setting changed |

The hub wraps each event in this envelope (example values):

```json
{
  "agent": "commonplace",
  "task": "#142",
  "need": "info",
  "message": "#142 context handed off http://127.0.0.1:8765/s/A1b2C3d",
  "id": "evt_42",
  "seq": 42,
  "kind": "handoff.completed",
  "at": "2026-10-30T12:00:00Z",
  "workspace": "ws_example",
  "subject": "#142",
  "actor": "conn:example",
  "data": {
    "handoff_id": "hof_example",
    "stored_thread_id": "thr_example",
    "task_ref": "#142",
    "successor_agent_ref": "t3:example",
    "number": 142
  },
  "url": "http://127.0.0.1:8765/s/A1b2C3d"
}
```

Handoff `data` contains available IDs and references only, plus `number`
for a numeric task reference and `decision` on failures. Empty references
are omitted. The webhook filter excludes `reason`, thread text and the
handoff prompt. `subject` is the task reference, or the handoff ID when
there is no task. `url` links to the app; `id` identifies the hub event.

The receiver gets a JSON POST with `X-Commonplace-Event`,
`X-Commonplace-Delivery` (the hub delivery ID), and
`X-Commonplace-Signature: t=<unix seconds>,v1=<hex digest>`. The digest is
HMAC-SHA256 with the UTF-8 secret over the timestamp, a dot and the exact
body bytes: `timestamp + b"." + body`. Verify those bytes before parsing
the JSON. For example, with a receiver-chosen five-minute age limit:

```python
import hashlib
import hmac
import json
import time


def verify_handoff(body: bytes, signature: str, secret: str) -> dict:
    fields = dict(part.split("=", 1) for part in signature.split(","))
    timestamp = fields["t"]
    expected = hmac.new(
        secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(fields["v1"], expected):
        raise ValueError("invalid signature")
    if abs(time.time() - int(timestamp)) > 300:
        raise ValueError("stale signature")
    return json.loads(body)
```

Pass the unmodified body and the `X-Commonplace-Signature` header to this
function. Read `kind` and `id` from the verified body; the event and
delivery headers are not signed. Deduplicate by event `id` to avoid
repeating an action, including replays within the age limit. Each delivery
attempt gets a fresh signature timestamp.

Delivery makes at most six attempts, with retry delays of 10 seconds,
1 minute, 5 minutes, 30 minutes and 2 hours. A `2xx` response succeeds;
other responses retry until the limit. Redirects are not followed.

## Fact indexing

After a thread is stored, a separate background job extracts named relations and explicit decision/preference sentences. It runs locally with deterministic rules. Extracted facts point to the exact stored messages and live in `private_threads` with private sensitivity. Secret-like and instruction-like messages are excluded from the fact index; the lossless chat stays verbatim.

The connection needs permission to read the source thread (`threads:read` and its source wing), plus the existing `fact_save` permissions for `private_threads`. Extraction uses the gateway's normal policy and privacy checks. It runs independently of mirroring and successor launch, so an unavailable fact worker or a slow embedding job does not block the handoff.

Extraction progress is stored beside the chat. A repeated or extended handoff resumes unfinished messages. A crash between saving a fact and committing progress may replay the save; the fact row and source pointer are deduplicated. Unsupported wording remains in the full chat and its search mirror. Historical relations require an explicit date, and automatic extraction makes no model or external API calls.

Run the offline extraction eval with `python benchmarks/fact_recall_bench.py --saver extract --threads 120`. Its gold fixture includes old-thread recall, changed values, exact merged sources, private facts and a two-message fact.

## Failure behaviour

| Failure | Result |
|---|---|
| Handoff is off | Nothing is stored or launched; normal compaction continues |
| Transcript parsing or storage fails | No successor starts; `handoff.failed`; normal compaction continues |
| The additional Memory search index cannot be updated | The full thread stays saved; handoff can continue |
| Server command or webhook launcher fails | The thread stays saved; `handoff.launch_failed`; the old agent is told to stop |
| Hook configuration, HTTP, timeout or response error | The hook exits successfully and silently; the agent continues on its normal path |

Owners can retry a recorded launch failure with
`commonplace handoff relaunch <handoff_id>`. Inspect the saved handoff
before starting a successor manually.

When the hook's own successor command fails, it reports
`POST /api/handoffs/<id>/launch-failed`, which records `launch_failed`
and sends `handoff.launch_failed`; the old agent keeps working and
compacts as usual.

Thread storage stays local. Requests go to your configured Commonplace
gateway and your configured webhook or launcher URLs. Event webhooks
carry references only. A webhook launcher receives the handoff prompt
because it must start the successor.

## Idempotency and the chain

The default idempotency key is `thread:<client thread id>`, unique within
the workspace. Retries use the same key. Only one response claims the
right to launch the successor. Registering the same successor twice is
a no-op; registering a different successor for that handoff is rejected.

Saving the same source thread and content returns the existing stored
thread. When a retry carries the same thread with more messages at the
end (the agent kept working), only the new messages are appended to the
stored thread; the handoff and its successor stay the same. Other
different content under the same source ID is a conflict. A failed
storage attempt can be retried. A handoff stuck mid-store after a crash
is taken over by the next retry once its 10-minute lease runs out. A
recorded launch failure needs an explicit owner relaunch to release its
launch claim.

When a successor's own thread ID was registered with its handoff, its
next handoff links into the chain even without `COMMONPLACE_HANDOFF_ID`.

When the successor later hands off, its hook sends
`COMMONPLACE_HANDOFF_ID` as `parent_handoff_id`. This links the stored
threads. `thread_chain` lists the current thread and then older ones;
`thread_recall` searches that same readable chain by default. The handoff
document's earlier-thread pointers are also newest first.

The card view at `/app/cards/142` shows handoff state for `#142`, with the
old thread and the successor reference or URL.

## Limits and open questions

- A single `thread_get` reads at most 200 messages. Continue with another
  range; stored messages are never shortened.
- Handoff quotes are limited to 1,200 characters and at most eight
  decision quotes. Use their full-range pointers for the rest.
- Request limits are 64 MB for `/api/handoff` and 1 MB for
  other JSON requests.
- A Codex handoff happens at the start of the next turn, not mid-turn;
  see the Codex section. If that successor fails to start and the gateway
  restarts before an owner relaunches it, the relaunched successor doesn't
  get the user's unanswered message (the control database holds no text);
  the old session showed it was refused.
- Each stored thread is also written into Memory search in the background,
  in a private section: ordinary Memory tools show it only to tokens with
  `private:read`. The thread tools need `threads:read`.
- `handoff.completed` is queued in the same transaction that records the
  successor and delivered exactly once, also after a crash (it goes out
  when the gateway starts again) and with several gateways on one control
  database. A webhook receiver may still see one delivery twice if the
  hub crashed after the receiver accepted it; deduplicate by delivery id.
