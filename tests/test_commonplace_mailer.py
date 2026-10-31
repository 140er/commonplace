"""SMTP mailer and ``commonplace login-link --send`` against an in-process fake server."""

import base64
import logging
import socketserver
import sys
import threading
from contextlib import closing
from email import message_from_bytes, policy
from urllib.parse import parse_qs, urlsplit

import pytest

from commonplace import cli, mailer, sessions
from commonplace.control_db import ControlDB

PASSWORD = "s3cret-Pa55word"
SMTP_ENV = (
    "COMMONPLACE_SMTP_HOST",
    "COMMONPLACE_SMTP_PORT",
    "COMMONPLACE_SMTP_USER",
    "COMMONPLACE_SMTP_PASSWORD",
    "COMMONPLACE_SMTP_FROM",
    "COMMONPLACE_SMTP_STARTTLS",
    "COMMONPLACE_SMTP_SSL",
)


class _Handler(socketserver.StreamRequestHandler):
    def _reply(self, line):
        self.wfile.write(line.encode() + b"\r\n")

    def handle(self):
        server = self.server
        self._reply("220 fake.example ESMTP")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            command = line.decode().rstrip("\r\n")
            verb = command.split(" ", 1)[0].upper()
            if verb == "EHLO":
                self._reply("250-fake.example")
                self._reply("250 AUTH PLAIN")
            elif verb == "AUTH":
                _, mechanism, *rest = command.split(" ")
                assert mechanism.upper() == "PLAIN"
                server.auth.append(base64.b64decode(rest[0]).decode())
                self._reply(server.auth_reply)
            elif verb == "MAIL":
                server.mail_from.append(command)
                self._reply("250 ok")
            elif verb == "RCPT":
                server.rcpt_to.append(command)
                self._reply("250 ok")
            elif verb == "DATA":
                self._reply("354 go ahead")
                data = b""
                while True:
                    chunk = self.rfile.readline()
                    if chunk == b".\r\n":
                        break
                    data += chunk[1:] if chunk.startswith(b"..") else chunk
                server.messages.append(message_from_bytes(data, policy=policy.default))
                self._reply("250 queued")
            elif verb == "QUIT":
                self._reply("221 bye")
                return
            else:
                self._reply("250 ok")


class _FakeSMTP(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.auth, self.mail_from, self.rcpt_to, self.messages = [], [], [], []
        self.auth_reply = "235 ok"


@pytest.fixture
def smtp_server():
    server = _FakeSMTP()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def smtp_env(monkeypatch, smtp_server):
    for name in SMTP_ENV:
        monkeypatch.delenv(name, raising=False)
    values = {
        "COMMONPLACE_SMTP_HOST": "127.0.0.1",
        "COMMONPLACE_SMTP_PORT": str(smtp_server.server_address[1]),
        "COMMONPLACE_SMTP_USER": "mailer@example.com",
        "COMMONPLACE_SMTP_PASSWORD": PASSWORD,
        "COMMONPLACE_SMTP_FROM": "Commonplace <noreply@example.com>",
        "COMMONPLACE_SMTP_STARTTLS": "0",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


@pytest.fixture
def no_smtp_env(monkeypatch):
    for name in SMTP_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def initialized(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "commonplace.urls", None)
    monkeypatch.delenv("COMMONPLACE_BASE_URL", raising=False)
    monkeypatch.setenv("COMMONPLACE_APP_URL", "https://app.example.com/")
    data = tmp_path / "data"
    assert cli.main(["--data", str(data), "init", "--admin-email", "owner@example.com"]) == 0
    capsys.readouterr()
    return data


def _unused_links(data):
    with closing(ControlDB(str(data / "control.sqlite3"))) as db:
        sessions.ensure_schema(db)
        return db._conn.execute(
            "SELECT count(*) FROM login_links WHERE used_at IS NULL"
        ).fetchone()[0]


# -- configuration -------------------------------------------------------------


def test_unset_config_is_a_disabled_mailer():
    sender = mailer.from_env({})
    assert isinstance(sender, mailer.DisabledMailer) and not sender.configured
    with pytest.raises(mailer.MailNotConfigured, match="mail not configured"):
        sender.send("someone@example.com", "subject", "text")


@pytest.mark.parametrize(
    ("env", "security", "port"),
    [
        ({}, "starttls", 587),
        ({"COMMONPLACE_SMTP_PORT": "2525"}, "starttls", 2525),
        ({"COMMONPLACE_SMTP_PORT": "465"}, "ssl", 465),
        ({"COMMONPLACE_SMTP_SSL": "true"}, "ssl", 465),
        ({"COMMONPLACE_SMTP_SSL": "1", "COMMONPLACE_SMTP_PORT": "2465"}, "ssl", 2465),
        ({"COMMONPLACE_SMTP_STARTTLS": "yes", "COMMONPLACE_SMTP_PORT": "465"}, "starttls", 465),
        ({"COMMONPLACE_SMTP_STARTTLS": "off"}, "none", 25),
        ({"COMMONPLACE_SMTP_SSL": "0", "COMMONPLACE_SMTP_PORT": "465"}, "starttls", 465),
    ],
)
def test_config_security_and_port(env, security, port):
    base = {"COMMONPLACE_SMTP_HOST": "smtp.example.com", "COMMONPLACE_SMTP_FROM": "a@example.com"}
    config = mailer.config_from_env({**base, **env})
    assert (config.security, config.port) == (security, port)


@pytest.mark.parametrize(
    ("env", "error"),
    [
        ({"COMMONPLACE_SMTP_FROM": ""}, "COMMONPLACE_SMTP_FROM is not set"),
        ({"COMMONPLACE_SMTP_FROM": "not an address"}, "invalid COMMONPLACE_SMTP_FROM"),
        ({"COMMONPLACE_SMTP_FROM": "a@example.com\r\nBcc: x@example.com"}, "invalid"),
        ({"COMMONPLACE_SMTP_PORT": "smtp"}, "must be a port number"),
        ({"COMMONPLACE_SMTP_PORT": "70000"}, "must be a port number"),
        ({"COMMONPLACE_SMTP_STARTTLS": "maybe"}, "must be true or false"),
        ({"COMMONPLACE_SMTP_SSL": "1", "COMMONPLACE_SMTP_STARTTLS": "1"}, "only one of"),
        ({"COMMONPLACE_SMTP_USER": "u"}, "both COMMONPLACE_SMTP_USER"),
        ({"COMMONPLACE_SMTP_PASSWORD": PASSWORD}, "both COMMONPLACE_SMTP_USER"),
        (
            {
                "COMMONPLACE_SMTP_USER": "u",
                "COMMONPLACE_SMTP_PASSWORD": PASSWORD,
                "COMMONPLACE_SMTP_STARTTLS": "0",
            },
            "without TLS",
        ),
    ],
)
def test_config_rejects_bad_values(env, error):
    base = {"COMMONPLACE_SMTP_HOST": "smtp.example.com", "COMMONPLACE_SMTP_FROM": "a@example.com"}
    with pytest.raises(mailer.MailError, match=error) as caught:
        mailer.config_from_env({**base, **env})
    assert PASSWORD not in str(caught.value)


def test_password_not_in_repr():
    env = {
        "COMMONPLACE_SMTP_HOST": "smtp.example.com",
        "COMMONPLACE_SMTP_FROM": "a@example.com",
        "COMMONPLACE_SMTP_USER": "u",
        "COMMONPLACE_SMTP_PASSWORD": PASSWORD,
    }
    sender = mailer.from_env(env)
    assert PASSWORD not in repr(sender)
    assert PASSWORD not in repr(sender.config)
    assert sender.config.password == PASSWORD


# -- sending -------------------------------------------------------------------


def test_send_over_the_wire(smtp_server, smtp_env):
    sender = mailer.from_env()
    sender.send("someone@example.com", "Hello", "plain body", html="<p>html body</p>")
    assert smtp_server.auth == [f"\0mailer@example.com\0{PASSWORD}"]
    assert smtp_server.mail_from == ["mail FROM:<noreply@example.com>"]
    assert smtp_server.rcpt_to == ["rcpt TO:<someone@example.com>"]
    (message,) = smtp_server.messages
    assert message["From"] == "Commonplace <noreply@example.com>"
    assert message["To"] == "someone@example.com"
    assert message["Subject"] == "Hello"
    assert message.get_body("plain").get_content().strip() == "plain body"
    assert message.get_body("html").get_content().strip() == "<p>html body</p>"


def test_send_rejects_header_injection(smtp_env):
    sender = mailer.from_env()
    with pytest.raises(mailer.MailError, match="invalid recipient"):
        sender.send("a@example.com\r\nBcc: b@example.com", "s", "t")
    with pytest.raises(mailer.MailError, match="invalid subject"):
        sender.send("a@example.com", "s\nBcc: b@example.com", "t")


class _RecordingSMTP:
    instances = []

    def __init__(self, host, port, timeout=None, context=None):
        self.calls = [("connect", host, port, context is not None)]
        _RecordingSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append(("quit",))

    def starttls(self, context=None):
        assert context is not None and context.verify_mode.name == "CERT_REQUIRED"
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        self.calls.append(("send", message["To"]))


@pytest.mark.parametrize(
    ("extra", "factory", "expected"),
    [
        (
            {},
            "SMTP",
            [
                ("connect", "smtp.example.com", 587, False),
                ("starttls",),
                ("login", "u", PASSWORD),
                ("send", "to@example.com"),
                ("quit",),
            ],
        ),
        (
            {"COMMONPLACE_SMTP_SSL": "1"},
            "SMTP_SSL",
            [
                ("connect", "smtp.example.com", 465, True),
                ("login", "u", PASSWORD),
                ("send", "to@example.com"),
                ("quit",),
            ],
        ),
    ],
)
def test_starttls_and_ssl_paths(monkeypatch, extra, factory, expected):
    _RecordingSMTP.instances = []
    monkeypatch.setattr(mailer.smtplib, factory, _RecordingSMTP)
    env = {
        "COMMONPLACE_SMTP_HOST": "smtp.example.com",
        "COMMONPLACE_SMTP_FROM": "a@example.com",
        "COMMONPLACE_SMTP_USER": "u",
        "COMMONPLACE_SMTP_PASSWORD": PASSWORD,
        **extra,
    }
    mailer.from_env(env).send("to@example.com", "s", "t")
    (client,) = _RecordingSMTP.instances
    assert client.calls == expected


def test_auth_failure_never_shows_password(smtp_server, smtp_env):
    smtp_server.auth_reply = f"535 bad credentials {PASSWORD}"
    with pytest.raises(mailer.MailError) as caught:
        mailer.from_env().send("someone@example.com", "s", "t")
    assert "535" in str(caught.value)
    assert PASSWORD not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_connection_failure_is_a_mail_error(smtp_env, monkeypatch):
    with socketserver.TCPServer(("127.0.0.1", 0), socketserver.BaseRequestHandler) as closed:
        port = closed.server_address[1]
    monkeypatch.setenv("COMMONPLACE_SMTP_PORT", str(port))
    with pytest.raises(mailer.MailError, match="mail not sent"):
        mailer.from_env().send("someone@example.com", "s", "t")


# -- login-link --send ---------------------------------------------------------


def test_login_link_send_emails_and_never_prints(
    initialized, smtp_server, smtp_env, capsys, caplog
):
    caplog.set_level(logging.DEBUG)
    argv = ["--data", str(initialized), "login-link", "owner@example.com", "--send"]
    assert cli.main(argv + ["--minutes", "30"]) == 0
    output = capsys.readouterr()
    assert output.out == "login link sent to owner@example.com\n"
    assert not output.err
    (message,) = smtp_server.messages
    assert message["To"] == "owner@example.com"
    assert message["From"] == "Commonplace <noreply@example.com>"
    assert message["Subject"] == "Your Commonplace sign-in link"
    body = message.get_content()
    (url,) = [line for line in body.splitlines() if line.startswith("https://")]
    assert url.startswith("https://app.example.com/app/login?t=")
    assert "expires in 30 minutes" in body
    token = parse_qs(urlsplit(url).query)["t"][0]
    for text in (output.out, output.err, caplog.text):
        assert token not in text and PASSWORD not in text
    with closing(ControlDB(str(initialized / "control.sqlite3"))) as db:
        assert sessions.redeem_login_link(db, token)
        assert sessions.redeem_login_link(db, token) is None


def test_login_link_without_send_still_prints(initialized, smtp_server, smtp_env, capsys):
    assert cli.main(["--data", str(initialized), "login-link", "owner@example.com"]) == 0
    assert capsys.readouterr().out.startswith("https://app.example.com/app/login?t=")
    assert not smtp_server.messages


def test_login_link_send_unconfigured_mints_nothing(initialized, no_smtp_env, capsys):
    argv = ["--data", str(initialized), "login-link", "owner@example.com", "--send"]
    assert cli.main(argv) == 2
    output = capsys.readouterr()
    assert not output.out
    assert "mail not configured" in output.err and "/app/login" not in output.err
    assert _unused_links(initialized) == 0


def test_login_link_send_failure_discards_link(initialized, smtp_server, smtp_env, capsys, caplog):
    caplog.set_level(logging.DEBUG)
    smtp_server.auth_reply = f"535 rejected {PASSWORD}"
    argv = ["--data", str(initialized), "login-link", "owner@example.com", "--send"]
    assert cli.main(argv) == 2
    output = capsys.readouterr()
    assert not output.out
    assert "mail not sent: 535" in output.err
    for text in (output.err, caplog.text):
        assert PASSWORD not in text and "/app/login" not in text
    assert not smtp_server.messages
    assert _unused_links(initialized) == 0
