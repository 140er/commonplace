"""Compute phone gutters and legible aggregate counts in the real browser modules."""

import json
import mimetypes
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import pytest

STATIC = Path(__file__).resolve().parents[1] / "commonplace/web/static"


def mount_compute(page, route):
    styles = ["app", "screens", "map", "mix", "ui", "topology", "topology-detail", "deck"]
    html = "<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
    html += "".join(f'<link rel="stylesheet" href="/app/{name}.css">' for name in styles)
    html += """<div id="root"><div class="shell two" data-view="compute"><main class="center">
        <div id="phone-bar"></div><header class="head"></header><div id="content"></div>
        <div id="phone-tabs"></div></main></div></div><script type="module">
        import { createDeck, applyDeck } from '/app/deck.js';
        applyDeck();
        const graph = { nodes: ['a','b','middle'].map(key => ({key, values:{kind:'server', name:key}})),
          links: Array.from({length:6}, (_,i) => ({id:`link-${i}`, from:'a', to:'b', method:'direct'})), routes:[] };
        const api = async path => {
          if(path === '/api/topology') return graph;
          if(path === '/api/runners') return {machines:[]};
          if(path === '/api/bots') return {bots:[{id:'general',kind:'general'}]};
          if(path === '/api/boards') return {boards:[]};
          if(path === '/api/compute') return {can_manage:true,gateways:[],nodes:[],scopes:[],executions:[],capabilities:{}};
          if(path.endsWith('/discoveries')) return {discoveries:[],capabilities:{}};
          if(path.endsWith('/scripts')) return {scripts:[]};
          return {};
        };
        const ui = createDeck({root:document.querySelector('#root'),api,
          context:() => ({me:{workspace:'demo',actor:'demo',can_admin:true,role:'owner'},boards:[],needs:{},go(){}}),title(){}});
        await ui.show(ROUTE);
        await document.fonts.ready;
        window.ready = true;
        </script>""".replace("ROUTE", json.dumps(route))

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
    page.goto("http://example.test/?ui=deck" + route)
    page.wait_for_function("window.ready === true")
    page.wait_for_function("document.querySelector('link[data-workspace-style]')?.sheet")


@pytest.mark.parametrize("route", ["#/compute", "#/compute/management"])
def test_compute_phone_has_16px_gutters_and_no_horizontal_overflow(route):
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=shutil.which("google-chrome"))
        try:
            page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
            mount_compute(page, route)
            if route == "#/compute":
                page.wait_for_selector(".topo-bundle-count")
            else:
                page.wait_for_selector("[data-compute-advanced]")
                page.locator("[data-compute-advanced] > summary").click()
                page.wait_for_selector(".compute-inventory")
            metrics = page.evaluate("""() => {
              const selectors = document.querySelector('.deck-compute')
                ? ['.dw-tabs','.dc-title','.dc-summary','.dc-actions','.topo-stage']
                : ['.dw-tabs','.compute-intro','.compute-inventory'];
              return { width:innerWidth, scroll:document.documentElement.scrollWidth,
                boxes:selectors.flatMap(selector => [...document.querySelectorAll(selector)].map(el => {
                  const r=el.getBoundingClientRect(); return {selector,left:r.left,right:r.right};
                })), actions:[...document.querySelectorAll('.dc-action')].map(el => {
                  const r=el.getBoundingClientRect(); return {left:r.left,right:r.right,top:r.top};
                }) };
            }""")
            assert metrics["scroll"] == metrics["width"] == 390, metrics
            for box in metrics["boxes"]:
                assert box["left"] == pytest.approx(16, abs=0.5), box
                assert box["right"] == pytest.approx(374, abs=0.5), box
            for action in metrics["actions"]:
                assert 16 <= action["left"] < action["right"] <= 374, action
            if route == "#/compute":
                assert len({a["top"] for a in metrics["actions"]}) > 1, metrics
                counts = page.locator(".topo-bundle-count").evaluate_all("""els => els.map(el => {
                  const r=el.getBoundingClientRect(), m=el.getScreenCTM(), style=getComputedStyle(el);
                  const overlaps = other => { const b=other.getBoundingClientRect();
                    return r.left < b.right && r.right > b.left && r.top < b.bottom && r.bottom > b.top; };
                  const count={font:parseFloat(style.fontSize)*Math.hypot(m.a,m.b), collision:false};
                  for(const node of document.querySelectorAll('.topo-node')) {
                    const disc=node.querySelector('.topo-plate').getBoundingClientRect();
                    const x=Math.max(r.left,Math.min((disc.left+disc.right)/2,r.right));
                    const y=Math.max(r.top,Math.min((disc.top+disc.bottom)/2,r.bottom));
                    if(Math.hypot(x-(disc.left+disc.right)/2,y-(disc.top+disc.bottom)/2)<=disc.width/2) count.collision=true;
                  }
                  for(const label of document.querySelectorAll('.topo-label-plate')) if(overlaps(label)) count.collision=true;
                  for(const path of document.querySelectorAll('.topo-route')) {
                    const m=path.getScreenCTM();
                    for(let d=0;d<=path.getTotalLength();d+=0.5) {
                      const p=path.getPointAtLength(d), s=new DOMPoint(p.x,p.y).matrixTransform(m);
                      if(s.x>=r.left-2 && s.x<=r.right+2 && s.y>=r.top-2 && s.y<=r.bottom+2) count.collision=true;
                    }
                  }
                  return count;
                })""")
                assert len(counts) == 2, counts
                for count in counts:
                    assert count["font"] == pytest.approx(11, abs=0.01), count
                    assert not count["collision"], count
        finally:
            browser.close()
