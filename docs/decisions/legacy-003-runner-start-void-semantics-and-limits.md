### Runner-start void semantics and limits

- A same-step retry of an unvoided step returns the stored job; any mismatch is a 409 with the stored step.

- A voided runner-start key instead returns `step_voided` (409), even for a changed request; other kinds retain `step_conflict`.

- Cancelling a job that belongs to a step is person-owner only through the standalone cancel route.

- Owner/local void also cancels linked queued or leased jobs atomically.

- Voided steps keep counting their jobs; refusal is `at_cap` with workspace orchestration off and `project_limit` with it on.

- Owner/local void permanently retires a runner-start key while preserving its request, link, state, evidence and history.

- Only queued/leased jobs are cancelled, in the void transaction; starting/uncertain/started and finished jobs are untouched.

- A new key goes through ordinary admission, including non-person rules for owner orchestrator calls.

- Repeat void preserves the first actor/time and writes nothing; one journal event may produce repeated successful-call feed notifications.

- Checkpoint jobs retain linked statuses.

- Void cannot stop or prove completion of a launched thread.

- Active jobs block concurrent starts, but the 24-hour non-person rule can later allow a new job on a todo, unassigned card while an earlier thread still runs.

- Caps count cards, not threads: started jobs age out after one hour unless a doing card with a connection assignee still counts; opt-in orchestration retains the todo/doing recent-start hold and ordinary connection slots.

- Uncertainty needs separate owner truth checks and resolution; empty thread search proves nothing.

- Void does not launch or replay triggers, but a pending unswept todo event can start the card on a later sweep after cancellation frees it.

- Trigger keys (`trig:<event>`) and owner `card_start` are independent of step keys; the latter also bypasses the non-person 24-hour rule.

- These limits predate void semantics.

