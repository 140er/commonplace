"""Secret-free prerequisite states; display copy and actions live in the shared UI."""

import json

from commonplace.chat import settings
from commonplace.policy import Policy
from commonplace.projects import orchestration, seats
from commonplace.runner import registry


def snapshot(gateway, principal, board=None, *, main=None):
    db, ws = gateway.db, principal.workspace_id
    config = settings.front(db, ws, board)
    chosen = config.enabled and config.provider != "none" and bool(config.model)
    model_code = "needs_turn_on" if chosen and not config.applies else "needs_configuration"
    rows = {
        "model": {
            "state": "done" if chosen and config.applies else "needs_you",
            "code": model_code,
        },
        "key": {"state": "not_needed"},
        "machine": {"state": "not_needed"},
        "connection": {"state": "not_needed"},
        "trust": {"state": "not_needed"},
        "notifications": {"state": "not_needed"},
    }
    if not chosen:
        rows["key"] = {"state": "needs_you", "code": "choose_model_first"}
    elif config.key_ref is not None or not settings.is_local(config):
        allowed = settings.key_allowed(config)
        available = allowed and bool(settings.resolve_key(config))
        rows["key"] = {
            "state": "done" if available else "needs_you",
            "code": "key_missing" if allowed or config.key_ref is None else "key_not_allowed",
        }
    if board is None:
        return rows  # Personal chat runs on the gateway, without an agent machine.
    main = main if main is not None else seats.main(gateway, principal, board).public()
    rows["connection"] = {
        "state": "done" if main["connected"] and main["seen"] else "needs_you",
        "code": "connection_unused" if main["connected"] else "connection_missing",
    }
    machines = [
        m
        for m in registry.machines(db, ws)
        if m["enabled"]
        and not m["revoked_at"]
        and seats.active_connection(db, ws, m["machine_connection_id"]) is not None
    ]
    rows["machine"] = {
        "state": "done" if machines else "needs_you",
        "code": "runner_not_configured",
    }
    if machines or orchestration.enabled(db, ws):
        attested = [m for m in machines if m["binding_state"] == "owner_attested"]
        trusted = bool(attested)
        if orchestration.enabled(db, ws):
            seat = seats.ensure_main(db, ws, board)
            live = seats.active_connection(db, ws, seat.connection_id)
            trusted = bool(
                live
                and Policy.from_json(live["policy"]).trust == "trusted"
                and any(seat.connection_id in json.loads(m["requesters"]) for m in attested)
            )
        rows["trust"] = {
            "state": "done" if trusted else "needs_you",
            "code": "trust_missing" if attested else "machine_untrusted",
        }
    return rows
