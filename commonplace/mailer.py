"""Outgoing mail, configured only through the environment.

``from_env()`` returns an :class:`SMTPMailer` when ``COMMONPLACE_SMTP_HOST`` is
set and a :class:`DisabledMailer` otherwise. Both implement :class:`Mailer`, so
callers never branch on how mail is delivered. The SMTP password never appears
in ``repr``, logs or exception messages.
"""

from __future__ import annotations

import ipaddress
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid, parseaddr
from typing import Mapping, Optional, Protocol

NOT_CONFIGURED = "mail not configured: set COMMONPLACE_SMTP_HOST and COMMONPLACE_SMTP_FROM"
TIMEOUT_S = 30.0
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class MailError(Exception):
    """Mail could not be configured or sent. The message is safe to show."""


class MailNotConfigured(MailError):
    """No SMTP server is configured."""


class Mailer(Protocol):
    configured: bool

    def send(self, to: str, subject: str, text: str, html: Optional[str] = None) -> None: ...


class DisabledMailer:
    """The default: sends nothing and says so."""

    configured = False

    def send(self, to: str, subject: str, text: str, html: Optional[str] = None) -> None:
        raise MailNotConfigured(NOT_CONFIGURED)


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    sender: str
    user: str = ""
    password: str = field(default="", repr=False)
    security: str = "starttls"  # "starttls", "ssl" or "none"
    timeout: float = TIMEOUT_S


def _address(value: str, what: str) -> str:
    if not isinstance(value, str) or any(c in value for c in "\r\n\0"):
        raise MailError(f"invalid {what} address")
    _, address = parseaddr(value)
    # getaddresses also catches lists that older parseaddr builds cut to one.
    if "@" not in address or " " in address or len(getaddresses([value])) != 1:
        raise MailError(f"invalid {what} address")
    return value.strip()


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class SMTPMailer:
    configured = True

    def __init__(self, config: SMTPConfig):
        self.config = config

    def __repr__(self) -> str:
        c = self.config
        return f"SMTPMailer(host={c.host!r}, port={c.port}, security={c.security!r})"

    def _message(self, to: str, subject: str, text: str, html: Optional[str]) -> EmailMessage:
        if any(c in subject for c in "\r\n"):
            raise MailError("invalid subject")
        message = EmailMessage()
        message["From"] = self.config.sender
        message["To"] = _address(to, "recipient")
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = make_msgid(domain=parseaddr(self.config.sender)[1].split("@")[-1])
        message.set_content(text)
        if html is not None:
            message.add_alternative(html, subtype="html")
        return message

    def _scrub(self, detail: str) -> str:
        password = self.config.password
        return detail.replace(password, "***") if password else detail

    def send(self, to: str, subject: str, text: str, html: Optional[str] = None) -> None:
        c = self.config
        message = self._message(to, subject, text, html)
        context = ssl.create_default_context()
        sent = False
        try:
            if c.security == "ssl":
                client = smtplib.SMTP_SSL(c.host, c.port, timeout=c.timeout, context=context)
            else:
                client = smtplib.SMTP(c.host, c.port, timeout=c.timeout)
            with client:
                if c.security == "starttls":
                    client.starttls(context=context)
                if c.user:
                    client.login(c.user, c.password)
                client.send_message(message)
                sent = True
        except smtplib.SMTPResponseException as exc:
            if sent:  # Accepted; only QUIT failed.
                return
            error = exc.smtp_error
            if isinstance(error, bytes):
                error = error.decode("utf-8", "replace")
            detail = f"{exc.smtp_code} {error}"
            raise MailError(f"mail not sent: {self._scrub(detail)}") from None
        except (smtplib.SMTPException, OSError) as exc:
            if sent:
                return
            detail = f"{type(exc).__name__}: {exc}"
            raise MailError(f"mail not sent: {self._scrub(detail)}") from None


def _flag(env: Mapping[str, str], name: str) -> Optional[bool]:
    value = (env.get(name) or "").strip().lower()
    if not value:
        return None
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise MailError(f"{name} must be true or false")


def config_from_env(env: Mapping[str, str]) -> Optional[SMTPConfig]:
    """Read ``COMMONPLACE_SMTP_*``. ``None`` when no host is set."""
    host = (env.get("COMMONPLACE_SMTP_HOST") or "").strip()
    if not host:
        return None
    sender = (env.get("COMMONPLACE_SMTP_FROM") or "").strip()
    if not sender:
        raise MailError("COMMONPLACE_SMTP_FROM is not set")
    sender = _address(sender, "COMMONPLACE_SMTP_FROM")
    raw_port = (env.get("COMMONPLACE_SMTP_PORT") or "").strip()
    try:
        port = int(raw_port) if raw_port else None
    except ValueError:
        port = 0
    if port is not None and not 0 < port < 65536:
        raise MailError("COMMONPLACE_SMTP_PORT must be a port number")
    use_ssl = _flag(env, "COMMONPLACE_SMTP_SSL")
    starttls = _flag(env, "COMMONPLACE_SMTP_STARTTLS")
    if use_ssl is None:
        use_ssl = port == 465 and not starttls
    if use_ssl and starttls:
        raise MailError("set only one of COMMONPLACE_SMTP_SSL and COMMONPLACE_SMTP_STARTTLS")
    if use_ssl:
        security = "ssl"
    elif starttls is False:
        security = "none"
    else:
        security = "starttls"
    if port is None:
        port = {"ssl": 465, "starttls": 587, "none": 25}[security]
    user = env.get("COMMONPLACE_SMTP_USER") or ""
    password = env.get("COMMONPLACE_SMTP_PASSWORD") or ""
    if bool(user) != bool(password):
        raise MailError("set both COMMONPLACE_SMTP_USER and COMMONPLACE_SMTP_PASSWORD, or neither")
    if user and security == "none" and not _loopback(host):
        raise MailError("refusing to send SMTP credentials without TLS to a remote host")
    return SMTPConfig(host, port, sender, user, password, security)


def from_env(env: Optional[Mapping[str, str]] = None) -> Mailer:
    """An SMTP mailer when ``COMMONPLACE_SMTP_HOST`` is set, else a disabled one."""
    if env is None:
        env = os.environ
    config = config_from_env(env)
    return DisabledMailer() if config is None else SMTPMailer(config)
