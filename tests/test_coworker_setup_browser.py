"""Setup and recovery captures use production controllers with synthetic API data."""

import json
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest

STATIC = Path(__file__).resolve().parents[1] / "commonplace/web/static"


@pytest.mark.parametrize("width", [1280, 390])
def test_coworker_setup_next_actions(tmp_path, width):
    playwright = pytest.importorskip("playwright.sync_api")
    static = Path(os.environ.get("COWORKER_SETUP_STATIC", STATIC))
    baseline = os.environ.get("COWORKER_SETUP_BASELINE") == "1"
    output = Path(os.environ.get("COWORKER_SETUP_CAPTURES", tmp_path))
    output.mkdir(parents=True, exist_ok=True)
    html = """<!doctype html><html data-ui="deck"><head>
      <meta name="viewport" content="width=device-width,initial-scale=1">
      <link rel="stylesheet" href="/app/app.css"><link rel="stylesheet" href="/app/bots.css">
      <link rel="stylesheet" href="/app/deck.css"><link rel="stylesheet" href="/app/coworkers.css">
      <link rel="stylesheet" href="/app/deck-coworkers.css">
      </head><body><main id="content"></main><script type="module">
      import {createCoworkers,coworkerJobHTML} from '/app/coworkers.js';
      import {sheetHTML} from '/app/bots.js';
      import {mount} from '/app/deck-coworkers.js';
      import {escape as e} from '/app/api.js';
      const root=document.querySelector('#content');let active;
      const directory={bots:[],conversations:[],members:[],user_id:'owner',can_manage_bots:true};
      const api=async(path,options={})=>{
        if(options.method==='POST' && path.endsWith('/bots')) {
          const bot={id:'fixture-bot',...options.body};directory.bots.push(bot);return {bot};
        }
        return path==='/api/bots'?{bots:[]}:directory;
      };
      window.conversations=()=>{active?.stop();active=createCoworkers({mount:()=>root,api,current:()=>({workspace:'fixture',actor:'user:owner'}),boards:()=>[{slug:'product',name:'Fixture project'}]});active.show();};
      window.empty=()=>{active?.stop();active=mount({root,route:'#/bots/-/coworkers',api,me:{workspace:'fixture',actor:'user:owner'}});};
      window.personal=()=>{active?.stop();root.innerHTML=sheetHTML({kind:'new'},{},e);};
      window.recovery=()=>{active?.stop();root.innerHTML='<h1>Response jobs</h1><ul>'+[
        ['needs_configuration','needs_configuration'],['runner_not_configured','done'],['provider_unavailable','failed']
      ].map(([code,state],index)=>coworkerJobHTML({id:index,conversation_id:'room',message_id:1,code,state},[],false,{},'product')).join('')+'</ul>';};
      conversations();window.ready=true;
      </script></body></html>"""
    with playwright.sync_playwright() as manager:
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

        def capture(name):
            strings[name] = page.locator("#content").inner_text()
            page.screenshot(path=str(output / f"{name}-{width}.png"), full_page=True)

        try:
            page.goto("https://example.test/")
            page.wait_for_function("window.ready")
            page.get_by_role("button", name="New", exact=True).click()
            capture("dm-prerequisite")
            if not baseline:
                page.locator('.cw-editor [data-cw-action="new-bot"]').click()
            else:
                page.locator('.cw-sidebar [data-cw-action="new-bot"]').click()
            capture("conversation-create")
            if not baseline:
                name = page.get_by_label("Name", exact=True)
                name.fill("Bad/Name")
                assert "letters" in name.evaluate("input=>input.validationMessage")
                name.fill("Research Helper")
                page.get_by_label("How to work").fill("Keep exact words")
                page.locator('[data-cw-form="bot"] button.primary').click()
                page.get_by_text("Research Helper", exact=True).wait_for()
                page.locator('[data-cw-action="start-dm"]').click()
                assert page.locator('[name="bot_ids"]:checked').count() == 1
            page.evaluate("personal()")
            capture("personal-create")
            if not baseline:
                assert page.get_by_role("heading", name="Create bot").is_visible()
                assert page.get_by_label("Name", exact=True).get_attribute("maxlength") == "60"
            page.evaluate("empty()")
            page.wait_for_selector(".cb-empty")
            capture("project-roster")
            if not baseline:
                assert "No bots on this project yet." in strings["project-roster"]
                assert page.get_by_role("link", name="Add a bot").get_attribute("href") == (
                    "#/bots/-/coworkers/conversations"
                )
            page.evaluate("recovery()")
            capture("recovery")
            if not baseline:
                assert "Needs a model: choose one in Project settings" in strings["recovery"]
                assert (
                    page.get_by_role("link", name="Project settings").get_attribute("data-board")
                    == "product"
                )
                assert (
                    page.get_by_role("link", name="Add machine").get_attribute("href") == "#/admin"
                )
                assert "provider could not be reached" in strings["recovery"]
                assert page.get_by_role("link", name="Try again in the conversation").count() == 1
            assert not errors, errors
            (output / f"strings-{width}.json").write_text(json.dumps(strings, indent=2) + "\n")
        finally:
            browser.close()
