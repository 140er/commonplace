### Agent adapter contract

- Any connection takes part in a board through the same five tools (`session_start`, `task_take_next`, `task_heartbeat`, `task_update`, `task_handoff`); `docs/agent-adapter.md` is the contract and `commonplace/adapter.py` its reference implementation. The hub has no per-agent-type branch on that path.
- A missed heartbeat expires the claim and keeps its holder: the card is never released automatically. The holder's heartbeat renews it; otherwise a person returns it to `todo`, after which any eligible agent takes it. The quiet agent's next call is refused.
- `token add --preset coding|chat` selects a policy preset for any client name. It grants nothing a caller of `token add` could not already pass as explicit flags, and the default by client name is unchanged; app-created connections stay restricted.
- The client's `ToolRefused` marks only an explicit `no:` refusal. A timeout or transport failure says nothing about a claim, so the adapter never reads one as a loss or a renewal.
