"""Onboarding acceptance gives the same answers when the hub is slower than the hook allows.

Every gateway answer takes longer than the startup hook's 0.4 s allowance
(``tests/_slow_hub_plugin.py``). The deadline clock is not starved here: the
acceptance tests run on a frozen deadline clock (``frozen_deadline_clock``), so
a starved clock could not reach them.
"""

from tests._slow_host import run_under

ACCEPTANCE = [
    "tests/test_commonplace_agent_defaults.py::"
    "test_default_cli_onboarding_installs_working_native_startup_and_warns_on_local_edits",
    "tests/test_commonplace_agent_defaults.py::"
    "test_onboarding_import_save_and_owner_approval_remain_one_canonical_flow",
]


def test_onboarding_acceptance_passes_when_every_gateway_answer_is_slower_than_the_hook_budget():
    run_under(["tests._slow_hub_plugin"], ACCEPTANCE)
