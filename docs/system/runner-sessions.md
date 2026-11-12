### Runner-owned persistent board sessions

- An owner-accepted live `[runner]` with `session = true` (optional, default
  false, so existing configs are unchanged) enables a separate
  `runner_sessions` record, without `[[triggers]]`, a manual watch/webhook or
  the workspace splitting switch. No accepted runner is `no_host: no host
  attached`; a runner without the key is `no_host: sessions not enabled`, makes
  no host call and starts no wake. No machine is selected implicitly.
- The runner launches the parentless orchestrator role with the accepted
  machine/project/runtime, its role topic/instructions and resolved model,
  retains the returned thread binding, inspects host turns and continues the
  same thread while actionable cards remain. Card worker jobs stay separate.
- Only Done, unresolved `task_blockers` and person-directed asks clear work.
  Blockers show dependency names or the question. Backlog, Doing, split children
  and capacity-saturated cards remain actionable. Completed turns cannot make
  an actionable board idle; clear idle boards produce no host calls or model
  turns, and new/unblocked work is found on the next runner pass.
- Project GET includes session state, reason and pending command. Only owners
  receive the thread binding and actionable/blocked card details. Signed-in owners POST `{}` to project
  `orchestrator/stop` or `orchestrator/resume`; stop persists across new work
  until resume. `stopping` distinguishes a pending interrupt from `stopped`.
- Machine-only session lease/starting/result routes fence every host effect
  against accepted config, the accepting owner's current requester identity,
  machine binding epoch and grants, wing and model. Restore flags/markers
  refuse effects. Runner then control locks cover guarded admission/commit;
  board reads inside runner transactions are read-only.
- Journals preserve ambiguous outcomes as `uncertain`, never automatic replay.
  Stop during launch preserves its acknowledgement and schedules interrupt.
  Changed configs, bindings or models fail closed. Recovery/rebinding, richer
  owner UI and service activation remain follow-up work; existing host
  credentials are neither switched nor elevated. Sessions do not occupy
  per-card worker capacity.
- Owner stop preserves unknown-effect state and markers, so stop followed by
  resume cannot relaunch an ambiguous effect. Session leasing filters by machine
  before board reads and skips each unavailable board independently.
- Missing/archived boards close leased commands without host effects and
  acknowledge results so the machine journal can clear. Observed completion
  retires idle/stopped; possibly running threads retain their binding and become
  uncertain. Existing uncertainty and owner stop survive. Status safely reports
  unavailable; a stop committed before board loss remains acknowledged. Storage
  errors still fail closed rather than count as an empty board.
- Invalid session journals are quarantined, and session-pass failures do not
  prevent per-card leasing in that pass. Before starting, a machine can report a
  fenced `refused` result with an allowlisted code; status shows `unavailable`
  and the reason without making a host call.
- Migration only adds `runner_sessions`; existing card jobs and their grants,
  leases and history are untouched. Read-only project status does not create
  an absent runner database. Existing per-card event sweeps and bounded
  split-root reconciliation require `card.todo`; neither runs for a triggerless
  runner config. Persistent session watches remain independent.
