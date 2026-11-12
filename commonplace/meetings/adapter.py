"""The meeting provider interface and the in-memory fake.

A provider is one :class:`Adapter`. The engine, the consent rules and the routes never name a
platform; provider names appear only in adapter modules and fixtures. Nothing is registered by
default, so a hub offers no provider until one is added with :func:`register`.

This slice uses ``provider``, ``caps``, ``parse_link`` and ``test``. The lifecycle methods are
declared so the interface is whole, and raise :class:`NotImplementedError` until the lifecycle
slice gives the fake (and the first real adapter) a behaviour for them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator, Optional

PROVIDER = re.compile(r"[a-z][a-z0-9_-]{1,31}\Z")
# What ``test`` may tell the owner. Anything else an adapter returns is shown as ``failed``, so a
# provider error string (which may echo a credential) never reaches a response or an audit row.
REASONS = frozenset(
    {"bad_credentials", "missing_permission", "no_chat_send", "unreachable", "adapter_error"}
)


@dataclass(frozen=True)
class Caps:
    chat_send: bool = False
    chat_receive: bool = False
    per_speaker_audio: bool = False
    captions: bool = False
    remove_detect: bool = False
    participant_list: bool = False
    lobby: bool = False
    max_text: int = 2000
    transport: str = "webhook"  # or "socket": events arrive over an outbound connection


@dataclass(frozen=True)
class Connection:
    """What an adapter sees of a configured connection. The secrets exist only in memory."""

    id: str
    provider: str
    label: str
    secrets: dict = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class TestResult:
    __test__ = False  # not a pytest class

    ok: bool
    reason: str = ""


@dataclass(frozen=True)
class JoinRef:
    ref: str  # the normalized meeting link; sealed before it is stored
    ext_meeting: str  # opaque provider meeting id, never a passcode


@dataclass(frozen=True)
class Reject:
    reason: str


@dataclass(frozen=True)
class JoinHandle:
    handle: str


@dataclass(frozen=True)
class Lobby:
    handle: str


@dataclass(frozen=True)
class Event:
    kind: str  # joined | admitted | lobby_timeout | participant_joined | participant_left | chat |
    # removed | meeting_ended
    ext_event: str = ""
    ext_participant: Optional[str] = None
    host: bool = False
    text: str = ""
    addressed: bool = False


@dataclass(frozen=True)
class Frame:
    pcm: bytes = field(repr=False)
    ext_participant: Optional[str] = None


@dataclass(frozen=True)
class PostResult:
    ok: bool
    ext_post: str = ""


@dataclass(frozen=True)
class Verified:
    connection_id: str
    ext_meeting: str = ""


class Adapter:
    provider: str = ""
    caps: Caps = Caps()

    def parse_link(self, link: str) -> JoinRef | Reject:
        raise NotImplementedError

    def test(self, connection: Connection) -> TestResult:
        raise NotImplementedError

    def join(
        self, connection, join_ref, display_name, idempotency_key
    ) -> JoinHandle | Lobby | Reject:
        raise NotImplementedError

    def leave(self, connection, handle) -> None:
        raise NotImplementedError

    def events(self, connection, handle) -> Iterator[Event]:
        raise NotImplementedError

    def audio(self, connection, handle) -> Iterator[Frame]:
        raise NotImplementedError

    def post_chat(self, connection, handle, text, idempotency_key) -> PostResult:
        raise NotImplementedError

    def verify(self, connection, headers, body) -> Verified | Reject:
        raise NotImplementedError


class FakeAdapter(Adapter):
    """Scripted, in-memory, no network. ``tested`` records every connection ``test`` saw."""

    FAKE_HOST = "meet.example.test"

    def __init__(self, provider="fake", caps=None, result=None, raises=None):
        self.provider = provider
        self.caps = caps if caps is not None else Caps(chat_send=True, chat_receive=True)
        self.result = result if result is not None else TestResult(True)
        self.raises = raises
        self.tested: list[Connection] = []

    def parse_link(self, link):
        match = re.fullmatch(rf"https://{re.escape(self.FAKE_HOST)}/m/([A-Za-z0-9-]{{4,64}})", link)
        if not match:
            return Reject("foreign_host")
        return JoinRef(ref=link, ext_meeting=match[1])

    def test(self, connection):
        self.tested.append(connection)
        if self.raises is not None:
            raise self.raises
        return self.result


_ADAPTERS: dict[str, Adapter] = {}


def register(adapter: Adapter) -> Adapter:
    if not PROVIDER.fullmatch(adapter.provider):
        raise ValueError("invalid provider name")
    if adapter.provider in _ADAPTERS and _ADAPTERS[adapter.provider] is not adapter:
        raise ValueError("provider registered twice")
    _ADAPTERS[adapter.provider] = adapter
    return adapter


def unregister(provider: str) -> None:
    _ADAPTERS.pop(provider, None)


def get(provider: str) -> Optional[Adapter]:
    return _ADAPTERS.get(provider)


def providers() -> list[str]:
    return sorted(_ADAPTERS)
