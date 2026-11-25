"""The document starts the route's first reads beside its bundle; assets travel gzip-encoded."""

import base64
import gzip
import hashlib
import json
import subprocess
from pathlib import Path

from commonplace.web import STATIC, assets
from tests.test_cold_start_routes import cold_hub as cold_hub
from tests.test_commonplace_public_surface import request
from tests.test_web_bundle import built as built

ROOT = Path(__file__).resolve().parents[1]


def test_early_script_is_first_hashed_and_precedes_the_stylesheet(built, cold_hub, monkeypatch):
    from commonplace import web

    source, manifest = built
    monkeypatch.setattr(web, "STATIC", source)
    status, headers, body = request(cold_hub[0], "GET", "/app/")
    assert status == 200
    script = assets.early_script(source)
    assert b"<script data-early-hints>" + script + b"</script>" in body
    # A script after a pending stylesheet waits for it, so this one must come first.
    assert body.index(b"data-early-hints") < body.index(b'rel="stylesheet"')
    assert body.index(b'rel="stylesheet"') < body.index(b"data-boot-guard")
    digest = base64.b64encode(hashlib.sha256(script).digest()).decode()
    assert f"'sha256-{digest}'" in headers["Content-Security-Policy"]
    assert "unsafe-inline" not in headers["Content-Security-Policy"]


def test_early_reads_start_per_route_and_the_client_takes_each_once(built, tmp_path):
    source, manifest = built
    script = tmp_path / "early.js"
    script.write_bytes(assets.early_script(source))
    result = subprocess.run(
        ["node", str(ROOT / "tests/early_reads_harness.mjs"), str(script), str(STATIC / "api.js")],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    out = json.loads(result.stdout)
    board = "/app/build/" + manifest["routes"]["board"]
    shell = "/api/boards?view=shell"
    me = "/api/me"
    # The bundle for this route starts loading at once; only the board route reads its project early.
    assert out["board_slug"] == {
        "links": [board],
        "fetched": [me, shell, "/api/boards/project/cards"],
    }
    assert out["board_encoded"]["fetched"][-1] == "/api/boards/p%20q/cards"
    assert out["settings"] == {
        "links": ["/app/build/" + manifest["routes"]["settings"]],
        "fetched": [me],
    }
    assert out["board_chat"]["fetched"] == [me]
    # A bare #/board follows the saved project, else the first readable one, never an ambiguous key.
    assert out["board_saved"]["fetched"] == [me, shell, "/api/boards/ops/cards"]
    assert out["board_saved_gone"]["fetched"] == [
        me,
        shell,
        "/api/boards/ops/cards",
        "/api/boards/a/cards",
    ]
    assert out["board_unsaved"]["fetched"] == [me, shell, "/api/boards/a/cards"]
    assert out["board_ambiguous"]["fetched"] == [me, shell, "/api/boards/a/cards"]
    assert out["board_empty"]["fetched"] == [me, shell]
    assert out["offline"]["fetched"] == [me, shell, "/api/boards/project/cards"]
    # Taking: once; never for a write; not when stale or failed; errors still surface.
    assert out["first"] == {"from": "early"} and out["second"] == {"from": "network"}
    assert out["post"] == {"from": "network"} and out["get_after_post"] == {"from": "early"}
    assert out["stale"] == {"from": "network"} and out["failed"] == {"from": "network"}
    assert out["refused"] == 401 and out["expired"] == 1
    assert out["timed"] == {"from": "early"}
    assert out["network"] == [
        "GET /api/me",
        "POST /api/boards/p/cards",
        "GET /api/boards?view=shell",
        "GET /api/stalled",
    ]


def test_static_assets_gzip_on_request_and_decode_to_the_same_bytes(built, cold_hub, monkeypatch):
    from commonplace import web

    source, manifest = built
    monkeypatch.setattr(web, "STATIC", source)
    for name in [manifest["css"], manifest["routes"]["board"]]:
        path = "/app/build/" + name
        raw = (source / "build" / name).read_bytes()
        status, plain_headers, plain = request(cold_hub[0], "GET", path)
        assert status == 200 and plain == raw and "Content-Encoding" not in plain_headers
        assert plain_headers["Vary"] == "Accept-Encoding"
        status, headers, packed = request(
            cold_hub[0], "GET", path, headers={"Accept-Encoding": "br, gzip"}
        )
        assert status == 200 and headers["Content-Encoding"] == "gzip"
        assert gzip.decompress(packed) == raw and len(packed) < len(raw) / 2
        assert int(headers["Content-Length"]) == len(packed)
        assert headers["Vary"] == "Accept-Encoding" and "immutable" in headers["Cache-Control"]
        # Each representation has its own validator; a match is a bodiless 304.
        assert headers["ETag"] != plain_headers["ETag"]
        status, _headers, body = request(
            cold_hub[0],
            "GET",
            path,
            headers={"Accept-Encoding": "gzip", "If-None-Match": headers["ETag"]},
        )
        assert status == 304 and body == b""
        for refused in ("gzip;q=0", "gzip;q=invalid", "identity"):
            status, again, body = request(
                cold_hub[0], "GET", path, headers={"Accept-Encoding": refused}
            )
            assert status == 200 and body == raw and "Content-Encoding" not in again
