"""Compute management plus explicit trusted, default-off service configuration.

The host may pass ExecutionConfig to ControlDB at service startup. Configuration
is per workspace, never request input or environment discovery. Only already
provisioned workspace/custody files are accepted; no private key is initialized
or read by imports, management requests, or lifecycle installation.
"""

import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


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


def _install_execution(gateway):
    from commonplace.boards.store import BoardStore

    from .control import open_store
    from .executor import ExecutionService
    from .fence import FenceDenied
    from .protocol import identifier

    db = gateway.db
    config = getattr(db, "_compute_config", None)
    if config is None:
        return
    coordinator = db._compute_coordinator
    gateway._compute_fences = {}
    gateway._compute_services = {}
    gateway._compute_execution_reasons = {}
    for workspace in config.workspaces:
        identifier(workspace.workspace)
        if workspace.enabled is not True and workspace.discovery_enabled is not True:
            gateway._compute_execution_reasons[workspace.workspace] = "disabled"
            continue
        try:
            directory = Path(workspace.directory)
            # Resolve no caller paths, create no workspaces or custody/key files.
            if not directory.is_absolute() or directory != gateway.supervisor.workspace_dir(
                workspace.workspace
            ):
                raise FenceDenied("authority_workspace_mismatch")
            paths = {
                "control": str(Path(db.path).absolute()),
                "boards": str(directory / "boards.sqlite3"),
                "compute": str(directory / "compute.sqlite3"),
                "custody": str(directory / "compute-credentials.sqlite3"),
            }
            if not all(Path(path).is_file() for path in paths.values()):
                raise FenceDenied("authority_provisioning_required")
            if not workspace.provisioning_keys or not Path(workspace.wrapping_key_path).is_file():
                raise FenceDenied("authority_provisioning_required")
            with coordinator.locked():
                # Constructors preserve their existing schema and safe-open rules.
                for domain in ("boards", "compute", "custody"):
                    coordinator.register(paths[domain], domain)
                BoardStore(directory, create=False)
                store = open_store(gateway, workspace.workspace)
                with store._journal():
                    pass
                # Install custody guards without opening the wrapping key or
                # reading encrypted credential columns. Safe-open is centralized.
                from .credentials import _open_directory, _private_file

                dirfd = _open_directory(directory, private=True)
                fd = None
                try:
                    fd = os.open(
                        "compute-credentials.sqlite3", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dirfd
                    )
                    _private_file(fd)
                    conn = sqlite3.connect(
                        f"/proc/self/fd/{dirfd}/compute-credentials.sqlite3", isolation_level=None
                    )
                    try:
                        coordinator.attach(conn, paths["custody"], "custody")
                    finally:
                        conn.close()
                finally:
                    if fd is not None:
                        os.close(fd)
                    os.close(dirfd)
                fence = coordinator.seal(workspace.workspace, paths.values())
                gateway._compute_fences[workspace.workspace] = fence
                store._authority_fence = fence
            if workspace.enabled is not True:
                gateway._compute_execution_reasons[workspace.workspace] = "disabled"
                continue
            service = ExecutionService(
                store,
                enabled=True,
                fence=fence,
                wrapping_key_path=workspace.wrapping_key_path,
                provisioning_keys=workspace.provisioning_keys,
                admission_mode=workspace.admission_mode,
            )
            db._compute_services.append(service)
            gateway._compute_services[workspace.workspace] = service
            service.start()
            gateway._compute_execution_reasons[workspace.workspace] = service.last_reason
        except Exception:
            # A partially guarded workspace stays guarded. Never remove guards
            # to make management or an old deployment silently bypass the fence.
            gateway._compute_execution_reasons[workspace.workspace] = "execution_unavailable"


def install(gateway):
    """Register management and the explicitly configured service lifecycle."""
    if getattr(gateway, "compute_installed", False):
        return
    from . import routes

    routes.install(gateway)
    _install_execution(gateway)
    from .discovery_service import install_lifecycle

    install_lifecycle(gateway)
    gateway.compute_installed = True
