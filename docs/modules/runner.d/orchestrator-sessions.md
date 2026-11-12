- **Promises:**

  - Missing/archived boards retire session commands and acknowledge results without replaying host effects. Potentially running threads retain their binding and uncertainty; storage failures remain retryable, and status/control handle board loss explicitly.

  - Owner stop never clears an unknown-effect marker. Session leasing filters by machine and skips bad boards, session failures leave per-card leasing available, and fenced local refusals expose their code without host effects.

  - Persistent board sessions are opt-in (`[runner] session = true`) and use accepted runner authority without todo triggers, retain thread bindings and durable owner stops, continue actionable boards and leave clear boards idle. Ambiguous effects and changed bindings fail closed; per-card worker slots remain separate.

- **Tests:**

  - `tests/test_runner_session_board_loss.py` covers board loss, including journal clearance during active turns.

  - `tests/test_runner_session_regressions.py` covers session availability, journal and transport failures and local refusals.

  - `tests/test_orch_sessions.py` proves the six lifecycle contracts; `tests/test_runner_session_guards.py` covers authority, races, duplicate leases and uncertain recovery.

