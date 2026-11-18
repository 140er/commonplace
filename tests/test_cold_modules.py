"""Initial modules and event hydration avoid unrelated routes and old backlog."""

import re
from pathlib import Path

import pytest

from commonplace import events
from commonplace.web import STATIC
from tests.test_cold_start_routes import cold_hub as cold_hub
from tests.test_commonplace_boards import api


def initial_modules(name="app.js", seen=None):
    seen = set() if seen is None else seen
    if name in seen:
        return seen
    seen.add(name)
    source = (STATIC / name).read_text()
    for dependency in re.findall(r"^import .*? from ['\"]\./([^'\"]+)['\"]", source, re.M):
        initial_modules(dependency, seen)
    return seen


@pytest.mark.parametrize(
    "name",
    [
        "admin.js",
        "instance-admin.js",
        "console.js",
        "invites.js",
        "topology-map.js",
        "chat.js",
        "details.js",
        "memory.js",
        "bots.js",
    ],
)
def test_unopened_route_is_outside_initial_graph(name):
    assert name not in initial_modules(), f"{name} blocks identity and the first shell"


def test_initial_modules_preload_before_app_execution(cold_hub):
    hub, cookie, _service = cold_hub
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=5)
    try:
        conn.request("GET", "/app/", headers=cookie)
        response = conn.getresponse()
        html = response.read().decode()
    finally:
        conn.close()
    assert response.status == 200
    preloads = set(re.findall(r'rel="modulepreload" href="/app/([^"]+)"', html))
    assert initial_modules() <= preloads
    assert html.index('src="/app/deck-prepaint.js"') < html.index('src="/app/app.js"')


def test_static_files_revalidate_without_redownload(cold_hub):
    import http.client

    hub, cookie, _service = cold_hub
    conn = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=5)
    try:
        conn.request("GET", "/app/api.js", headers=cookie)
        response = conn.getresponse()
        body = response.read()
        etag = response.getheader("ETag")
        assert etag and "no-cache" in response.getheader("Cache-Control", "")
        conn.request("GET", "/app/api.js", headers={**cookie, "If-None-Match": etag})
        response = conn.getresponse()
        assert response.status == 304 and response.read() == b""
        assert response.getheader("Content-Length") is None
        assert body
    finally:
        conn.close()


def test_initial_owner_events_use_recent_window_and_resume(cold_hub):
    hub, cookie, _service = cold_hub
    with hub.db._lock:
        hub.db._conn.executemany(
            "INSERT INTO events(at,workspace_id,kind,subject) VALUES (?,?,?,?)",
            [("2026-01-01T00:00:00Z", hub.workspace, "card.updated", str(n)) for n in range(600)],
        )
        hub.db._conn.commit()
    status, first = api(hub, cookie, "GET", "/api/events?after=0&epoch=")
    assert status == 200
    assert len(first["events"]) == 200
    assert first["events"][-1]["subject"] == "599"
    assert first["seq"] == first["events"][-1]["seq"]
    event = events.emit(hub.db, hub.workspace, "card.updated", subject="new")
    status, following = api(hub, cookie, "GET", f"/api/events?after={first['seq']}")
    assert status == 200
    assert following["events"] == [event]


@pytest.mark.parametrize(
    "scenario",
    [
        "settings",
        "admin",
        "memory",
        "bots",
        "board",
        "retry",
        "single-flight",
        "styles",
        "card-history",
    ],
)
def test_route_imports_only_its_controllers(scenario):
    import subprocess

    result = subprocess.run(
        ["node", str(Path(__file__).with_name("cold_modules.mjs")), scenario],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_only_content_fingerprinted_assets_are_immutable(cold_hub, tmp_path, monkeypatch):
    import hashlib
    import http.client

    from commonplace import web

    hub, cookie, _service = cold_hub
    body = b"export const fixture = true;"
    name = "fixture." + hashlib.sha256(body).hexdigest()[:12] + ".js"
    (tmp_path / name).write_bytes(body)
    monkeypatch.setattr(web, "STATIC", tmp_path)
    conn = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=5)
    try:
        conn.request("GET", "/app/" + name, headers=cookie)
        response = conn.getresponse()
        assert response.status == 200 and response.read() == body
        assert "immutable" in response.getheader("Cache-Control")
    finally:
        conn.close()
