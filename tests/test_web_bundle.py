"""Production asset bytes, cascade, cache validators and deferred module identity."""

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from commonplace.web import STATIC, assets
from tests.test_cold_start_routes import cold_hub as cold_hub
from tests.test_commonplace_public_surface import request

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    source = tmp_path_factory.mktemp("bundle") / "static"
    shutil.copytree(STATIC, source, ignore=shutil.ignore_patterns("build"))
    for output in [source / "build", source / "again"]:
        result = subprocess.run(
            ["node", str(ROOT / "scripts/web-build/build.mjs"), str(source), str(output)],
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    return source, json.loads((source / "build/manifest.json").read_text())


def test_build_is_reproducible_content_addressed_and_keeps_pinned_cascade(built):
    source, manifest = built
    first = {p.name: p.read_bytes() for p in (source / "build").iterdir()}
    second = {p.name: p.read_bytes() for p in (source / "again").iterdir()}
    assert first == second
    for name, body in first.items():
        if name != "manifest.json":
            assert hashlib.sha256(body).hexdigest()[:16] in name
    pinned = re.findall(
        r'<link rel="stylesheet" href="/app/([^"]+)"', (source / "index.html").read_text()
    )
    assert manifest["styles"][: len(pinned)] == pinned
    css = first[manifest["css"]].decode()
    assert [css.index(f"/* {name} */") for name in pinned] == sorted(
        css.index(f"/* {name} */") for name in pinned
    )
    assert "@import" not in css
    assert len(manifest["fonts"]) == 2 and "font-display: swap" in css
    assert 'url("/app/fonts/' in css


def test_production_document_has_one_css_no_module_waterfall_and_csp_hashes(
    built, cold_hub, monkeypatch
):
    from commonplace import web

    source, manifest = built
    monkeypatch.setattr(web, "STATIC", source)
    status, headers, body = request(cold_hub[0], "GET", "/app/")
    assert status == 200
    assert body.count(b'rel="stylesheet"') == 1
    assert body.count(b'as="font"') == 2
    assert b'rel="modulepreload"' not in body and b"route-preload.js" not in body
    assert manifest["css"].encode() in body
    assert (
        body.index(b"data-boot-guard")
        < body.index(b"deck-prepaint.js")
        < body.index(b"data-bundle-entry")
    )
    for script in re.findall(
        rb"<script[^>]*(?:data-boot-guard|data-bundle-entry)[^>]*>(.*?)</script>", body, re.S
    ):
        import base64

        digest = base64.b64encode(hashlib.sha256(script).digest()).decode()
        assert f"'sha256-{digest}'" in headers["Content-Security-Policy"]
    assert b"Email a sign-in link" not in body
    for name in [manifest["css"], manifest["routes"]["board"]]:
        status, headers, body = request(cold_hub[0], "GET", "/app/build/" + name)
        assert status == 200 and "immutable" in headers["Cache-Control"]
        status, _headers, body = request(
            cold_hub[0], "GET", "/app/build/" + name, headers={"If-None-Match": headers["ETag"]}
        )
        assert status == 304 and body == b""


def test_route_bundles_share_singletons_and_do_not_eagerly_evaluate_other_screens(built):
    source, manifest = built
    # Execute the shipped runtime while omitting only the app's DOM bootstrap.
    # A lazy closure registering an already evaluated API must reuse its people map.
    script = r"""
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const dir=process.argv[1], manifest=JSON.parse(fs.readFileSync(dir+'/manifest.json'));
let entry=fs.readFileSync(dir+'/'+manifest.routes.shell,'utf8').replace("runtime.require('app.js');",'').replace('export {};','');
const context={}; vm.createContext(context); vm.runInContext(entry,context);
const runtime=context.commonplaceModules, api=runtime.require('api.js');
api.setPeople({'user:fixture':'Exact fixture name'});
vm.runInContext(fs.readFileSync(dir+'/'+manifest.modules['details.js'],'utf8'),context);
assert.equal(runtime.require('api.js'),api);
assert.equal(runtime.require('api.js').actor('user:fixture'),'Exact fixture name');
assert.equal(runtime.require('routes.js').boardRoute({boards:[{slug:'project'}]}),'#/board/project');
assert.throws(()=>runtime.require('admin.js'),/Missing screen module/);
"""
    result = subprocess.run(
        ["node", "-e", script, str(source / "build")], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_manifest_retains_unbundled_development_entrypoint(tmp_path):
    shutil.copy(STATIC / "boot-guard.js", tmp_path)
    shutil.copy(STATIC / "app.js", tmp_path)
    html = assets.index((STATIC / "index.html").read_bytes(), tmp_path)
    assert b'src="/app/app.js"' in html and b"modulepreload" in html
    assert html.count(b'rel="stylesheet"') == 23


@pytest.mark.parametrize(
    "encoding,compressed",
    [
        ("gzip", True),
        ("gzip;q=0", False),
        ("br, gzip;q=0.5", True),
        ("gzip;q=invalid", False),
        ("", False),
    ],
)
def test_large_json_wire_compression_is_opt_in_and_verbatim(encoding, compressed):
    import gzip
    import io
    from types import SimpleNamespace

    from commonplace.boards._http import send_json

    headers = {}
    handler = SimpleNamespace(
        headers={"Accept-Encoding": encoding},
        wfile=io.BytesIO(),
        send_response=lambda _status: None,
        send_header=lambda key, value: headers.update({key: value}),
        end_headers=lambda: None,
    )
    words = "Exact words $& $' $` — café\n" * 300
    send_json(handler, 200, {"words": words})
    body = handler.wfile.getvalue()
    assert int(headers["Content-Length"]) == len(body)
    assert (headers.get("Content-Encoding") == "gzip") == compressed
    decoded = gzip.decompress(body) if compressed else body
    assert json.loads(decoded)["words"] == words
    assert headers["Cache-Control"] == "no-store" and headers["Vary"] == "Accept-Encoding"
