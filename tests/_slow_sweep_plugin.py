"""Pytest plugin: make search-index sweeps behave as on a starved host.

Every sweep starts late and `wait_for_mirrors` gives up after a hundredth of
the budget the caller passed. A test that trusts one fixed wait budget sees
unfinished sweeps, exactly as it would when the host is heavily loaded.
"""

import time

from commonplace.handoff import search_index
from commonplace.handoff.service import HandoffService

SWEEP_DELAY_SECONDS = 0.1
BUDGET_SCALE = 0.01


def pytest_configure(config):
    sweep = search_index.sweep
    wait = HandoffService.wait_for_mirrors

    def slow_sweep(*args):
        time.sleep(SWEEP_DELAY_SECONDS)
        return sweep(*args)

    def short_wait(self, timeout=None):
        return wait(self, None if timeout is None else timeout * BUDGET_SCALE)

    search_index.sweep = slow_sweep
    HandoffService.wait_for_mirrors = short_wait
