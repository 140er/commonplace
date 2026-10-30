"""Turning a request's credentials into a :class:`Principal`.

The gateway asks each authenticator in turn. Personal access tokens are the
only kind today; the OAuth 2.1 PR adds an authenticator for its access tokens
(``auth = 'oauth'``) plus the ``/oauth/*`` and ``/.well-known/*`` routes, and
nothing past this point changes: policy, stamping and audit see a Principal.
"""

from __future__ import annotations

from typing import Optional, Protocol

from .control_db import ControlDB, Principal


class Authenticator(Protocol):
    def authenticate(self, bearer: str) -> Optional[Principal]: ...


class PersonalTokenAuthenticator:
    def __init__(self, db: ControlDB):
        self.db = db

    def authenticate(self, bearer: str) -> Optional[Principal]:
        return self.db.authenticate_token(bearer)


def bearer_from_header(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    scheme, _, credential = value.strip().partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        return None
    return credential.strip()
