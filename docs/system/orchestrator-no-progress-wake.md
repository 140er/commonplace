### Orchestrator no-progress wake

- The hub measures progress, not liveness: a runner-configured board with actionable open work and no card done for N minutes (default 20, per board, 0 = off) emits one `board.stalled` wake through the existing event and webhook path, with backoff and owner escalation after three unanswered nudges. Boards whose open cards all wait on a blocker or a person are never nudged.
