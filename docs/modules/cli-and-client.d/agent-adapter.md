- **Owns:**

  - The agent adapter contract (`docs/agent-adapter.md`) and its reference implementation `commonplace/adapter.py`: register, take, heartbeat, report and release for any agent type over the canonical tools; `token add --preset coding|chat` gives any client name a named policy preset.

- **Promises:**

  - The adapter is agent-type neutral: it calls only `session_start`, `task_take_next`, `task_heartbeat`, `task_update` and `task_handoff`, heartbeats after two thirds of the claim, and never guesses at a reply outside the documented shapes. Without `--preset`, `token add` picks the preset by client name exactly as before.

  - The client raises `ToolRefused` only for the hub's explicit `no:` refusal; a timeout or any other tool error stays a plain `ClientError` with the same safe message.

- **How it fails:**

  - A refused heartbeat raises `ClaimLost` (stop working on the card); a transport failure stays a `ClientError` and says nothing about the claim; an unexpected reply raises `AdapterError`.

- **Tests:**

  - `tests/test_agent_adapter_conformance.py` drives a fake agent type and a built-in one through the adapter against the real gateway, including claim expiry and return.
