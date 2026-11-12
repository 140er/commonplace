- **Owns:**

  - The optional `[factory]` merge policy of a config (`factory.py`): gate requirement, named checks with their pass and never-pass outcomes, full-suite cadence; its canonical form, summary lines, the journal's merge guard and the checkpoint's gate counts.

- **Promises:**

  - A config without `[factory]` keeps its canonical form, render, journal behavior and checkpoint keys; `factory` appears in `orch_config_get` and the checkpoint only when the accepted version declares it. Timeout, skip and error outcomes can never be declared a pass. The hub enforces the gate requirement only; checks and batch cadence are declared data.

- **How it fails:**

  - With `[factory]` and `gate.required = true`, a new `merge` step without a card is refused `factory_card_required` and one whose card's newest unvoided gate is not `gate_pass` is refused `factory_gate_required` (409, details: the gate); nothing is written. A `[factory]` that contradicts itself is refused `factory_contradiction` at propose, write and accept.

- **Tests:**

  - `tests/test_orch_factory.py` covers the `[factory]` schema (a valid policy, each contradiction, structure refusals, diff and the experiment rule), the policy in `orch_config_get`, render and the checkpoint with gate counts, the merge guard for every gate verdict, another card's gate, void and retry, a newly accepted version changing what is enforced, and a board without a policy.
