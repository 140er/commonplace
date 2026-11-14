"""Local owner pin and scoped status checks; this never authorizes execution."""

import json

from . import host_status
from .bootstrap import parse_issuer_pin
from .install_files import STATE, digest
from .install_host import InstallError
from .install_receipts import canonical
from .receipt_cache import MODES

PIN = STATE + "/host-issuer-pin.json"


def load(install):
    try:
        expected = install.journal.get("host_issuer_pin")
        if expected is None and install.fs.info(PIN) is None:
            return None
        item = next(i for i in install.journal["items"] if i["path"] == PIN and i["kind"] == "file")
        raw = install.fs.read(PIN)
        value = parse_issuer_pin(json.loads(raw))
        if value is None or value != expected or digest(raw) != item["digest"]:
            raise ValueError
        return value
    except Exception:
        raise InstallError("owner issuer pin unavailable") from None


def context(install):
    from .installer import CONFIG
    from .runtime_install import Runtime
    from .setup_client import GRANT, Setup

    runtime = Runtime(
        install, json.loads(install.fs.read(CONFIG)), install.transport, install.runtime_host
    )
    current = runtime._receipt()
    value = json.loads(install.fs.read(GRANT))
    scope = value["signed"]["receipt"]["scope"]
    metadata = {
        "version",
        "approved_by",
        "approved_at",
        "attested_at",
        "nonce",
        "issued_at",
        "expires_at",
        "agent_ready",
    }
    if scope != {k: v for k, v in current.items() if k not in metadata}:
        raise ValueError
    Setup(install, runtime)._check_grant(
        {"id": value["signed"]["receipt"]["request_id"], "scope_digest": digest(canonical(scope))},
        scope,
    )
    return runtime, scope, value


def accept(install, envelope, nonce, *, request=None):
    try:
        pin = load(install)
        if pin is None:
            raise ValueError
        _, scope, _ = context(install)
        host_status.verify(
            envelope,
            {pin["issuer"]: pin["public_key"].encode("ascii")},
            scope,
            digest(canonical(scope)),
            nonce,
        )
        if request is not None:
            _scope(scope, request)
        return {"agent_ready": False}
    except Exception:
        raise InstallError("pinned host scope unavailable") from None


def _scope(scope, request):
    if not isinstance(request, dict) or set(request) != {"project", "topic", "runtime_mode"}:
        raise ValueError
    project = scope["projects"][request["project"]]
    mode = request["runtime_mode"]
    if (
        request["topic"] not in scope["topics"]
        or mode not in MODES
        or MODES.index(mode) > MODES.index(project["runtime_ceiling"])
        or MODES.index(mode) > MODES.index(scope["max_runtime_mode"])
    ):
        raise ValueError
