"""Web pages: the Memory settings toggle and the card's handoff state. Owner: part T14.

GET  /app/login, POST /app/login     paste a cmmn_ token; server-side session
GET  /app/settings/memory            workspace toggle, threshold, overrides
POST /app/settings/memory            form post (CSRF token), owner only
GET  /app/cards/<task ref>           the card's handoff state: "handed off",
                                     link to the old thread and the new agent
GET  /app/threads/<id>               the stored thread, verbatim, paged
"""

from __future__ import annotations


def register(ctx) -> None:
    raise NotImplementedError
