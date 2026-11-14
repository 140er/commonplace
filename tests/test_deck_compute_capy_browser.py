"""Optional Capy Compute screen checks using the real controllers and deterministic API reads."""

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


def open_compute(page):
    styles = [
        "app",
        "screens",
        "map",
        "mix",
        "ui",
        "topology",
        "topology-detail",
        "workbench",
        "deck",
        "deck-capy",
    ]
    html = "<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
    html += "".join(f'<link rel="stylesheet" href="/app/{name}.css">' for name in styles)
    html += """<div id="root"><div class="shell two" data-view="compute"><nav class="rail" id="nav"></nav>
      <main class="center"><div id="phone-bar"></div><header class="head"></header><div id="content"></div>
      <div id="phone-tabs"></div></main></div></div><script type="module">
      import {createDeck,applyDeck} from '/app/deck.js';
      applyDeck();
      const nodes=[['hub','Hub','server','Production'],['runner','Runner','pc','Agent runner'],
        ['build','Build machine','server','Builds'],['laptop','Laptop','mac','Builds'],['bench','Bench','pc','Bench']];
      const graph={nodes:nodes.map(([key,name,kind,role])=>({key,values:{kind,name,role,resources:{memory_gb:32,storage_gb:240}}})),
        links:[['hub','runner','tailscale'],['runner','build','direct'],['runner','laptop','tailscale'],['runner','bench','tailscale']]
          .map(([from,to,method],i)=>({id:'link-'+i,from,to,method})),
        routes:[{site:'workspace.example',hops:[{link:'link-0',from:'hub',to:'runner',method:'tailscale',status:'up'}]}],status:{nodes:{hub:'up',runner:'up',build:'down',laptop:'unknown',bench:'degraded'},links:{
          'link-0':{from:'up',to:'up'},'link-1':{from:'up',to:'down'},'link-2':{from:'unknown',to:'unknown'},'link-3':{from:'up',to:'degraded'}}}};
      const api=async path=>{
        if(path==='/api/topology')return graph;
        if(path.startsWith('/api/topology/node'))return {node:{found:true,fields:{resources:{value:{memory_gb:32,storage_gb:240}}}}};
        if(path==='/api/bots')return {bots:[]};
        return {};
      };
      window.ui=createDeck({root:document.querySelector('#root'),api,
        context:()=>({me:{workspace:'fixture',actor:'user:one',can_admin:true},boards:[],needs:{},go:hash=>{window.destination=hash}}),title(){}});
      document.querySelector('#nav').outerHTML=ui.bar();
      document.querySelector('#phone-bar').innerHTML=ui.top();
      document.querySelector('#phone-tabs').innerHTML=ui.tabs();
      ui.appearance.sync('#/compute');
      await ui.show('#/compute');
      document.querySelector('#phone-bar').innerHTML=ui.top();
      await document.fonts.ready;window.ready=true;
      </script>"""

    def serve(route):
        path = urlsplit(route.request.url).path
        if path == "/":
            route.fulfill(body=html, content_type="text/html")
        else:
            file = (STATIC / path.removeprefix("/app/")).resolve()
            if (
                path.startswith("/app/")
                and file.is_relative_to(STATIC.resolve())
                and file.is_file()
            ):
                route.fulfill(
                    body=file.read_bytes(),
                    content_type=mimetypes.guess_type(file.name)[0] or "application/octet-stream",
                )
            else:
                route.fulfill(status=404, body="")

    page.route("**/*", serve)
    page.goto("http://example.test/?ui=deck#/compute")
    page.wait_for_function(
        "window.ready && document.querySelector('link[data-compute-capy]')?.sheet"
    )
    page.wait_for_selector(".topo-node")


def capture(page, name):
    if folder := os.environ.get("CAPY_COMPUTE_SHOTS"):
        destination = Path(folder)
        destination.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(destination / name))


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["desktop", "phone"])
def test_compute_capy_screen(browser, size):
    page = browser.new_page(viewport={"width": size[0], "height": size[1]}, device_scale_factor=2)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.set_default_timeout(8000)
        open_compute(page)
        capture(page, "initial-desktop.png" if size[0] == 1440 else "initial-phone.png")
        assert page.evaluate("document.documentElement.scrollWidth") == size[0]
        assert page.locator(".dc-summary").evaluate("el=>el.scrollWidth <= el.clientWidth")
        for word in page.locator(".dc-summary-state").all():
            box = word.bounding_box()
            assert box["x"] >= 16 and box["x"] + box["width"] <= size[0] - 16
        assert page.evaluate("async () => (await fetch('/app/../../pyproject.toml')).status") == 404
        ring = page.locator('.dc-node[data-key="laptop"] .dc-node-status circle')
        assert ring.evaluate("el=>getComputedStyle(el).fill") == "none"
        assert (
            page.locator('.dc-node[data-key="hub"] .dc-node-status').get_attribute("data-shape")
            == "disc"
        )
        assert page.locator(".deck-stage").get_attribute("data-dimmed") == "true"
        # Sparse pairs may use a line rather than a routed path.
        live = page.locator('.topo-link[data-active="true"] :is(line,.topo-route)').first
        assert live.evaluate("el=>getComputedStyle(el).animationDuration") == "1.4s"
        assert live.evaluate("el=>getComputedStyle(el).strokeDasharray") == "4px, 6px"
        idle = page.locator('.topo-link[data-active="false"] :is(line,.topo-route)').first
        assert idle.evaluate("el=>getComputedStyle(el).animationName") == "none"
        assert all(
            display == "none"
            for display in page.locator(
                '.topo-link[data-active="false"] .dc-link-label'
            ).evaluate_all("els=>els.map(el=>getComputedStyle(el).display)")
        )
        if size[0] == 390:
            assert page.locator(".topo-detail:not([hidden])").count() == 0
            assert page.locator(".dc-node-tile").count() == 0
            stage = page.locator(".topo-stage").bounding_box()
            nav_top = page.locator("#phone-tabs").bounding_box()["y"]
            for node in page.locator(".dc-node-surface").all():
                box = node.bounding_box()
                assert box["y"] >= stage["y"] + 23
                assert box["y"] + box["height"] <= nav_top - 24
            actions = page.locator(".dc-actions").bounding_box()
            assert actions["y"] >= stage["y"] + stage["height"]
            assert page.locator("#phone-bar .dc-top-add").is_visible()
            capture(page, "compute-phone.png")
            page.locator('.topo-node[data-key="hub"]').click()
            page.get_by_role("button", name="Back to machines").wait_for()
            assert (
                page.locator(".topo-stage").evaluate("el=>getComputedStyle(el).display") == "none"
            )
            capture(page, "inspector-phone.png")
            page.get_by_role("button", name="Back to machines").click()
            assert page.locator(".topo-stage").is_visible()
            assert page.locator(".dc-action").first.bounding_box()["height"] >= 44
        else:
            panel = page.locator(".topo-detail:not([hidden])")
            assert panel.bounding_box()["width"] == pytest.approx(300)
            assert panel.get_by_text("32 GB capacity").is_visible()
            assert panel.get_by_text("Healthy", exact=True).is_visible()
            capture(page, "compute-desktop.png")
        assert live.evaluate("el=>getComputedStyle(el).strokeWidth") == "1px"
        assert page.locator(".topo-link [data-end]").count() == 0
        assert page.locator(".topo-bundle").count() == 0
        assert page.locator(".dc-map-controls button").count() == 2
        if size[0] == 1440:
            page.locator('.topo-link[data-link="link-0"]').focus()
            page.keyboard.press("Enter")
            assert page.locator('.topo-link[aria-pressed="true"]').count() == 1
            selected = page.locator(".topo-link.selected .topo-route")
            assert selected.evaluate("el=>getComputedStyle(el).strokeWidth") == "2px"
            assert selected.evaluate("el=>getComputedStyle(el).strokeDasharray") == "none"
            assert selected.evaluate("el=>getComputedStyle(el).strokeOpacity") == "1"
            assert page.locator(".dc-node.dc-link-end").count() == 2
            assert page.locator('.dc-node[aria-pressed="true"]').count() == 0
            assert (
                page.locator(".topo-detail")
                .get_by_role("heading", name="Hub to Runner")
                .is_visible()
            )
            assert page.locator(".topo-detail dt").all_text_contents() == [
                "Route",
                "State",
                "Last seen",
            ]
            capture(page, "compute-link-selected.png")
            page.locator('.dc-node[data-key="hub"]').focus()
            page.keyboard.press("Enter")
            assert page.locator(".topo-link.selected").count() == 0
            assert page.locator(".dc-node.dc-link-end").count() == 0
            assert page.locator(".topo-detail").get_by_text("Healthy", exact=True).is_visible()
        page.emulate_media(reduced_motion="reduce")
        assert live.evaluate("el=>getComputedStyle(el).animationName") == "none"
        assert page.locator(".deck-compute").get_attribute("data-compute-motion") == "reduced"
        page.locator('.dc-actions [data-compute-action="add-machine"]').click()
        assert page.evaluate("window.destination") == "#/admin"
        show_management = """async () => { await ui.show('#/compute/management'); }"""
        page.evaluate(show_management)
        page.locator("[data-compute-route]").wait_for()
        page.locator("[data-compute-route]").select_option("workspace.example")
        page.get_by_role("button", name="Show on map").click()
        assert page.evaluate("window.destination") == "#/compute"
        page.evaluate("async () => { await ui.show('#/compute'); }")
        page.wait_for_selector(".topo-link.on")
        assert not errors, errors
    finally:
        page.close()


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["desktop", "phone"])
def test_compute_all_four_states(browser, size):
    page = browser.new_page(viewport={"width": size[0], "height": size[1]}, device_scale_factor=2)
    try:
        open_compute(page)
        for key, shape, color in [
            ("hub", "disc", "rgb(34, 211, 238)"),
            ("bench", "triangle", "rgb(157, 92, 255)"),
            ("build", "diamond", "rgb(240, 112, 138)"),
            ("laptop", "ring", "rgb(133, 131, 154)"),
        ]:
            mark = page.locator(f'.dc-node[data-key="{key}"] .dc-node-status')
            assert mark.get_attribute("data-shape") == shape
            assert mark.evaluate("el=>getComputedStyle(el).color") == color
        page.evaluate(
            """async phone => {
          const {computePresentation} = await import('/app/deck-compute-scene.js');
          const states=['up','degraded','down','unknown'];
          const presentation=computePresentation(phone);
          const markup=states.map((status,i)=>presentation.node({key:status,name:status[0].toUpperCase()+status.slice(1),role:'Machine',kind:'server',status},
            {x:phone ? 96+(i%2)*198 : 270+i*300,y:phone ? 64+Math.floor(i/2)*84 : 86},{words:{up:'Up',degraded:'Degraded',down:'Down',unknown:'Unknown'}})).join('');
          const gallery=document.createElement('section');gallery.className='deck-compute';gallery.id='state-gallery';
          gallery.style.cssText='position:fixed;inset:0;z-index:999;background:var(--card);width:100%;padding:16px 0;color:var(--ink)';
          gallery.innerHTML='<h1 style="padding:0 16px">Machine states</h1><svg width="100%" height="'+(phone ? 210 : 172)+'" viewBox="0 0 '+(phone ? 390 : 1440)+' '+(phone ? 210 : 172)+'">'+markup+'</svg>';
          document.body.append(gallery);
        }""",
            size[0] == 390,
        )
        if folder := os.environ.get("CAPY_COMPUTE_SHOTS"):
            page.locator("#state-gallery").screenshot(
                path=str(
                    Path(folder)
                    / (
                        "compute-states-phone.png"
                        if size[0] == 390
                        else "compute-states-desktop.png"
                    )
                )
            )
    finally:
        page.close()
