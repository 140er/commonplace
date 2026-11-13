"""Behavioral witness that also runs unchanged against the pre-consent base."""

# ruff: noqa: F811
from commonplace.runner import install_receipts, registry
from tests.test_runner_runtime_install import ready, setup  # noqa: F401


def test_owner_gets_machine_fingerprint_before_final_approval(ready):
    s = ready
    s.local.sync_receipts()
    row = registry.get_machine(s.db, s.owner.workspace_id, s.config["machine_id"])
    view = install_receipts.owner_view(s.db, row)
    assert "setup_approval" in view, "installation must expose fingerprint-bound browser consent"
    assert len(view["setup_approval"]["fingerprint"]) == 64
    assert view["setup_approval"]["agent_ready"] is False
