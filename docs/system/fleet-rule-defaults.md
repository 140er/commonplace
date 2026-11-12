### Fleet rule defaults

- Every journal step persists a result destination on insert: its immutable
  owning card (`kind=card`, board and card number), or an explicit park
  (`kind=parked`, `reason=no_owning_card`). This also covers immediate `next`
  notes, gate verdicts and job-derived completions. Checkpoints expose each
  destination and a bounded list of parked finished results. An additive,
  atomic migration fills this metadata for existing steps without rewriting
  their content, evidence, states or journal events. Destination metadata is
  routing state, with no claim of session delivery.

- Fleet rules are defaults of every orchestrated board; the accepted config's
  optional `[fleet]` table overrides them per board (`repair_attempts` 3,
  `route_findings` true, `reuse_agents` true, `agent_cards` 2). Experiments
  cannot change it.

  - Repairs: the check is a gate, the work is the pull request across its heads.
    The first `gate_fail` opens the one repair with the recording role as owner;
    later failures from any role join it; `gate_pass` resolves it. At the bound
    the repair is parked (`repair_exhausted`) and `gate_open` refuses the pull
    request until the owner raises the bound. Repairs are rows of `orch_repairs`
    (additive table) with a partial unique index on the open repair per pull
    request.

  - Findings: a failed gate, or `orch_finding`, is routed by the hub to the role
    whose lease started the card's latest unvoided runner start while that lease
    is live (`orch_findings`, additive table); otherwise parked with a reason.
    The owner acknowledges with `orch_finding_ack`. Liveness is the lease, the
    only session signal the hub holds; there is no inferred session.

  - Fresh agents: `orch_card_start` refuses (`agent_has_capacity`) while a
    started agent that holds the context of the card, its parent or its children
    has capacity, unless `fresh_reason` is given; the step records why a fresh
    agent started (`orch_steps.fresh_reason`, additive column). The context
    window is six hours, a fixed constant. Person and trigger starts are not
    subject to the rule. Pending: a runtime-reported live capacity signal; the
    hub counts only the unfinished cards of a started thread.
