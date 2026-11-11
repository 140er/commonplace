"""Rules injection tests give the same answers on a host too slow for the budget."""

from tests._slow_host import run_under

FUNCTIONAL = [
    "tests/test_commonplace_rules_inject.py::test_authorized_text_scopes_and_owner_edits",
    "tests/test_commonplace_rules_inject.py::test_moved_rule_and_subagent_get_only_their_own_rules",
    "tests/test_commonplace_rules_inject.py::test_snapshot_served_state_and_no_binding_served_or_events",
    "tests/test_commonplace_rules_inject.py::test_lost_bound_store_has_only_header_and_pointer",
]


def test_functional_injection_tests_pass_when_every_clock_read_is_slow():
    run_under(["tests._slow_host_plugin"], FUNCTIONAL)
