- **Tests:**

  - `tests/test_coworker_model_turns.py` exercises the coworker creation model/effort contract and unified composer through actual private stores, synthetic canonical runner ledgers and stubbed transports; validates missing/default/unknown options, mode/fallback decisions, immutable retries and unchanged authority without contacting a provider.
  - The full composer regression file also records control/writer acquisition order on every view route and runtime, forbids model policy reads in writer scopes, checks active origins behind over 100 finished jobs and verifies bounded effort-provider errors through the runtime.

  - Creation/list/detail/restart persistence checks compare every stored conversation field while allowing additive directory metadata on list responses. Directory section, unread, state and count defaults are asserted separately; detail excludes only the live `owns` projection, and restart and audit assertions remain exact. The directory API regressions retain the bounded-query and visibility checks with model/effort present.
