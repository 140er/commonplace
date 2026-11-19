- **Owns:**

  - Chat-relay cost reporting: `chat_ledger.py` gives each chat job a `run_ledger` row (`source = 'chat'`, the board as its project, the leasing machine's worker connection as its agent) and one cost event at its end; `chat_relay.py` checks budgets when a chat message is admitted and when a runner leases it; the answer and failure routes take optional token and cost fields, which `chat_agent.py` fills from the CLI's JSON result (`_unwrap`: token counts and the reported cost only). Design in `docs/system/cost-budgets.md`.

- **Promises:**

  - A chat job's tokens, model, provider and cost, or an explicit unknown, are in the same ledger and sums as card runs, once per job. A new chat start on a workspace, agent connection or project at or over its hard budget is refused with `budget_exceeded` and a plain reply in the chat, a queued one is skipped at lease, a running turn finishes, and chat spend feeds the 80% owner warning. Unknown cost is counted apart and never blocks. The runner chat client sends the usage its provider CLI reported (never prompt text, completions, paths or secrets); a CLI that reports none, or a zero cost, stays unknown. The project scope of chat is the board slug (a same-named runner project counts with it) and an agent budget skips a chat job its machine leases without moving it to another machine; both are documented in the design note.

- **How it fails:**

  - A refused chat start is stored as a skipped job with `budget_exceeded` and shown as the chat status's last error; the receipt stays. A cost report that cannot be written is logged and the answer still stands; the job's run is then ended as uncertain with an unknown cost. A retried or late answer is fenced and writes nothing more.
