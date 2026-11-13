"""Identity: link codes, links, and the per-event re-check that turns a platform id into a person.

A platform identity is nobody until a signed-in member binds it. The secret travels one way, from
the member's web session to their platform account: the member asks for a code here, then sends
it to the app in a direct message (``redeem``, called by intake). The code is shown once in the
issue answer and exists afterwards only as a salted SHA-256 hash; no answer, log line or audit row
repeats it. It lives 10 minutes, works once, and a sender who presents wrong codes is locked out
after five. A code sent in a channel is burned. Audit rows carry ids and fixed codes, never a code,
a platform id or a message.
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from commonplace import sessions
from commonplace.chat_bridge import service, store
from commonplace.control_db import ControlError

log = logging.getLogger(__name__)

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # no 0/1/I/L/O
CODE_LENGTH = 12
CODE_TTL = timedelta(minutes=10)
LOCK_SECONDS = 15 * 60
_MARKUP = " \t\r\n`'\"*_~<>"

# One fixed sentence per outcome, so nothing about why a code failed (or whose it was) is said.
REDEEM_TEXT = {
    "linked": "Your account is linked. You can message me here now.",
    "invalid": "That code did not work. Ask for a new one in the web app and send it here in a "
    "direct message.",
    "locked": "Too many wrong codes. Try again later.",
    "channel": "That code was sent in a channel, so it no longer works. Ask for a new one in the "
    "web app and send it to me in a direct message.",
    "already_linked": "A link already exists for this platform account or for your Commonplace "
    "account. Remove it in the web app first.",
}


@dataclass(frozen=True)
class Redeemed:
    """The outcome of presenting a code: ``linked``, ``invalid``, ``locked``, ``channel``,
    ``already_linked``, ``off`` (no such or disabled install: nothing is said) or ``not_a_code``
    (the text is not shaped like a code: nothing was counted)."""

    code: str
    link_id: str = ""

    @property
    def text(self) -> str:
        return REDEEM_TEXT.get(self.code, "")


@dataclass(frozen=True)
class Resolution:
    """Who a platform id is right now. ``principal`` is set only when the install is on, the link
    is live and the person is still a member; otherwise ``reason`` says which check failed."""

    principal: object = None
    link: dict = None
    reason: str = "unlinked"  # ok | off | unlinked | removed


def normalize(text):
    """The code inside ``text`` if the whole text is one (spacing, dashes and markup around it are
    ignored), else None."""
    if not isinstance(text, str) or len(text) > 64:
        return None
    flat = text.strip(_MARKUP).replace("-", "").replace(" ", "").upper()
    if len(flat) == CODE_LENGTH and all(c in ALPHABET for c in flat):
        return flat
    return None


def digest(install_id, code) -> str:
    return hashlib.sha256(
        f"commonplace-bridge-link-code\0{install_id}\0{code}".encode()
    ).hexdigest()


def _shown(code) -> str:
    return "-".join(code[i : i + 4] for i in range(0, CODE_LENGTH, 4))


def _person(gateway, workspace_id, user_id):
    """The member's principal, or None for a removed, disabled or deleted person."""
    try:
        return sessions.person_principal(gateway.db, user_id, workspace_id)
    except ControlError:
        return None


# Person: ask for a code, see and end their own links


def issue(gateway, principal, install_id, now=None) -> dict:
    """A fresh code for the signed-in person and an enabled install (anything else is
    :class:`store.Missing`). Their earlier unused codes for it end. The only place the code is
    ever returned."""
    ws = principal.workspace_id
    bridge = store.open_bridge(gateway, ws)
    if not bridge.get(install_id)["enabled"]:
        raise store.Missing()
    if any(link["install_id"] == install_id for link in bridge.links(user_id=principal.user_id)):
        service.audit(gateway, principal, ws, "link_code", install_id, "refused", "already_linked")
        raise service.Refused("already_linked")
    now = now or datetime.now(timezone.utc)
    code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
    expires = store.stamp(now + CODE_TTL)
    bridge.issue_code(install_id, principal.user_id, digest(install_id, code), expires, now)
    service.audit(gateway, principal, ws, "link_code", install_id)
    return {
        "install_id": install_id,
        "code": _shown(code),
        "expires_at": expires,
        "expires_in": int(CODE_TTL.total_seconds()),
    }


def _link_view(link, installs) -> dict:
    install = installs.get(link["install_id"], {})
    return {
        "id": link["id"],
        "install_id": link["install_id"],
        "platform": install.get("platform"),
        "label": install.get("label"),
        "ext_user": link["ext_user"],
        "confirm_ok": bool(link["confirm_ok"]),
        "created_at": link["created_at"],
    }


def person_overview(gateway, principal) -> dict:
    """The signed-in person's own links, and the enabled installs they could link to."""
    bridge = store.open_bridge(gateway, principal.workspace_id)
    installs = {i["id"]: i for i in bridge.installs()}
    return {
        "links": [_link_view(link, installs) for link in bridge.links(user_id=principal.user_id)],
        "available": [
            {"id": i["id"], "platform": i["platform"], "label": i["label"]}
            for i in installs.values()
            if i["enabled"]
        ],
    }


def set_confirm(gateway, principal, link_id, value) -> dict:
    """The person's own opt-in to confirm actions by tapping in the platform (off by default).
    Another person's link, a missing or an ended one is :class:`store.Missing`; turning it off
    also drops the open chips of that platform id."""
    if type(value) is not bool:
        raise service.Refused("invalid")
    ws = principal.workspace_id
    bridge = store.open_bridge(gateway, ws)
    link = bridge.set_confirm_ok(link_id, principal.user_id, value)
    if link is None:
        raise store.Missing()
    service.audit(
        gateway, principal, ws, "confirm_on" if value else "confirm_off", link["install_id"],
        link_id=link_id,
    )  # fmt: skip
    installs = {i["id"]: i for i in bridge.installs()}
    return _link_view(link, installs)


def unlink(gateway, principal, link_id, install_id=None) -> None:
    """End a link at once. The person ends their own (another person's link is
    :class:`store.Missing`, as is a missing one); the owner route passes the install and ends any
    link of it."""
    ws = principal.workspace_id
    bridge = store.open_bridge(gateway, ws)
    link = bridge.link(link_id)
    owner = install_id is not None
    if link is None or (owner and link["install_id"] != install_id):
        raise store.Missing()
    if not owner and link["user_id"] != principal.user_id:
        raise store.Missing()
    if bridge.revoke_link(link_id) is None:
        raise store.Missing()
    service.audit(
        gateway, principal, ws, "unlink_owner" if owner else "unlink", link["install_id"],
        link_id=link_id,
    )  # fmt: skip


# Owner: see who is linked (never what they said), end any link


def owner_links(gateway, principal, install_id) -> list:
    bridge = store.open_bridge(gateway, principal.workspace_id)
    bridge.get(install_id)  # store.Missing -> 404
    out = []
    for link in bridge.links(install_id=install_id):
        try:
            email = gateway.db.user(link["user_id"])["email"]
        except ControlError:
            email = None
        out.append(
            {
                "id": link["id"],
                "ext_user": link["ext_user"],
                "user_id": link["user_id"],
                "email": email,
                "member": _person(gateway, principal.workspace_id, link["user_id"]) is not None,
                "created_at": link["created_at"],
            }
        )
    return out


# Intake: present a code, resolve a platform id


def redeem(gateway, workspace_id, install_id, ext_user, kind, text, now=None) -> Redeemed:
    """A platform user sent ``text`` to the app (``kind`` is ``dm``, ``mention`` or ``other``).
    Only a direct message can link; the same text in a channel burns the code instead."""
    code = normalize(text)
    if code is None:
        return Redeemed("not_a_code")
    bridge = store.open_bridge(gateway, workspace_id)
    if not bridge.exists():
        return Redeemed("off")
    try:
        enabled = bridge.get(install_id)["enabled"]
    except store.Missing:
        return Redeemed("off")
    if not enabled:
        return Redeemed("off")
    if bridge.locked(install_id, ext_user, now):
        return Redeemed("locked")
    hashed = digest(install_id, code)
    row = bridge.code(install_id, hashed)
    stamped = store.stamp(now)
    live = row is not None and row["used_at"] is None and row["expires_at"] > stamped
    if kind != "dm":
        if live and bridge.burn_code(install_id, hashed, now):
            service.audit(
                gateway, None, workspace_id, "code_burned", install_id, "refused", "channel"
            )
        elif not live:
            _wrong(gateway, bridge, workspace_id, install_id, ext_user, now)
        return Redeemed("channel")
    person = _person(gateway, workspace_id, row["user_id"]) if live else None
    if person is None or row["tries"] >= store.CODE_TRIES:
        return Redeemed(
            "locked"
            if _wrong(gateway, bridge, workspace_id, install_id, ext_user, now)
            else "invalid"
        )
    held = bridge.link_rows(install_id, ext_user, row["user_id"])
    stale = [
        r["id"] for r in held if r["revoked_at"] or not _person(gateway, workspace_id, r["user_id"])
    ]
    if len(stale) != len(held):
        bridge.bump_code(install_id, hashed)
        return Redeemed("already_linked")
    link_id = store.new_install_id()
    try:
        bridge.create_link(link_id, install_id, ext_user, row["user_id"], hashed, stale, now)
    except store.CodeGone:
        return Redeemed("invalid")
    gateway.db.audit(
        action=service.AUDIT,
        decision="ok",
        workspace_id=workspace_id,
        user_id=row["user_id"],
        tool="link",
        target_ids=[install_id, link_id],
    )
    return Redeemed("linked", link_id)


def _wrong(gateway, bridge, workspace_id, install_id, ext_user, now) -> bool:
    locked = bridge.wrong_try(install_id, ext_user, store.CODE_TRIES, LOCK_SECONDS, now)
    if locked:
        service.audit(gateway, None, workspace_id, "code_locked", install_id, "refused", "tries")
    return locked


def resolve(gateway, workspace_id, install_id, ext_user) -> Resolution:
    """Re-checked on every event and tap: the install is on, the platform id has a live link and
    the linked person is still a member of the workspace. A link whose person left is ended here,
    so adding them back later does not bring it back."""
    bridge = store.open_bridge(gateway, workspace_id)
    if not bridge.exists():
        return Resolution(reason="off")
    try:
        if not bridge.get(install_id)["enabled"]:
            return Resolution(reason="off")
    except store.Missing:
        return Resolution(reason="off")
    link = bridge.link_of(install_id, ext_user)
    if link is None:
        return Resolution()
    principal = _person(gateway, workspace_id, link["user_id"])
    if principal is None:
        if bridge.revoke_link(link["id"]):
            service.audit(
                gateway,
                None,
                workspace_id,
                "link_end",
                install_id,
                "ok",
                "removed",
                link_id=link["id"],
            )
        return Resolution(link=link, reason="removed")
    return Resolution(principal, link, "ok")
