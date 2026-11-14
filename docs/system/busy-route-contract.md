### The busy refusal contract is status and code

- The lock-timeout refusal on a board route is a 409 with code `busy`; that pair is what clients and tests rely on. The card routes (add, update) add a plain sentence and a retry hint, the orchestrator routes keep the bare reason.
- Decision: route tests assert status and code exactly, and the sentence separately per route family, so wording can improve on one family without breaking the other.
