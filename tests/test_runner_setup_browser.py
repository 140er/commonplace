"""Browser-free consent action proof plus optional, isolated Deck captures."""

import json
import mimetypes
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest

STATIC = Path(__file__).resolve().parents[1] / "commonplace/web/static"
ITEM = {
    "id": "opaque-consent",
    "machine_id": "opaque-machine",
    "kind": "runner_consent",
    "approval_kind": "grant",
    "state": "pending",
    "label": "Build machine",
    "provider": "host",
    "fingerprint": "0123456789abcdef" * 4,
    "scope_digest": "a" * 64,
    "projects": ["Website"],
    "topics": ["Development"],
    "runtime": "approval-required",
}


def test_browser_free_consent_actions_and_no_raw_ids():
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("runner_setup_consent.mjs"))],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def browser():
    if os.environ.get("COMMONPLACE_BROWSER_TESTS") == "off":
        pytest.skip("optional browser checks disabled")
    playwright = pytest.importorskip("playwright.sync_api", reason="optional browser dependency")
    with playwright.sync_playwright() as manager:
        try:
            browser = manager.chromium.launch(executable_path=shutil.which("google-chrome"))
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("optional browser executable unavailable")
            raise
        yield browser
        browser.close()


@pytest.mark.parametrize("width", [390, 768, 1440])
@pytest.mark.parametrize("kind", ["login", "grant", "attest", "unsupported"])
def test_browser_consent_and_fingerprint_comparison(browser, width, kind):
    item = {**ITEM, "approval_kind": "login" if kind == "unsupported" else kind}
    if kind in ("login", "unsupported"):
        item["provider"] = "claude" if kind == "unsupported" else "codex"
        item["unsupported"] = kind == "unsupported"
    if kind == "attest":
        item["binding"] = {
            "topics": ["Development"],
            "provider_instance_id": "opaque-provider",
            "worker_connection_id": "opaque-worker",
        }
    requests = []
    page = browser.new_page(viewport={"width": width, "height": 900})

    def route(r):
        path = urlsplit(r.request.url).path
        if r.request.method == "POST":
            requests.append((path, r.request.post_data_json))
            r.fulfill(
                body=json.dumps({"ok": True, "agent_ready": False}), content_type="application/json"
            )
            return
        if path == "/app/test":
            # Real Needs me component and Deck tokens, with only synthetic data.
            body = (
                '<!doctype html><html data-ui="deck"><head><link rel="stylesheet" href="/app/app.css"><link rel="stylesheet" href="/app/workbench.css"><link rel="stylesheet" href="/app/deck.css"></head><body class="ui-deck"><div id="mount" style="height:100vh"></div><script type="module">import {createNeeds} from "/app/needs.js"; const view=createNeeds({mount:document.querySelector("#mount"),api:async (url,options)=>{if(options?.method===\'POST\'){const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(options.body)});return r.json();} return {holds:[]};}}); await view.show({needs:{runner_setup:['
                + json.dumps(item)
                + "]},boards:[]});</script></body></html>"
            )
            r.fulfill(body=body, content_type="text/html")
            return
        file = STATIC / path.removeprefix("/app/")
        if file.is_file():
            r.fulfill(
                body=file.read_bytes(),
                content_type=mimetypes.guess_type(file)[0] or "application/octet-stream",
            )
        else:
            r.fulfill(status=404, body="")

    page.route("**/*", route)
    try:
        page.goto("http://example.test/app/test")
        card = page.locator(".runner-consent")
        card.wait_for()
        assert "opaque-" not in card.inner_text()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert card.locator("h2").evaluate("e=>getComputedStyle(e).fontSize") == "24px"
        if width == 390:
            assert card.bounding_box()["x"] == 16
        from tests.test_deck_card_needs_browser import assert_contrast

        assert_contrast(page, ".runner-consent")
        if kind == "unsupported":
            assert card.locator("button").count() == 0
        else:
            card.locator("button").click()
            assert not requests
            assert "fingerprint first" in card.inner_text()
            card.locator("input[type=checkbox]").check()
            card.locator("button").click()
            page.wait_for_function(
                "document.querySelector('[data-runner-consent-status]').textContent.includes('Saved')"
            )
            assert len(requests) == 1
            path, data = requests[0]
            assert path.endswith("/attest" if kind == "attest" else "/decide")
            assert (data["setup_attestation"] if kind == "attest" else data)["fingerprint"] == item[
                "fingerprint"
            ]
        target = os.environ.get("CP_UI_SHOTS")
        if target:
            output = Path(target)
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / f"runner-setup-{kind}-{width}.png"), full_page=True)
    finally:
        page.close()
