"""Operator controls for gateway launchers and their network destinations."""

import ipaddress
import os
import socket
from urllib.parse import urlsplit

from commonplace.extensions import ToolError
from commonplace.webhooks import validate_url


class LauncherPolicyError(ValueError):
    pass


def require_enabled(kind: str) -> None:
    allowed = {
        item.strip() for item in os.environ.get("COMMONPLACE_HANDOFF_LAUNCHERS", "").split(",")
    }
    if kind in ("command", "webhook") and kind not in allowed:
        raise LauncherPolicyError(
            f"{kind} launcher requires operator opt-in via COMMONPLACE_HANDOFF_LAUNCHERS"
        )


def require_public_address(address: str) -> None:
    try:
        value = ipaddress.ip_address(address)
        public = value.is_global and not value.is_multicast
    except ValueError:
        public = False
    if not public:
        raise LauncherPolicyError("webhook launcher requires a public destination address")


def validate_webhook_url(url: str) -> None:
    try:
        validate_url(url)
    except ToolError as error:
        raise LauncherPolicyError(str(error).removeprefix("no: ")) from None
    target = urlsplit(url)
    try:
        address = ipaddress.ip_address(target.hostname)
    except ValueError:
        try:
            addresses = socket.getaddrinfo(
                target.hostname, target.port or 443, type=socket.SOCK_STREAM
            )
        except OSError:
            raise LauncherPolicyError(
                "webhook launcher destination could not be resolved"
            ) from None
        if not addresses:
            raise LauncherPolicyError(
                "webhook launcher destination could not be resolved"
            ) from None
        for entry in addresses:
            require_public_address(entry[4][0])
    else:
        require_public_address(str(address))
