"""Optional real-browser canvas, appearance and render-budget checks."""

import json
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


def open_stage(page):
    styles = ["app", "screens", "ui", "workbench", "deck", "deck-capy"]
    html = "<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
    html += "".join(f'<link rel="stylesheet" href="/app/{name}.css">' for name in styles)
    html += """<div id="root"><div class="shell two"><nav class="rail" id="nav"></nav>
      <main class="center"><header class="head"><h1>Home</h1></header><div id="content"></div></main>
      </div></div><script type="module">
      import {applyDeck,createDeck} from '/app/deck.js';
      applyDeck();
      const root=document.querySelector('#root');
      const me={workspace:'fixture',actor:'user:one'};
      window.ui=createDeck({root,context:()=>({me}),title(){}});
      document.querySelector('#nav').outerHTML=ui.bar();
      window.show=(route)=>{
        ui.appearance.sync(route);
        document.querySelector('h1').textContent=route==='#/'?'Home':route==='#/today'?'Today':'Settings';
        document.querySelector('#content').innerHTML=route==='#/settings'?'<div class="settings"></div>':'';
        if(route==='#/settings')ui.settings(document.querySelector('#content'));
      };
      show('#/'); window.ready=true;
      </script>"""

    def serve(request):
        path = urlsplit(request.request.url).path
        if path == "/":
            request.fulfill(body=html, content_type="text/html")
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
    page.goto("http://example.test/?ui=deck")
    page.wait_for_function("window.ready && window.ui.appearance.measurement()")


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["desktop", "phone"])
def test_stage_dims_settings_persist_and_motion_is_optional(browser, size, tmp_path):
    page = browser.new_page(
        viewport={"width": size[0], "height": size[1]},
        device_scale_factor=2,
        reduced_motion="reduce",
    )
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        open_stage(page)
        canvas = page.locator(".stage-dither")
        assert canvas.get_attribute("data-dim") == "0"
        home = page.evaluate("ui.appearance.measurement()")
        assert home["lowFraction"] >= 0.9, home
        assert home["dots"] == home["cols"] * home["rows"]
        assert canvas.evaluate("c=>getComputedStyle(c).imageRendering") == "pixelated"
        assert (
            page.locator(".deck-stage").evaluate("s=>getComputedStyle(s,'::after').display")
            == "none"
        )
        page.evaluate("show('#/today')")
        assert canvas.get_attribute("data-dim") == "0.45"
        assert (
            page.locator(".deck-stage").evaluate("s=>getComputedStyle(s,'::after').backgroundColor")
            == "rgba(8, 8, 11, 0.5)"
        )
        page.evaluate("show('#/settings')")
        page.locator('[data-scene="palace-gate"]').click()
        page.locator('[data-bayer="8"]').click()
        for name, value in [("dot", "7"), ("vignette", ".7"), ("surface", ".6")]:
            page.locator(f'[data-appearance="{name}"]').evaluate(
                "(el,v)=>{el.value=v;el.dispatchEvent(new Event('input',{bubbles:true}));}", value
            )
        assert page.evaluate("ui.appearance.measurement().scene") == "palace-gate"
        assert (
            page.evaluate(
                "getComputedStyle(document.documentElement).getPropertyValue('--surface-a')"
            )
            == "0.6"
        )
        assert page.locator("[data-appearance-preview]").evaluate(
            "c=>c.getContext('2d').getImageData(0,0,c.width,c.height).data.some(x=>x!==0)"
        )
        page.reload()
        page.wait_for_function(
            "window.ready && window.ui.appearance.measurement()?.scene==='palace-gate'"
        )
        assert page.evaluate("ui.appearance.measurement().dot") == 7
        page.set_viewport_size({"width": size[0] - 20, "height": size[1] - 20})
        page.wait_for_function("ui.appearance.measurement().cols !== " + str(home["cols"]))
        assert not errors, errors
        (tmp_path / "measurement.json").write_text(json.dumps(home, indent=2))
    finally:
        page.close()


def test_render_budget_at_1440_by_900(browser):
    page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    try:
        open_stage(page)
        metrics = page.evaluate("""() => {
          const samples=[];
          for(let i=0;i<12;i++) {
            ui.appearance.sync(i%2?'#/':'#/today');
            if(i>1)samples.push(ui.appearance.measurement().ms);
          }
          return {samples,mean:samples.reduce((a,b)=>a+b)/samples.length};
        }""")
        assert metrics["mean"] <= 25, metrics
    finally:
        page.close()
