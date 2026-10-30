# Context handoff

## What it does

- When a thread nears its context limit, handoff saves it in Memory.
- Every message, tool call and tool result stays verbatim.
- A fresh agent starts with a short handoff and a pointer to that thread.
- It can retrieve the earlier words when it needs them.

## Turn it on

Handoff is off by default. With it off, existing behaviour and normal
compaction stay unchanged.

Open `/app/login` on your Commonplace gateway and sign in with a `cmmn_…`
token. Open `/app/settings/memory` and turn on **Handoff**. Workspace owners
can change the switch, threshold and agent or task overrides.

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

Windows come from a model-name table. A name containing `[1m]` means a
1,000,000-token window. The default window is 200,000 tokens. Set
`context_window` through the API, or use `--window` on `handoff enable`,
to supply the correct window for your model.

Unknown usage does not trigger a threshold handoff. `PreCompact` is the
backstop: when handoff is enabled, it requests a handoff even if the
threshold has not been reached or usage is unavailable.

<!-- verify -->
<!-- T15: reconcile hook.py's no-network fast path with the brief's effective-settings cache, refreshed every 60 seconds. -->
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
nested `hooks` object and handler groups. This example checks at `Stop`
and uses `PreCompact` as the backstop.

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

<!-- verify -->
<!-- T15: confirm Codex stop/compaction suppression. Its brief allows systemMessage if no stop mechanism exists. -->
The Codex stop mechanism still needs verification. A `systemMessage`
alone does not establish that the old agent stops or compaction is
cancelled. Do not rely on automatic replacement until that is confirmed.

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

<!-- verify -->
<!-- T15: confirm missing-command fallback, T3 thread ID/URL capture, forwarding COMMONPLACE_HANDOFF_ID into the actual T3 agent, and branch/worktree selection. -->
Configure the command before enabling handoff. With no command, the client
hook's planned fallback is to show the prompt file path for manual startup.
For T3, thread ID and URL capture, forwarding the handoff ID into the new
agent, and selection of the working branch or worktree need verification.

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

The MCP tools require `threads:read`, an allowed tool name, and permission
to read the stored thread. The `standard` and `full` profiles include the
tool names; explicit `--tools` lists can name them too. The profile does
not replace the scope or data-access checks. An inaccessible thread looks
like a missing thread.

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
| `end` | integer or null | `null` | Last message number, inclusive; null means the end |
| `include_raw` | boolean | `false` | Include each message's original source record |

Message numbers start at zero. Each call returns at most 200 messages.
Request further ranges to read a longer thread. For example, `start=20,
end=24` reads five messages.

<!-- verify -->
<!-- T12 adds include_raw in its brief, but mcp_tools.py's shared signature omits it. Confirm schema and default after merge. -->

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
| POST | `/api/handoffs/<id>/relaunch` | owner | Retry a failed launch |
| GET | `/api/threads/<id>` | `threads:read` | Stored thread metadata |
| GET | `/api/threads/<id>/messages?start=0&end=99` | `threads:read` | Verbatim messages in the inclusive range |
| POST | `/api/threads/<id>/recall` | `threads:read` | `{query, limit?, follow_chain?}` → search hits |
| GET | `/api/threads/<id>/chain` | `threads:read` | This thread and readable predecessors |
| GET | `/api/settings/handoff?agent=&task=` | authenticated token | Saved rows and effective settings |
| PUT | `/api/settings/handoff` | owner; members: task `enabled` only | `{scope, ref, enabled?, threshold?, context_window?, launcher?}` |
| DELETE | `/api/settings/handoff?scope=&ref=` | owner | Remove an override |
| GET | `/api/webhooks` | owner | Webhook registrations, without secrets |
| POST | `/api/webhooks` | owner | Register a webhook; secret returned once |
| DELETE | `/api/webhooks/<id>` | owner | Remove a webhook |

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

Handoffs write audit entries and enqueue events for configured webhooks.
Delivery runs in the background. Register your own receiver with:

```bash
commonplace webhook add http://127.0.0.1:9000/handoff --workspace personal
```

The command prints the signing secret once. Store it at the receiver.

| Event | Meaning |
|---|---|
| `handoff.started` | A handoff began |
| `handoff.stored` | The full thread and handoff are saved |
| `handoff.completed` | The successor was registered |
| `handoff.failed` | Storage failed; use normal compaction |
| `handoff.launch_failed` | The thread is saved but successor launch failed |
| `handoff.settings_changed` | A saved setting changed |

The event payload contains references, never thread text:

```json
{
  "event": "handoff.completed",
  "workspace": "ws_example",
  "handoff_id": "hof_example",
  "stored_thread_id": "thr_example",
  "task_ref": "#142",
  "successor_agent_ref": "t3:example",
  "at": "2026-10-30T12:00:00+00:00"
}
```

Failure payloads may also contain `decision` and a short `reason` without
thread text. References that are not yet available may be empty.

The receiver gets a JSON POST with `X-Commonplace-Event`,
`X-Commonplace-Delivery` (the outbox ID), and
`X-Commonplace-Signature: sha256=<hex digest>`. The signature is
HMAC-SHA256 using the UTF-8 secret and the exact request body bytes.
The sender uses sorted JSON keys without spaces. Do not parse and
re-serialize the body before checking it.

```python
import hashlib
import hmac
import json


def verify_handoff(body: bytes, signature: str, secret: str) -> dict:
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("invalid signature")
    return json.loads(body)
```

Pass the unmodified body and the `X-Commonplace-Signature` header to this
function. Read the event from the verified body: the other headers are
not covered by the signature. The contract defines no timestamp tolerance
or replay window. Verification alone does not reject replays.

Handle repeated deliveries without repeating your action. Delivery retries
after 1 second, 5 seconds, 30 seconds, 2 minutes and 10 minutes, then gives
up. A `2xx` response succeeds; `410` gives up immediately. Delivery does
not follow redirects.

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

<!-- verify -->
<!-- T10/T15: define how a client-side launch failure reaches launch_failed; no reporting endpoint is specified. -->
The contract does not yet define how the client reports a local launch
failure. Do not assume that such a failure is recorded as `launch_failed`
or is eligible for `relaunch`.

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
thread. Different content under the same source ID is a conflict. A
failed storage attempt can be retried. A recorded launch failure needs an
explicit owner relaunch to release its launch claim.

When the successor later hands off, its hook sends
`COMMONPLACE_HANDOFF_ID` as `parent_handoff_id`. This links the stored
threads. `thread_chain` lists the current thread and then older ones;
`thread_recall` searches that same readable chain by default. The handoff
document's earlier-thread pointers are also newest first.

The card view at `/app/cards/142` shows handoff state for `#142`, with the
old thread and the successor reference or URL. It does not require a full
board implementation.

## Limits and open questions

- A single `thread_get` reads at most 200 messages. Continue with another
  range; stored messages are never shortened.
- Handoff quotes are limited to 1,200 characters and at most eight
  decision quotes. Use their full-range pointers for the rest.
- The planned request limits are 64 MB for `/api/handoff` and 1 MB for
  other JSON requests.
- Model windows must match the model you run. Use an explicit override
  when the model-name table is insufficient.
- Codex stop behaviour, T3 successor identity and context, client
  launch-failure reporting and the `include_raw` tool argument need
  integration checks at the verification markers above.
- Webhook signatures do not define replay protection or an expiry policy.
- Board task events remain future work; the handoff record and task view
  carry the references for now.
