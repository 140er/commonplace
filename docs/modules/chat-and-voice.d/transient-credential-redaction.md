- **Promises:**

  - `CoworkerWorker._context` applies the existing front-chat credential redactor to the transient outbound bot name, complete persona before its existing slice, current message, selected memory copies and history text before JSON serialization. Stored values keep their original content; selected memory snapshots used by freshness checks remain original. Provider credentials and existing authority, configuration and input-budget checks keep their separate channels and behavior.

- **Tests:**

  - `tests/test_chat_coworkers_runtime_redaction.py` captures real front/coworker worker serialization at the transport boundary and checks retained originals, both provider envelope formats, context budgets, refusal controls, memory freshness and the explicit task branch with synthetic data and a denied provider connector.
