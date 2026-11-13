### Card-lifecycle rule replay

- `tests/test_statechart_card_rules.py` keeps one proof per rule of the card-lifecycle statechart replay: release of restricted and subagent cards by the live orchestrator, subagent questions to the orchestrator, approvals offered to the owner only from Needs me, claimed manual assignments, starts by an owner-trusted seat, the machine limit past an hour, and completion reaching the recommended seat webhook. It changes no behavior; a seat without the owner's trust confirmation still cannot request starts.
