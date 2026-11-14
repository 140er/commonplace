"""Install management: validation, sealing, the hub index, the test and the audit rows.

Both the owner routes and the command line call these functions, so the rules are written once.
Every refusal is a :class:`Refused` with a fixed code and sentence. Audit rows carry ids, the
route and a fixed code, never a credential, a label or a platform's text.
"""

import re
from pathlib import Path

from commonplace.chat_bridge import adapter, index, sealing, store

AUDIT = "bridge.install"  # one action name; the tool column says what happened
TEAM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
LABEL_MAX, SECRET_MAX, SECRETS_MAX = 80, 4096, 16_384

REFUSALS = {
    "invalid": (400, "Invalid request"),
    "unsupported_platform": (400, "That platform is not available on this hub."),
    "team_taken": (409, "That team is already installed on this hub."),
    "not_tested": (409, "Run a successful test before turning this on."),
    "sealing_unavailable": (503, "Secret storage is not set up on this hub."),
    "already_linked": (409, "You already have a link for this install. Remove it first."),
    "unknown_board": (400, "That project does not exist or you cannot read it."),
    "not_found": (404, "Not found"),
}


class Refused(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code
        self.status, self.text = REFUSALS[code]


def _data_root(gateway):
    return Path(gateway.db.path).resolve().parent


def audit(
    gateway,
    principal_or_none,
    workspace_id,
    action,
    install_id,
    decision="ok",
    reason="",
    link_id=None,
):
    user = getattr(principal_or_none, "user_id", None)
    gateway.db.audit(
        action=AUDIT,
        decision=decision,
        workspace_id=workspace_id,
        user_id=user,
        tool=action,
        reason=reason,
        target_ids=[i for i in (install_id, link_id) if i],
    )


def _text(value, name, high):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= high:
        raise Refused("invalid")
    value = value.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise Refused("invalid")
    return value


def _cap(value):
    if type(value) is not int or not 1 <= value <= store.DAILY_CAP_MAX:
        raise Refused("invalid")
    return value


def _secrets(platform_adapter, value) -> dict:
    wanted = set(platform_adapter.secret_fields)
    if not isinstance(value, dict) or set(value) != wanted:
        raise Refused("invalid")
    total = 0
    for item in value.values():
        if not isinstance(item, str) or not item or len(item) > SECRET_MAX:
            raise Refused("invalid")
        if any(ord(c) < 32 or ord(c) == 127 for c in item):
            raise Refused("invalid")
        total += len(item)
    if total > SECRETS_MAX:
        raise Refused("invalid")
    return dict(value)


def _key(gateway, workspace_id) -> bytes:
    try:
        return sealing.key_for(_data_root(gateway), workspace_id)
    except sealing.Unavailable:
        raise Refused("sealing_unavailable") from None


def create(gateway, principal, body: dict) -> dict:
    ws = principal.workspace_id
    platform_adapter = adapter.get(body.get("platform"))
    if platform_adapter is None:
        audit(gateway, principal, ws, "create", None, "refused", "unsupported_platform")
        raise Refused("unsupported_platform")
    try:
        team = _text(body.get("ext_team"), "ext_team", 128)
        if not TEAM.fullmatch(team):
            raise Refused("invalid")
        label = _text(body.get("label"), "label", LABEL_MAX)
        cap = _cap(body["daily_cap"]) if "daily_cap" in body else store.DAILY_CAP_DEFAULT
        secrets = _secrets(platform_adapter, body.get("secrets"))
        key = _key(gateway, ws)
    except Refused as refused:
        audit(gateway, principal, ws, "create", None, "refused", refused.code)
        raise
    install_id = store.new_install_id()
    sealed = sealing.seal(key, install_id, secrets)
    bridge = store.open_bridge(gateway, ws)
    try:
        index.claim(gateway.db, install_id, ws, platform_adapter.platform, team)
    except index.TeamTaken:
        audit(gateway, principal, ws, "create", None, "refused", "team_taken")
        raise Refused("team_taken") from None
    try:
        view = bridge.create(
            install_id, platform_adapter.platform, team, label, sealed, cap, principal.user_id
        )
    except BaseException:
        index.release(gateway.db, install_id)
        raise
    audit(gateway, principal, ws, "create", install_id)
    return view


def update(gateway, principal, install_id, body: dict) -> dict:
    ws = principal.workspace_id
    bridge = store.open_bridge(gateway, ws)
    current = bridge.get(install_id)  # store.Missing -> 404
    try:
        label = _text(body["label"], "label", LABEL_MAX) if "label" in body else None
        cap = _cap(body["daily_cap"]) if "daily_cap" in body else None
        for flag in ("enabled", "consequential_taps"):
            if flag in body and type(body[flag]) is not bool:
                raise Refused("invalid")
        sealed = None
        if "secrets" in body:
            platform_adapter = adapter.get(current["platform"])
            if platform_adapter is None:
                raise Refused("unsupported_platform")
            secrets = _secrets(platform_adapter, body["secrets"])
            sealed = sealing.seal(_key(gateway, ws), install_id, secrets)
    except Refused as refused:
        audit(gateway, principal, ws, "update", install_id, "refused", refused.code)
        raise
    taps = body.get("consequential_taps")
    view = bridge.update(install_id, label=label, daily_cap=cap, sealed=sealed, taps=taps)
    if sealed is not None:
        audit(gateway, principal, ws, "rotate", install_id)
    if label is not None or cap is not None:
        audit(gateway, principal, ws, "update", install_id)
    if taps is not None:
        audit(gateway, principal, ws, "taps_on" if taps else "taps_off", install_id)
    if "enabled" in body:
        try:
            view = bridge.set_enabled(install_id, body["enabled"])
        except store.NotTested:
            audit(gateway, principal, ws, "enable", install_id, "refused", "not_tested")
            raise Refused("not_tested") from None
        audit(gateway, principal, ws, "enable" if body["enabled"] else "disable", install_id)
    return view


def delete(gateway, principal, install_id) -> None:
    ws = principal.workspace_id
    store.open_bridge(gateway, ws).delete(install_id)
    index.release(gateway.db, install_id)
    audit(gateway, principal, ws, "delete", install_id)


def test(gateway, principal, install_id) -> dict:
    """Run the adapter's credential test; a pass records ``tested_at``."""
    ws = principal.workspace_id
    bridge = store.open_bridge(gateway, ws)
    current = bridge.get(install_id)
    platform_adapter = adapter.get(current["platform"])
    if platform_adapter is None:
        audit(gateway, principal, ws, "test", install_id, "refused", "unsupported_platform")
        raise Refused("unsupported_platform")
    try:
        secrets = sealing.open_blob(_key(gateway, ws), install_id, bridge.secrets(install_id))
    except sealing.Unavailable:
        audit(gateway, principal, ws, "test", install_id, "refused", "sealing_unavailable")
        raise Refused("sealing_unavailable") from None
    ref = adapter.InstallRef(install_id, current["platform"], current["ext_team"], secrets)
    result = adapter.run_test(platform_adapter, ref)
    if result.ok:
        bridge.mark_tested(install_id)
    audit(gateway, principal, ws, "test", install_id, "ok" if result.ok else "refused", result.code)
    return {"ok": result.ok, "code": result.code, "text": adapter.TEST_TEXT[result.code]}


DISCLOSURES = ("titles", "full")


def put_channel(gateway, principal, install_id, ext_channel, body: dict) -> dict:
    """Map a channel to one project (the owner's explicit act; ``titles`` unless the owner picks
    ``full``). A channel's replies are read by everyone in it, so the owner must be able to read
    the project too."""
    from commonplace.boards.service import BoardService
    from commonplace.extensions import ToolContext

    ws = principal.workspace_id
    bridge = store.open_bridge(gateway, ws)
    bridge.get(install_id)  # store.Missing -> 404
    try:
        if not TEAM.fullmatch(ext_channel):
            raise Refused("invalid")
        board = body.get("board")
        disclosure = body.get("disclosure", "titles")
        enabled = body.get("enabled", True)
        if not isinstance(board, str) or disclosure not in DISCLOSURES or type(enabled) is not bool:
            raise Refused("invalid")
        boards = BoardService(ToolContext(principal=principal, gateway=gateway)).board_list()
        if board not in {b["slug"] for b in boards}:
            raise Refused("unknown_board")
    except Refused as refused:
        audit(gateway, principal, ws, "map_channel", install_id, "refused", refused.code)
        raise
    view = bridge.put_channel(
        install_id, ext_channel, board, disclosure, enabled, principal.user_id
    )
    audit(gateway, principal, ws, "map_channel", install_id)
    return view


def delete_channel(gateway, principal, install_id, ext_channel) -> None:
    ws = principal.workspace_id
    if not store.open_bridge(gateway, ws).delete_channel(install_id, ext_channel):
        raise store.Missing()
    audit(gateway, principal, ws, "unmap_channel", install_id)


def install_ref(gateway, workspace_id, install_id):
    """The install with its credentials unsealed in memory, for the adapter; None when the
    install is gone or no sealing key can be served. Never logged, never returned by a route."""
    bridge = store.open_bridge(gateway, workspace_id)
    try:
        current = bridge.get(install_id)
        secrets = sealing.open_blob(
            _key(gateway, workspace_id), install_id, bridge.secrets(install_id)
        )
    except (store.Missing, sealing.Unavailable, Refused):
        return None
    return adapter.InstallRef(install_id, current["platform"], current["ext_team"], secrets)


def kill_switch(db, supervisor, workspace_ids, actor_note="cli") -> list:
    """Switch every install of the given workspaces off (command line). Returns the ids."""
    done = []
    for workspace_id in workspace_ids:
        bridge = store.BridgeStore(supervisor, workspace_id)
        try:
            ids = bridge.disable_all()
        finally:
            bridge.close()
        for install_id in ids:
            db.audit(
                action=AUDIT,
                decision="ok",
                workspace_id=workspace_id,
                tool="disable",
                reason=actor_note,
                target_ids=[install_id],
            )
        done.extend(ids)
    return done
