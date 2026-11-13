"""Browser-free action proof and optional captures inside the real Deck shell."""

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


def open_setup(browser, width, mode):
    mode = mode.removeprefix("after-")
    item = {**ITEM}
    if mode in ("login", "device", "manual"):
        item.update(
            approval_kind="login",
            provider="claude" if mode == "manual" else "codex",
            state="approved" if mode == "device" else "pending",
            manual_login=mode == "manual",
        )
    elif mode == "attest":
        item.update(
            approval_kind="attest",
            binding={
                "topics": ITEM["topics"],
                "provider_instance_id": "opaque-provider",
                "worker_connection_id": "opaque-worker",
            },
        )
    elif mode == "long":
        item.update(
            label="Build machine for the shared publication and archive projects",
            projects=[
                "Website",
                "Publication archive",
                "Documentation",
                "Release checks",
                "Research notes",
            ],
        )
    requests = []
    context = browser.new_context(viewport={"width": width, "height": 1100}, device_scale_factor=2)
    page = context.new_page()
    responses = {
        "/api/me": {
            "actor": "user:owner",
            "name": "Owner",
            "workspace": "demo",
            "can_admin": True,
            "people": {},
            "role": "owner",
        },
        "/api/boards": {"boards": []},
        "/api/needs-me": {"count": 1, "runner_setup": [item]},
        "/api/bots": {"bots": []},
        "/api/rules/count": {"waiting": 0},
        "/api/holds": {"holds": []},
        "/api/events": {"events": [], "seq": 0, "epoch": "test"},
    }

    def serve(route):
        path = urlsplit(route.request.url).path
        if path.startswith("/api/"):
            if route.request.method == "POST":
                requests.append((path, route.request.post_data_json))
                route.fulfill(
                    status=409 if mode == "failed" else 200,
                    body=json.dumps(
                        {"error": "changed"}
                        if mode == "failed"
                        else {
                            "url": "https://auth.openai.com/codex/device",
                            "user_code": "ABCD-EFGH",
                        }
                        if path.endswith("/device")
                        else {"ok": True, "agent_ready": False}
                    ),
                    content_type="application/json",
                )
            else:
                route.fulfill(
                    body=json.dumps(responses.get(path, {})), content_type="application/json"
                )
        else:
            name = path.removeprefix("/app/") or "index.html"
            file = STATIC / name
            if file.is_file():
                route.fulfill(
                    body=file.read_bytes(),
                    content_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                )
            else:
                route.fulfill(status=404, body="")

    page.route("**/*", serve)
    page.goto("http://example.test/app/?ui=deck#/needs")
    page.locator(".runner-consent").wait_for()
    page.evaluate("document.fonts.ready")
    return context, page, item, requests


MEASURE = """() => {
  const card=document.querySelector('.runner-consent'), grid=card.querySelector('.fingerprint');
  const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
  const style=e=>{const s=getComputedStyle(e);return {background:s.backgroundColor,color:s.color,
    fontSize:s.fontSize,fontWeight:s.fontWeight,radius:s.borderRadius,padding:s.padding};};
  const button=card.querySelector('.runner-primary'), check=card.querySelector('[type=checkbox]');
  return {scale:devicePixelRatio,card:rect(card),pageTitle:rect(document.querySelector('#title')),
    grid:{...rect(grid),...style(grid),columns:getComputedStyle(grid).gridTemplateColumns.split(' ').length,
      gap:getComputedStyle(grid).gap,userSelect:getComputedStyle(grid).userSelect,
      groups:[...grid.children].map(e=>({text:e.textContent,...rect(e)}))},
    primary:button?{disabled:button.disabled,...style(button)}:null,
    checkbox:check?{...rect(check),checked:check.checked,...style(check)}:null,
    step:card.querySelector('.runner-step').textContent,
    stepLine:{...rect(card.querySelector('.runner-step')),...style(card.querySelector('.runner-step'))},
    title:{...rect(card.querySelector('h2')),...style(card.querySelector('h2'))},
    refuse:card.querySelector('.runner-refuse')?style(card.querySelector('.runner-refuse')):null,
    name:style(card.querySelector('.runner-name')),
    labels:[...card.querySelectorAll('dt')].map(style),values:[...card.querySelectorAll('dd')].map(style),
    instruction:style(card.querySelector('.runner-instruction')),
    shell:{top:rect(document.querySelector('#board-nav')),tabs:rect(document.querySelector('#phone-tabs'))}};
}"""


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize(
    "mode",
    [
        "login",
        "device",
        "grant",
        "attest",
        "after",
        "after-login",
        "after-device",
        "after-attest",
        "refuse",
        "failed",
        "long",
        "manual",
    ],
)
def test_deck_setup_states_and_measurements(browser, width, mode):
    context, page, item, requests = open_setup(browser, width, mode)
    screen = mode.removeprefix("after-")
    try:
        card = page.locator(".runner-consent")
        before = page.evaluate(MEASURE)
        assert before["scale"] == 2
        assert before["title"]["fontSize"] == "24px"
        assert before["stepLine"]["color"] == "rgb(148, 148, 148)"
        assert before["stepLine"]["y"] + before["stepLine"]["height"] <= before["title"]["y"]
        assert before["refuse"]["color"] == "rgb(240, 112, 138)"
        assert before["card"]["width"] == (720 if width == 1280 else 358)
        assert before["card"]["x"] == before["pageTitle"]["x"]
        assert before["grid"]["columns"] == (4 if width == 1280 else 2)
        assert before["grid"]["gap"] == "12px"
        assert before["grid"]["radius"] == "12px" and before["grid"]["padding"] == "16px"
        assert before["grid"]["fontSize"] == "16px"
        assert before["grid"]["background"] == "rgb(30, 30, 30)"
        assert before["grid"]["userSelect"] == "text"
        assert [g["text"] for g in before["grid"]["groups"]] == [
            item["fingerprint"][i : i + 8] for i in range(0, 64, 8)
        ]
        assert before["name"]["fontSize"] == "16px" and before["name"]["fontWeight"] == "600"
        assert all(
            r["fontSize"] == "13px" and r["color"] == "rgb(148, 148, 148)" for r in before["labels"]
        )
        assert all(
            r["fontSize"] == "15px" and r["color"] == "rgb(255, 255, 255)" for r in before["values"]
        )
        assert before["instruction"]["color"] == "rgb(180, 180, 180)"
        assert (
            before["step"]
            == f"Step {4 if screen == 'attest' else 2 if screen == 'device' else 1 if screen in ('login', 'manual') else 3} of 4"
        )
        assert "opaque-" not in card.inner_text()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        shell = page.locator("#board-nav" if width == 1280 else "#phone-tabs .m-tabs")
        assert shell.is_visible()
        states = {"before": before}
        if mode == "manual":
            assert not card.locator("[type=checkbox]").count()
            assert "waiting for sign-in on the machine" in card.inner_text()
            assert 'sh "$HOME/.commonplace-runner/sign-in" claude' in card.inner_text()
        else:
            primary = card.locator(".runner-primary")
            assert primary.is_disabled()
            assert before["primary"]["background"] == "rgb(30, 30, 30)"
            assert before["primary"]["color"] == "rgb(148, 148, 148)"
            assert before["checkbox"]["width"] == before["checkbox"]["height"] == 20
            assert before["checkbox"]["radius"] == "6px"
            if mode == "failed" or mode.startswith("after"):
                card.locator("[type=checkbox]").check()
                states["checked"] = page.evaluate(MEASURE)
                assert primary.is_enabled()
                assert states["checked"]["primary"]["background"] == "rgb(255, 255, 255)"
                assert states["checked"]["checkbox"]["background"] == "rgb(255, 255, 255)"
                primary.click()
                page.wait_for_function(
                    "() => !document.querySelector('[data-runner-consent-status]') || document.querySelector('[data-runner-consent-status]').textContent.includes('Could not save')"
                )
                assert len(requests) == 1
                if mode.startswith("after"):
                    assert card.locator("button,input").count() == 0
                    assert (
                        card.locator("output").inner_text()
                        == "Confirmed. The machine continues setup by itself."
                    )
                    assert "cannot run work yet" not in card.inner_text()
                else:
                    assert "Could not save" in card.inner_text()
                    assert "Confirmed." not in card.inner_text()
            elif mode == "refuse":
                card.get_by_role("button", name="This is not my machine", exact=True).click()
                assert not requests
                card.get_by_role("button", name="Cancel", exact=True).click()
                assert not requests and card.locator(".runner-primary").is_disabled()
                card.get_by_role("button", name="This is not my machine", exact=True).click()
                assert card.get_by_role("button", name="Yes, refuse this machine").is_visible()
                assert not card.locator("[type=checkbox]").count()
        states["captured"] = page.evaluate(MEASURE)
        target = os.environ.get("CP_UI_SHOTS")
        if target:
            folder = Path(target)
            folder.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(folder / f"runner-setup-{mode}-{width}.png"), full_page=True)
            (folder / f"runner-setup-{mode}-{width}.json").write_text(json.dumps(states, indent=2))
        # Capture the confirm step before proving that refusal performs one write.
        if mode == "refuse":
            card.get_by_role("button", name="Yes, refuse this machine").click()
            page.wait_for_function(
                "document.querySelector('.runner-consent').textContent.includes('Refused.')"
            )
            assert len(requests) == 1 and requests[0][0].endswith("/refuse")
            assert card.locator("button,input").count() == 0
        elif mode == "device":
            card.locator("[type=checkbox]").check()
            card.locator(".runner-primary").click()
            page.wait_for_selector(".runner-device")
            assert "ABCD-EFGH" in card.inner_text()
            assert card.locator("button,input").count() == 0
    finally:
        context.close()
