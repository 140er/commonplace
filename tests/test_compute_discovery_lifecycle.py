"""Trusted discovery installation must never upgrade a local-only fence."""

from types import SimpleNamespace

from commonplace.compute import discovery_service
from tests.test_compute_control import compute_env as compute_env
from tests.test_compute_executor import execution_env as execution_env
from tests.test_compute_fence import local_state as local_state


def test_missing_configuration_is_inert(compute_env, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("default-off installation constructed a worker")

    monkeypatch.setattr(discovery_service, "configure", forbidden)
    discovery_service.install_lifecycle(compute_env.gateway)
    assert discovery_service.configured_service(compute_env.gateway, compute_env.workspace) is None


def test_trusted_local_fence_never_becomes_remote_discovery_authority(local_state, monkeypatch):
    state, env = local_state, local_state.env

    def forbidden(*args, **kwargs):
        raise AssertionError("local-only authority reached custody")

    monkeypatch.setattr(discovery_service.DiscoveryService, "_custody_store", forbidden)
    workspace = SimpleNamespace(
        workspace=env.workspace,
        discovery_enabled=True,
        discovery_sources=("linux_arp",),
        wrapping_key_path=state.key_path,
        provisioning_keys=state.service._provisioning_keys,
    )
    env.db._compute_config = SimpleNamespace(workspaces=(workspace,))
    discovery_service.install_lifecycle(env.gateway)
    service = discovery_service.configured_service(env.gateway, env.workspace)
    assert service is not None and service.fence is state.fence
    assert service in env.db._compute_services
    assert not service.running and not service.available()
    assert env.gateway._compute_discovery_reasons[env.workspace] == "discovery_unavailable"
    assert service._threads == []
