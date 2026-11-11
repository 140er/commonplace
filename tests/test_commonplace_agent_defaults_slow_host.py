"""Onboarding acceptance gives the same answers on a host too slow for startup budgets."""

from tests._slow_host import run_under

ACCEPTANCE = [
    "tests/test_commonplace_agent_defaults.py::"
    "test_default_cli_onboarding_installs_working_native_startup_and_warns_on_local_edits",
    "tests/test_commonplace_agent_defaults.py::"
    "test_onboarding_import_save_and_owner_approval_remain_one_canonical_flow",
]


def test_onboarding_acceptance_passes_when_the_hub_and_every_clock_read_are_slow():
    run_under(["tests._slow_hub_plugin", "tests._slow_host_plugin"], ACCEPTANCE)
