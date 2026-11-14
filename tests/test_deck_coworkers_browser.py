"""Optional Capy screen checks using synthetic, already-redacted API fixtures."""

import mimetypes
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import pytest

STATIC = Path(__file__).resolve().parents[1] / "commonplace/web/static"


@pytest.fixture
def browser():
    if os.environ.get("COMMONPLACE_BROWSER_TESTS") == "off":
        pytest.skip("optional browser checks disabled")
    playwright = pytest.importorskip("playwright.sync_api", reason="optional browser dependency")
    with playwright.sync_playwright() as manager:
        try:
            instance = manager.chromium.launch(executable_path=shutil.which("google-chrome"))
        except playwright.Error as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip("optional browser executable unavailable")
            raise
        yield instance
        instance.close()


def open_screen(page):
    styles = [
        "app",
        "screens",
        "ui",
        "workbench",
        "bots",
        "deck",
        "deck-bots",
        "deck-capy",
        "deck-coworkers",
        "deck-shell",
    ]
    html = "<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
    html += "".join(f'<link rel="stylesheet" href="/app/{name}.css">' for name in styles)
    html += """</head><body><div id="root"><div class="shell two" data-view="bots">
      <nav class="rail" id="nav"></nav><main class="center"><div id="phone-bar"></div>
      <header class="head"><h1>Bots</h1></header><div id="content"></div></main>
      <div id="phone-tabs"></div></div></div><script type="module">
      import {applyDeck,createDeck} from '/app/deck.js';
      import {createBots} from '/app/bots.js';
      import {mount} from '/app/deck-workspace.js';
      import {escape as e} from '/app/api.js';
      applyDeck();
      const root=document.querySelector('#root'), content=document.querySelector('#content');
      const me={workspace:'fixture',actor:'user:one',people:{'user:one':'Owner'}};
      const api=async(path,options={})=>{
        const result=await fetch(path,{method:options.method||'GET',body:options.body?JSON.stringify(options.body):undefined});
        if(!result.ok)throw Object.assign(new Error('Request unavailable'),{status:result.status});
        return result.json();
      };
      const deck=createDeck({root,context:()=>({me}),title(){}});
      const shell=route=>{
        deck.appearance.sync(route);
        document.querySelector('#nav').outerHTML=deck.bar({hash:route}).replace('<nav class="rail"','<nav id="nav" class="rail"');
        document.querySelector('#phone-bar').innerHTML=deck.top({hash:route});
        document.querySelector('#phone-tabs').innerHTML=deck.tabs({hash:route});
      };
      let active;
      window.show=route=>{
        active?.stop();shell(route);
        if(route==='#/bots') {active=createBots({mount:()=>content,api,e});void active.show();}
        else active=mount({root:content,api,me,route,boards:[{slug:'product',name:'Product'}]});
      };
      show('#/bots/atlas/coworkers');window.ready=true;
      </script></body></html>"""
    bot = {
        "id": "atlas",
        "name": "Workspace helper",
        "job": "Keeps the release work moving",
        "section": "Workspace",
        "private": True,
        "unread": 2,
        "state": "working",
        "kind": "custom",
        "mine": True,
        "owner": {"id": "one", "name": "Owner"},
    }
    conversations = [
        {
            "id": "release",
            "title": "Release",
            "summary": "Shipping the saved-memory view",
            "board": "product",
            "bot_ids": ["atlas"],
            "kind": "dm",
            "state": "needs_you",
        },
        {
            "id": "checks",
            "title": "Checks",
            "summary": "Verifying the latest change",
            "bot_ids": ["atlas"],
            "kind": "dm",
            "state": "working",
        },
        {
            "id": "research",
            "title": "Research",
            "summary": "Reading the reference material",
            "bot_ids": ["atlas"],
            "kind": "dm",
            "state": "idle",
        },
    ]
    log = "\n".join(
        f"12:{index:02d}  checks  Synthetic redacted log line {index}" for index in range(25)
    )
    log += "\n<img src=x onerror=window.injected=true> [redacted:credential]"
    detail = {
        "conversation": {
            **conversations[0],
            "model": "Local model",
            "working_on": [
                {
                    "name": "Release",
                    "summary": "Waiting for approval",
                    "state": "needs_you",
                    "children": [
                        {"name": "Change", "summary": "Pushed the update", "state": "done"},
                        {"name": "Review", "summary": "Checked the change", "state": "done"},
                        {"name": "Checks", "summary": "Affected tests passed", "state": "done"},
                        {"name": "Publish", "summary": "Needs your go", "state": "needs_you"},
                    ],
                },
            ],
            "owns": [
                {"path": "src/memory.py"},
                {"path": "src/access.py", "shared": True},
                {"path": "web/static/memory.js"},
                {"path": "tests/test_memory.py"},
            ],
            "live_log": {"tail": log},
        },
        "jobs": [
            {
                "id": 7,
                "conversation_id": "release",
                "task_progress": {"availability": "available", "controls": {"can_follow_up": True}},
            }
        ],
    }
    writes = []
    shares = [
        {"user": {"id": "two", "name": "Workspace member"}, "talk": True, "see_shared": False},
        {"user": {"id": "three", "name": "Workspace reader"}, "talk": False, "see_shared": True},
    ]

    def serve(request):
        path = urlsplit(request.request.url).path
        if "/shares" in path and request.request.method != "GET":
            body = request.request.post_data_json if request.request.method != "DELETE" else {}
            writes.append((path, body))
            user = path.rsplit("/", 1)[-1] if request.request.method != "POST" else body["user"]
            if request.request.method == "DELETE":
                shares[:] = [row for row in shares if row["user"]["id"] != user]
                request.fulfill(json={"ok": True})
            else:
                row = {
                    "user": {"id": user, "name": me_json()["people"]["user:" + user]},
                    "talk": body["talk"],
                    "see_shared": body["see_shared"],
                }
                shares[:] = [old for old in shares if old["user"]["id"] != user] + [row]
                request.fulfill(json={"share": row})
        elif request.request.method == "POST":
            body = request.request.post_data_json
            writes.append((path, body))
            payload = (
                {
                    "receipt": {
                        "job_id": 7,
                        "request_id": body["request_id"],
                        "text": body["text"],
                        "operation": "follow_up",
                        "state": "applied",
                    }
                }
                if path.endswith("/controls")
                else {"message": {"conversation_id": "release"}}
            )
            request.fulfill(json=payload)
        elif path == "/":
            request.fulfill(body=html, content_type="text/html")
        elif path == "/api/bots":
            request.fulfill(json={"bots": [bot]})
        elif path == "/api/coworkers":
            request.fulfill(json={"bots": [], "conversations": conversations})
        elif path == "/api/coworkers/conversations/release":
            request.fulfill(json=detail)
        elif path in (
            "/api/coworkers/conversations/checks",
            "/api/coworkers/conversations/research",
        ):
            request.fulfill(
                json={
                    "conversation": next(
                        row for row in conversations if path.endswith("/" + row["id"])
                    ),
                    "jobs": [],
                }
            )
        elif path == "/api/projects/product":
            request.fulfill(json={"models": {"main": {"model": "Legacy model"}}})
        elif path.endswith("/checkpoint"):
            request.fulfill(
                json={
                    "open": [{"kind": "Checks", "detail": "Legacy checkpoint", "state": "running"}]
                }
            )
        elif path == "/api/me":
            request.fulfill(json={**me_json(), "can_admin": False})
        elif path == "/api/bots/settings":
            request.fulfill(json={"sharing": True})
        elif path.endswith("/shares"):
            request.fulfill(json={"shares": shares})
        elif path.endswith("/access"):
            request.fulfill(json={"entries": []})
        elif path.endswith("/access/log"):
            request.fulfill(
                json={
                    "log": [
                        {
                            "id": 1,
                            "at": "2026-01-02T12:34:00Z",
                            "action": "share",
                            "actor_id": "one",
                            "member_id": "two",
                        },
                        {
                            "id": 2,
                            "at": "2026-01-02T12:36:00Z",
                            "action": "permissions",
                            "actor_id": "one",
                            "member_id": "three",
                        },
                    ]
                }
            )
        else:
            file = STATIC / path.removeprefix("/app/")
            if file.is_file():
                request.fulfill(
                    body=file.read_bytes(),
                    content_type=mimetypes.guess_type(file.name)[0] or "application/octet-stream",
                )
            else:
                request.fulfill(status=404, body="")

    page.route("**/*", serve)
    page.goto("https://example.test/?ui=deck")
    page.wait_for_selector(".cb-worker")
    page.wait_for_function(
        "document.querySelector('[data-cb-log]')?.textContent.includes('[redacted:credential]')"
    )
    return writes


def me_json():
    return {
        "workspace": "fixture",
        "actor": "user:one",
        "people": {
            "user:one": "Owner",
            "user:two": "Workspace member",
            "user:three": "Workspace reader",
            "user:four": "New member",
        },
    }


def save_shot(page, name, tmp_path):
    folder = Path(os.environ.get("CAPY_SHOTS_DIR", str(tmp_path)))
    folder.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(folder / name), full_page=True)


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["desktop", "phone"])
def test_coworker_panels_modes_sharing_and_overview(browser, size, tmp_path):
    page = browser.new_page(
        viewport={"width": size[0], "height": size[1]},
        device_scale_factor=2,
        reduced_motion="reduce",
    )
    page.set_default_timeout(10000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        writes = open_screen(page)
        assert page.locator(".cb-worker").count() == 3
        assert (
            page.locator(".cb-worker-list").evaluate("el=>el.textContent.includes('Local model')")
            is False
        )
        assert page.get_by_text(
            "Runs on Local model · Shipping the saved-memory view", exact=True
        ).is_visible()
        assert page.locator("[data-cb-log] img").count() == 0
        assert page.evaluate("window.injected") is None
        log = page.locator("[data-cb-log]")
        assert log.evaluate("el=>el.scrollTop+el.clientHeight>=el.scrollHeight-2")
        assert log.evaluate("el=>getComputedStyle(el).height") == "176px"
        if size[0] == 1440:
            assert page.locator(".cb-worker-list").bounding_box()["width"] == 260
        else:
            assert (
                page.locator("[data-cb-composer]").evaluate("el=>getComputedStyle(el).position")
                == "static"
            )
            assert page.locator(".cb-heading").bounding_box()["x"] == 16
            assert page.locator("[data-cb-composer]").bounding_box()["y"] > log.bounding_box()["y"]
        assert page.locator(".cb-tree .cb-dot").count() == 0
        assert page.get_by_text("Waiting for approval", exact=True).count() == 0
        assert page.get_by_role("link", name="Conversation", exact=True).count() == 1
        assert page.get_by_role("link", name="Open conversation", exact=True).count() == 0
        assert page.locator("#cb-draft").get_attribute("placeholder") == "Message Release"
        assert page.locator("[data-cb-now]").count() == 1
        assert page.locator(".cb-mode-help li").count() == 3
        assert page.locator(".cb-mode-help").is_visible()
        assert page.locator(".cb-compose-reason").count() == 0
        assert page.locator(".cb-send").is_disabled()
        assert page.locator("#cb-draft").evaluate("el=>getComputedStyle(el).resize") == "none"
        assert page.locator(".cb-log-source").count() == 25
        colours = page.locator(".cb-log-line").first.evaluate(
            "el=>Array.from(el.querySelectorAll('span')).map(node=>getComputedStyle(node).color)"
        )
        assert len(set(colours)) == 3
        if size[0] == 390:
            assert page.locator(".cb-log-message").first.bounding_box()["height"] > 20
            assert (
                page.locator(".cb-log-message").first.bounding_box()["x"]
                == page.locator(".cb-log-message").nth(1).bounding_box()["x"]
            )
        page.locator('[data-cb-mode="steer"]').click()
        assert page.locator("#cb-draft").get_attribute("placeholder") == "Steer Release"
        page.locator("#cb-draft").fill("  Exact follow-up  ")
        page.locator(".cb-send").click()
        page.get_by_text(
            "Follow-up received. Worker application is unconfirmed.", exact=True
        ).wait_for()
        assert writes[-1][0] == "/api/coworkers/jobs/7/controls"
        assert writes[-1][1]["text"] == "  Exact follow-up  "
        page.locator('[data-cb-mode="chat"]').click()
        page.locator("#cb-draft").fill("Exact chat")
        page.locator(".cb-send").click()
        page.get_by_text("Message saved.", exact=True).wait_for()
        assert writes[-1][0] == "/api/coworkers/conversations/release/messages"
        page.locator("#content").evaluate("el=>el.scrollTop=0")
        save_shot(page, f"coworkers-{size[0]}.png", tmp_path)
        page.locator("[data-cb-share]").click()
        page.get_by_role("heading", name="Who can use Workspace helper", exact=True).wait_for()
        page.locator("[data-people-access]").wait_for()
        if size[0] == 1440:
            assert page.locator(".bot-access").bounding_box()["width"] == 460
        assert page.locator(".bot-access").evaluate(
            "el=>getComputedStyle(el).backgroundColor"
        ) == page.locator(".rail").evaluate("el=>getComputedStyle(el).backgroundColor")
        explanation = page.locator(".cb-share-explanation").bounding_box()
        pills = page.locator(".bot-access-tabs").bounding_box()
        assert pills["y"] - explanation["y"] - explanation["height"] == pytest.approx(12)
        title = page.locator("#sheet-h").bounding_box()
        first_pill = page.locator(".bot-access-tabs button").first.bounding_box()
        assert first_pill["x"] == title["x"]
        drawer = page.locator(".bot-access").bounding_box()
        assert title["x"] - drawer["x"] == (25 if size[0] == 1440 else 17)
        select_style = page.locator("[data-capy-permission]").first.evaluate(
            "el=>{const s=getComputedStyle(el);return [s.backgroundColor,s.borderTopWidth,s.borderRadius,s.boxShadow]}"
        )
        assert select_style == ["rgb(30, 30, 30)", "0px", "12px", "none"]
        assert page.get_by_text(
            "You gave Workspace member access to this bot.", exact=True
        ).is_visible()
        assert page.locator("[data-capy-permission]").count() == 2
        assert page.locator(".cb-share-footer").bounding_box()["y"] < size[1] - 56
        save_shot(page, f"sharing-{size[0]}.png", tmp_path)
        before = len(writes)
        page.locator('[data-capy-permission="two"]').select_option("both")
        assert len(writes) == before
        page.get_by_role("button", name="Save", exact=True).click()
        page.locator("[data-access-saved]").wait_for()
        assert writes[-1] == ("/api/bots/atlas/shares/two", {"talk": True, "see_shared": True})
        before = len(writes)
        page.get_by_role("button", name="Stop sharing", exact=True).click()
        assert len(writes) == before
        page.get_by_role("button", name="Stop sharing now", exact=True).click()
        page.get_by_text("Only you.", exact=True).wait_for()
        assert len(writes) == before + 2
        page.get_by_role("button", name="Close", exact=True).click()
        assert not page.get_by_role("dialog").count()
        page.locator('[data-cb-worker="conversation:checks"]').click()
        page.wait_for_function(
            "document.querySelector('.cb-detail-head h2')?.textContent==='Checks'"
        )
        assert page.get_by_role("heading", name="Owns", exact=True).count() == 0
        assert page.get_by_role("heading", name="Live log", exact=True).count() == 0
        assert page.locator('[data-cb-mode="steer"]').is_disabled()
        page.evaluate("show('#/bots')")
        page.wait_for_selector(".cb-bot-row")
        assert page.get_by_text("3 coworkers", exact=True).is_visible()
        if size[0] == 390:
            assert page.locator("#phone-bar .m-top > b").inner_text() == "Bots"
            assert page.locator("#phone-bar .m-top > b").is_visible()
        save_shot(page, f"bots-{size[0]}.png", tmp_path)
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        assert not errors, errors
    finally:
        page.close()
