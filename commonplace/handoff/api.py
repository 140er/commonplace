"""REST API for handoff, on the gateway. Owner: part T09.

All routes need ``Authorization: Bearer cmmn_…`` (``gateway.authenticate``)
and answer JSON; errors are ``{"error": "..."}`` with 400/401/404/409.
A thread or handoff the caller can't see is 404, never 403.

POST /api/handoff                         handoff:write   HandoffRequest → HandoffResult
GET  /api/handoffs?task=#142              threads:read    list
GET  /api/handoffs/<id>                   threads:read
POST /api/handoffs/<id>/successor         handoff:write   {agent_ref, url?, thread_id?}
POST /api/handoffs/<id>/relaunch          owner
GET  /api/threads/<id>                    threads:read    StoredThread
GET  /api/threads/<id>/messages?start=&end=   threads:read
POST /api/threads/<id>/recall             threads:read    {query, limit?, follow_chain?}
GET  /api/threads/<id>/chain              threads:read
GET  /api/settings/handoff?agent=&task=   any token: rows + effective setting
PUT  /api/settings/handoff                owner (members: task rows' enabled only)
DELETE /api/settings/handoff?scope=&ref=  owner
GET  /api/webhooks, POST /api/webhooks, DELETE /api/webhooks/<id>   owner
"""

from __future__ import annotations


def register(ctx) -> None:
    raise NotImplementedError
