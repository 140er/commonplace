"""Desktop and phone captures of production setup surfaces with synthetic state."""

import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

STATIC = Path(__file__).resolve().parents[1] / "commonplace/web/static"


@pytest.mark.parametrize("width", [1280, 390])
def test_ready_to_start_browser(tmp_path, width):
    pw = pytest.importorskip("playwright.sync_api")
    static = Path(os.environ.get("READY_STATIC", STATIC))
    baseline = os.environ.get("READY_BASELINE") == "1"
    output = Path(os.environ.get("READY_CAPTURES", tmp_path))
    output.mkdir(parents=True, exist_ok=True)
    html = """<!doctype html><html><head>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <link rel="stylesheet" href="/app/app.css"><link rel="stylesheet" href="/app/bots.css">
    <link rel="stylesheet" href="/app/deck.css"><link rel="stylesheet" href="/app/deck-coworkers.css">
    </head><body><main id="content"></main><script type="module">
    import {mountProject} from '/app/projects.js';
    import {createBots} from '/app/bots.js';
    import {mount} from '/app/deck-coworkers.js';
    import {escape as e} from '/app/api.js';
    const root=document.querySelector('#content');let active;
    const states=['model','key','machine','connection','trust','notifications'];
    const ready=Object.fromEntries(states.map(id=>[id,{state:id==='notifications'?'not_needed':'done'}]));
    const codes={model:'needs_configuration',key:'key_missing',machine:'runner_not_configured',connection:'connection_missing',trust:'machine_untrusted'};
    window.show=async(surface,ui,owner,missing)=>{
      active?.stop();document.documentElement.dataset.ui=ui;
      const readiness=structuredClone(ready);
      if(missing) readiness[missing]={state:'needs_you',code:codes[missing]};
      const project={board:'product',name:'Fixture project',can_configure:owner,readiness,
        main:{name:'Project agent',connected:!missing,connectable:owner,seen:!missing},models:{},model_catalog:[],limits:{}};
      const model={ready:!missing,code:codes[missing]};
      if(surface==='project') await mountProject(root,{slug:'product',api:async(path,options={})=>{
        if(path.startsWith('/api/chat-settings')) {
          if(options.method==='PUT') {
            project.readiness.model={state:'done'};project.readiness.key={state:'not_needed'};
            return {front:options.body.front};
          }
          return {front:{provider:'none'},local_models:['fixture-model'],local_reachable:true,hosted:[]};
        }
        return project;
      },e,settings:true});
      else if(ui==='classic') {
        const bot={id:'bot',name:'Fixture bot',board:'product',readiness};
        active=createBots({mount:()=>root,e,viewport:null,api:async path=>path==='/api/bots'
          ? {bots:[bot],model,readiness,can_configure:owner} : {bot,messages:[],bots:[],conversations:[]}});
        await active.show('bot');
      }
      else {
        active=mount({root,route:'#/bots/bot/coworkers',me:{workspace:'fixture',actor:'user:fixture'},
          api:async path=>path==='/api/bots'?{bots:[{id:'bot',name:'Fixture bot',board:'product'}],model,readiness,can_configure:owner}:path==='/api/coworkers'?{bots:[],conversations:[]}:project});
      }
    };window.ready=true;
    </script></body></html>"""
    with pw.sync_playwright() as manager:
        browser = manager.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 844})
        page.set_default_timeout(10000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def serve(route):
            path = urlsplit(route.request.url).path
            if path == "/":
                route.fulfill(body=html, content_type="text/html")
            else:
                file = static / path.removeprefix("/app/")
                if file.is_file():
                    route.fulfill(
                        body=file.read_bytes(),
                        content_type=mimetypes.guess_type(file.name)[0] or "text/plain",
                    )
                else:
                    route.fulfill(status=404)

        page.route("**/*", serve)
        strings = {}
        try:
            page.goto("https://example.test/")
            page.wait_for_function("window.ready")
            for surface in ("project", "bot"):
                for ui in ("classic", "deck"):
                    for owner, missing in (
                        (True, "model"),
                        (True, "key"),
                        (True, "machine"),
                        (True, "trust"),
                        (True, None),
                        (False, "trust"),
                    ):
                        page.evaluate("args=>show(...args)", [surface, ui, owner, missing])
                        if surface == "bot" and ui == "deck":
                            page.wait_for_selector(".cb-detail")
                        name = (
                            f"{surface}-{ui}-{'owner' if owner else 'member'}-{missing or 'ready'}"
                        )
                        strings[name] = page.locator("#content").inner_text()
                        if not baseline:
                            checklist = page.get_by_role("region", name="Ready to start?")
                            assert checklist.is_visible()
                            assert checklist.locator("li").count() == 6
                            # Only the steps that need you are open; done, later and not-needed steps fold away.
                            open_steps = 1 if missing else 0
                            assert checklist.get_by_role("link").count() == (
                                open_steps if owner else 0
                            )
                            assert checklist.locator("ul:not(.ready-rest ul) > li").count() == (
                                open_steps
                            )
                            assert checklist.locator("details.ready-rest").count() == 1
                            assert checklist.locator("details.ready-rest li").count() == (
                                6 - open_steps
                            )
                            # Every step keeps its action link behind the fold (the optional one has none).
                            assert checklist.locator("a").count() == (5 if owner else 0)
                            if missing:
                                row = checklist.locator(f'[data-ready="{missing}"]')
                                assert row.get_attribute("data-state") == "needs_you"
                                if owner:
                                    href = row.get_by_role("link").get_attribute("href")
                                    assert href == (
                                        "#/admin"
                                        if missing in ("trust", "machine")
                                        else "#/board/product"
                                    )
                            assert checklist.evaluate("el=>el.scrollWidth<=el.clientWidth")
                            if not owner:
                                assert "Ask the project owner" in checklist.inner_text()
                        page.screenshot(path=str(output / f"{name}-{width}.png"), full_page=True)
                        if not baseline:
                            if surface == "project" and owner and missing in ("model", "key"):
                                checklist.locator(f'[data-ready="{missing}"]').get_by_role(
                                    "link"
                                ).click()
                                assert (
                                    page.locator(".workbench-project").get_attribute(
                                        "data-ready-focus"
                                    )
                                    == "chat"
                                )
                                assert page.locator('[data-chat-mode="hosted"]').is_enabled()
                                assert page.locator("[data-project-chat]").is_visible()
                                page.locator('[data-chat-mode="local"]').check()
                                page.wait_for_function(
                                    "document.querySelector('[data-ready=\"model\"]').dataset.state==='done'"
                                )
            assert not errors, errors
            (output / f"strings-{width}.json").write_text(json.dumps(strings, indent=2) + "\n")
        finally:
            browser.close()
