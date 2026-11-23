"""Isolated actual HTTP gateway for Activity admission and browser tests."""

import json
import threading
from http.client import HTTPConnection
from types import SimpleNamespace
from typing import Any, cast

import pytest

from commonplace import sessions
from commonplace.control_db import ControlDB
from commonplace.gateway import Gateway
from commonplace.workers import WorkerSupervisor


@pytest.fixture
def activity_hub(tmp_path):
    db = ControlDB(str(tmp_path / "control.sqlite3"))
    supervisor = WorkerSupervisor(str(tmp_path / "workspaces"), launcher=cast(Any, object()))
    gateway: Any = Gateway(db, supervisor)
    user = db.create_user("activity@example.test")
    workspace = db.create_workspace("Activity review", user)
    cookie = sessions.start_session(db, user, workspace)
    server = gateway.build_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    hub = SimpleNamespace(
        db=db,
        gateway=gateway,
        port=server.server_address[1],
        user=user,
        workspace=workspace,
        cookie=cookie,
    )
    try:
        yield hub
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        gateway.email_auth.close()
        gateway.webhooks.stop()
        gateway.handoff.close()
        supervisor.stop_all()
        db.close()


def request(
    hub,
    method="GET",
    path="/api/compute/activity",
    body=None,
    *,
    cookie=True,
    token=None,
    origin=True,
):
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = f"{sessions.COOKIE}={hub.cookie if cookie is True else cookie}"
    if token:
        headers["Authorization"] = "Bearer " + token
    if origin:
        headers["Origin"] = f"http://127.0.0.1:{hub.port}" if origin is True else origin
    conn = HTTPConnection("127.0.0.1", hub.port)
    try:
        conn.request(method, path, json.dumps(body).encode() if body is not None else None, headers)
        response = conn.getresponse()
        raw = response.read()
        return response.status, json.loads(raw), dict(response.getheaders())
    finally:
        conn.close()
