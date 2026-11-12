"""Compute management plus explicit trusted, default-off service configuration.

The host may pass ExecutionConfig to ControlDB at service startup. Configuration
is per workspace, never request input or environment discovery. Only already
provisioned workspace/custody files are accepted; no private key is initialized
or read by imports, management requests, or lifecycle installation.
"""

from dataclasses import dataclass, field
from pathlib import Path

from .script_control import ScriptRuntimeConfig


@dataclass(frozen=True)
class WorkspaceExecutionConfig:
    workspace: str
    directory: Path
    wrapping_key_path: Path
    provisioning_keys: dict[str, bytes] = field(default_factory=dict)
    enabled: bool = False
    admission_mode: str | None = None
    discovery_enabled: bool = False
    discovery_sources: tuple[str, ...] = ()
    scripts_enabled: bool = False
    script_runtimes: tuple[ScriptRuntimeConfig, ...] = ()

    def __post_init__(self):
        if (
            type(self.scripts_enabled) is not bool
            or type(self.script_runtimes) is not tuple
            or len(self.script_runtimes) > 32
            or any(type(r) is not ScriptRuntimeConfig for r in self.script_runtimes)
            or len({r.node_id for r in self.script_runtimes}) != len(self.script_runtimes)
        ):
            raise ValueError("invalid_compute_configuration")


@dataclass(frozen=True)
class ExecutionConfig:
    authority_directory: Path
    workspaces: tuple[WorkspaceExecutionConfig, ...] = ()
    lock_timeout: float = 1.0


def _configure_control(db, config):
    """Trusted constructor seam. No public fence or private-key setter."""
    from commonplace import sessions

    from .fence import _ControlLock, _Coordinator

    if type(config) is not ExecutionConfig:
        raise ValueError("invalid_compute_configuration")
    if len(config.workspaces) > 32 or len({w.workspace for w in config.workspaces}) != len(
        config.workspaces
    ):
        raise ValueError("invalid_compute_configuration")
    if hasattr(db, "_compute_coordinator"):
        raise ValueError("compute_already_configured")
    coordinator = _Coordinator(config.authority_directory, timeout=config.lock_timeout)
    original = db._lock
    try:
        # The existing central session writer table must exist before guarding it.
        sessions.ensure_schema(db)
        coordinator.register(str(Path(db.path).absolute()), "control")
        db._lock = _ControlLock(coordinator, original)
        with db._lock:
            coordinator.attach(db._conn, str(Path(db.path).absolute()), "control")
        db._compute_config = config
        db._compute_coordinator = coordinator
        db._compute_services = []
        # Registered before gateway workers: signal all services before joining.
        db.add_pre_close_callback(lambda: _shutdown(db))
    except BaseException:
        db._lock = original
        coordinator.close()
        raise


def _shutdown(db):
    services = tuple(db._compute_services)
    for service in services:
        service._stop.set()
    stopped = all([service.stop(timeout=5 / max(1, len(services))) for service in services])
    db._compute_shutdown_complete = stopped
    if not stopped:
        # Keep the coordinator available to late worker teardown. Python cannot
        # forcibly stop a thread or prove remote termination.
        raise RuntimeError("compute_shutdown_incomplete")
