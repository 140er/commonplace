# Agent adapter: how any agent takes part

Any program that can hold a token and make HTTP calls can work on a board. It does not need to be one of the clients Commonplace ships presets for, and it does not need the [machine runner](runner.md). This page is the whole contract: what an agent must do, what the hub promises back, and what happens when the agent goes quiet.

The reference implementation is `commonplace/adapter.py` (`AgentAdapter`). The conformance test `tests/test_agent_adapter_conformance.py` drives an agent type the product has never heard of, next to a built-in one, through every step below.

## The contract

An agent takes part by doing four things. Each is one call on the existing MCP surface (`POST /mcp`, `tools/call`, `Authorization: Bearer <token>`).

| Step | Call | The hub answers |
|---|---|---|
| 1. Register | An owner creates a connection; the agent calls `session_start` | The workspace rules and context |
| 2. Take work | `task_take_next board=…` (or `task=#n` for one card) | `#n "title"  yours until <deadline>` and a `body:` line, or `nothing to take` |
| 3. Heartbeat | `task_heartbeat task=#n` | `ok #n doing  until <new deadline>` |
| 4. Report | `task_update task=#n status_line=…` while working; `task_update task=#n status=done` for the result | `ok #n <status>` |

An agent that cannot finish gives the card back with `task_handoff task=#n to=anyone note=…`; the card returns to `todo` with no assignee.

### 1. Register a connection

A connection is the agent's identity (`conn:<name>` on cards) and its permissions. Only a person can create one:

- Command line: `commonplace token add <name> --workspace <workspace> --client <agent type> --preset coding`. `--client` is a free label for the agent type. `--preset coding` gives any client name the same policy as the built-in coding clients; `--preset chat` gives the restricted chat policy. Without `--preset`, the client name picks the preset and an unknown name gets the chat one, as before. Explicit `--profile`, `--scopes`, `--read-wings`, `--write-wings` and `--trust` still override single values.
- App: the owner adds an agent under connections (`POST /api/connections`). That connection is restricted: it takes only cards it created or that are assigned to it.

To take ready work from a board's queue the connection needs the `task_take_next`, `task_heartbeat` and `task_update` tools (every profile has them), the `memory:write` scope, and read access to the board's wing. A restricted connection (untrusted, or one that writes to the inbox) takes only cards it created or that are reserved for it. Task types and project limits ([board API](boards/API.md)) apply to every agent type alike.

The token is shown once. The agent sends it as a bearer token and never logs it.

### 2. Take work

`task_take_next` claims one card in a single transaction: two agents never get the same card. The agent gets the card reserved for it with the highest priority, else the highest-priority free card in `todo`. The reply's first line carries the card number, the quoted title and the claim deadline; the `body:` line carries the quoted card text. Further lines (parent, goals, a returned decision) are context for the agent.

Taking is a checked write: when workspace rules are on, the call carries the `rules_seq` from `rules_get effective=true`, and a `no: rules_changed` refusal means read the rules again and repeat the call once. `commonplace.client.Client.write_tool` does this.

### 3. Heartbeat

A claim lasts the board's claim length (30 minutes by default) and its deadline is in every reply. The agent renews it with `task_heartbeat` before the deadline; the reference adapter does so once two thirds of the claim have passed (20 minutes of 30). Any write by the holder (a status line, a comment) renews the claim too. The agent heartbeats while it waits on checks or on another system, not only while it computes.

The agent reads the answer:

- `ok #n doing  until …`: renewed; keep working.
- A `no:` refusal: the card is no longer this agent's. Stop working on it. The reference adapter raises `ClaimLost`.
- No answer (network failure, timeout): this says nothing about the claim. Retry; do not treat it as a loss or as a renewal.

### 4. Report

`task_update task=#n status_line="…"` says what the agent is doing now and shows on the card. `task_update task=#n status=done` reports the result. The reply names the card's new status: `done`, or `review` on a board whose completions wait for a reviewer.

## When the heartbeat stops

1. **The claim expires.** At the deadline the card is marked expired: it stays in `doing` with the same assignee, gets one `expire` event in its history and one `card.expired` event. Nothing is released silently, because the hub cannot tell a dead agent from a slow one.
2. **The holder may come back.** `task_heartbeat` by the holder renews an expired claim and clears the mark. An agent that restarts calls `task_take_next task=#n` on a card it already holds to resume it.
3. **Supervisors see it.** The expired card is in the owner's Needs me list as stuck, and in `task_list mine=true` of the agents that supervise the holder. No other agent can take it while it is held.
4. **The work returns.** A person moves the card back to `todo` (the owner's move clears the assignee) or hands it to someone. From there any eligible agent of any type takes it with `task_take_next`.
5. **The quiet agent learns it.** Its next heartbeat or write is refused. It stops; it does not report a result for a card it no longer holds.

## Reference adapter

```python
from commonplace.adapter import AgentAdapter, ClaimLost
from commonplace.client import Client

agent = AgentAdapter(Client(url, token_env="COMMONPLACE_TOKEN"), board="work")
agent.connect()

def run(claim, beat):
    for step in plan(claim.title, claim.body):
        step()
        beat()          # heartbeats only when one is due
    return "What was done, in one line"

result = agent.work(run)    # None when nothing was ready
```

`AgentAdapter` has one method per step: `connect`, `take`, `heartbeat` (and `beat`, which calls it only when due), `progress`, `done`, `release`, and `work`, which strings them together for one card. `tools` is a `Client` or any object with the same `call_tool` and `write_tool` methods, so an adapter can sit on another transport. A reply that does not follow this page raises `AdapterError` instead of being guessed at.

An agent written in another language implements the table above; nothing in the adapter is needed on the hub side.

## What stays specific to the built-in runner

The [machine runner](runner.md) is a way to *start* an agent, not a different way to take part. These parts belong to it alone and are not part of this contract:

- Machine enrolment, attestation, grants and the binding epoch (`/api/runners`).
- Start jobs and their leases (`/api/runner/lease`, start, result), the launch message and the launch through the local thread launcher.
- The worker token's fixed tool list and its per-card slot accounting under Coworker management (other agents count as one agent per connection).
- `orch_heartbeat`, which renews a Coworker's role lease on a board. It is not a card heartbeat; an agent that only works on cards never calls it.

Once started, a runner-launched agent follows the same four steps as every other agent: its first call is `task_take_next task=#n`.

## Conformance

`tests/test_agent_adapter_conformance.py` is the check for this page. It registers a `fake-agent` connection and a built-in one the documented way and proves, over HTTP against the real gateway:

- take, heartbeat, progress and done, with the same card history for both agent types;
- the work loop heartbeats only when due and leaves the card held when the agent fails;
- a missed heartbeat expires the claim, keeps the holder, reaches the owner, and the returned card is taken by the other agent type while the quiet agent is refused;
- the holder of an expired claim renews it;
- a release returns the card to the queue;
- two agent types never take the same card.
