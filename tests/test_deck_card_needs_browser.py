"""Real shell contrast and scroll regressions for the opt-in Deck panels."""

import json
import mimetypes
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import pytest

STATIC = Path(__file__).resolve().parents[1] / "commonplace/web/static"
CARD = {
    "number": 1,
    "version": 1,
    "board": "work",
    "title": "Review the release checklist",
    "body": "Check the proposed change and record a decision.\n" * 12,
    "status": "todo",
    "priority": "normal",
    "need": "idle",
    "events": [],
    "final": False,
    "can_pocket": True,
    "can_prioritize": True,
    "pocket": [{"id": 1, "text": "Keep the release evidence with this card."}],
    "updated_at": "2026-01-01T12:00:00Z",
    "actions": [],
}
BOARD = {"slug": "work", "name": "Release", "wing": "work", "cards": 1}


def open_screen(browser, screen, width, *, look="deck", css=None, populated=False):
    """Serve the real shell and modules with deterministic, local API responses."""
    context = browser.new_context(
        viewport={"width": width, "height": 844 if width == 390 else 800},
        device_scale_factor=2,
    )
    context.add_init_script("""{
      const OriginalDate=Date, now=Date.parse('2026-01-01T12:00:00Z');
      globalThis.Date=class extends OriginalDate {
        constructor(...args){super(...(args.length?args:[now]));} static now(){return now;}
      };
    }""")
    page = context.new_page()
    needs = {"count": 0}
    if populated:
        needs = {"count": 1, "done_review": [{**CARD, "status": "review"}]}
    responses = {
        "/api/me": {
            "actor": "user:owner",
            "name": "Owner",
            "workspace": "demo",
            "can_admin": True,
            "people": {},
            "role": "owner",
        },
        "/api/boards": {"boards": [BOARD]},
        "/api/boards/work/cards": {"cards": [CARD], "children": [], "linked": []},
        "/api/needs-me": needs,
        "/api/bots": {"bots": []},
        "/api/rules/count": {"waiting": 0},
        "/api/cards/1": CARD,
        "/api/cards/1/runs": {"runs": []},
        "/api/holds": {"holds": []},
        "/api/events": {"events": [], "seq": 0, "epoch": "test"},
    }

    def serve(route):
        path = urlsplit(route.request.url).path
        if path.startswith("/api/"):
            route.fulfill(body=json.dumps(responses.get(path, {})), content_type="application/json")
        else:
            name = path.removeprefix("/app/") or "index.html"
            file = STATIC / name
            if css is not None and name == "deck.css":
                route.fulfill(body=css, content_type="text/css")
            elif file.is_file():
                route.fulfill(
                    body=file.read_bytes(),
                    content_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                )
            else:
                route.fulfill(status=404, body="")

    page.route("**/*", serve)
    page.goto(f"http://example.test/app/?ui={look}#/board/work")
    page.wait_for_selector(".workbench-board .card")
    board_pills = pill_styles(page, ".workbench-board")
    if screen == "card":
        page.locator('.card[data-card="1"]').click()
        page.wait_for_selector("#pocket-text")
    elif screen == "needs":
        page.evaluate("location.hash='#/needs'")
        page.wait_for_selector(".needs-me")
    page.evaluate("document.fonts.ready")
    page.evaluate("styles => { window.boardPills = styles; }", board_pills)
    return context, page


def pill_styles(page, selector):
    return page.locator(selector).evaluate("""root => {
      const properties=['backgroundColor','color','fontSize','fontWeight','minHeight','borderRadius'];
      const style=el=>Object.fromEntries(properties.map(key=>[key,getComputedStyle(el)[key]]));
      return {radius:getComputedStyle(root.querySelector('.seg')).borderRadius,
        selected:style(root.querySelector('.seg [aria-pressed=true]')),
        idle:style(root.querySelector('.seg [aria-pressed=false]'))};
    }""")


CONTRAST = r"""selector => {
  const root=document.querySelector(selector);
  const rgba=s => s.match(/[\d.]+/g).map(Number);
  const over=(a,b) => a.slice(0,3).map((c,i)=>c*(a[3]??1)+b[i]*(1-(a[3]??1)));
  function background(el) {
    const chain=[];
    for(let n=el;n;n=n.parentElement) chain.unshift(n);
    return chain.reduce((bg,n)=>over(rgba(getComputedStyle(n).backgroundColor),bg),[255,255,255]);
  }
  const luminance=rgb=>rgb.map(c=>{c/=255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4;})
    .reduce((sum,c,i)=>sum+c*[.2126,.7152,.0722][i],0);
  const rows=[];
  for(const el of [root,...root.querySelectorAll('*')]) {
    if(el.closest('.sr,option,[hidden]') || !el.checkVisibility({checkVisibilityCSS:true,checkOpacity:true})) continue;
    const own=[...el.childNodes].filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join('').trim();
    const control=el.matches('input,textarea,select');
    let text=control?(el.value || el.placeholder || el.selectedOptions?.[0]?.textContent):own;
    if(!text) continue;
    const placeholder=control&&!el.value&&el.placeholder;
    const style=getComputedStyle(el,placeholder?'::placeholder':null);
    const bg=background(el), fg=over(rgba(style.color),bg);
    const opacity=Number(style.opacity);
    const ink=fg.map((c,i)=>c*opacity+bg[i]*(1-opacity));
    const a=luminance(ink),b=luminance(bg),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    rows.push({element:el.tagName.toLowerCase(),class:el.className,text:text.slice(0,90),
      placeholder:Boolean(placeholder),foreground:ink,background:bg,ratio});
  }
  return rows;
}"""


def assert_contrast(page, selector):
    pairs = page.evaluate(CONTRAST, selector)
    assert pairs
    failures = [pair for pair in pairs if pair["ratio"] < 4.5]
    assert not failures, f"Low contrast text/background pairs: {failures}"
    return pairs


@pytest.fixture
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as manager:
        browser = manager.chromium.launch(executable_path=shutil.which("google-chrome"))
        yield browser
        browser.close()


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("screen", ["card", "needs"])
def test_deck_panels_have_readable_surfaces_and_contained_fields(browser, screen, width):
    context, page = open_screen(browser, screen, width)
    try:
        selector = ".workbench-details" if screen == "card" else ".needs-me"
        assert_contrast(page, selector)
        assert (
            page.locator(selector).evaluate("el=>getComputedStyle(el).backgroundColor")
            == "rgb(17, 17, 17)"
        ), "Classic light surface present in Deck"
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
        if screen == "card":
            assert page.locator("#pocket-text").evaluate(
                "el=>({bg:getComputedStyle(el).backgroundColor,radius:getComputedStyle(el).borderRadius})"
            ) == {"bg": "rgb(30, 30, 30)", "radius": "12px"}
            for target in ["#pocket-text", "[data-details-description]", ".activity"]:
                page.locator(target).scroll_into_view_if_needed()
            page.locator("#pocket-text").scroll_into_view_if_needed()
            geometry = page.locator("#pocket-text").evaluate("""el=>{
              const r=el.getBoundingClientRect(),body=el.closest('.ctx-body').getBoundingClientRect();
              const footer=document.querySelector('.workbench-details .ctx-foot').getBoundingClientRect();
              return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,bodyTop:body.top,
                bodyBottom:body.bottom,footerTop:footer.top,width:innerWidth,
                overflow:el.closest('.ctx-body').scrollWidth-el.closest('.ctx-body').clientWidth};
            }""")
            assert geometry["top"] >= geometry["bodyTop"]
            assert geometry["bottom"] <= geometry["bodyBottom"] <= geometry["footerTop"]
            assert geometry["overflow"] == 0
            assert page.locator("#pocket-text").evaluate("""el=>{
              const r=el.getBoundingClientRect();
              return document.elementFromPoint((r.left+r.right)/2,(r.top+r.bottom)/2)===el;
            }"""), "Pocket field is covered by another element"
            if width == 390:
                assert geometry["left"] == 16 and geometry["right"] <= width - 16
        else:
            box = page.locator(selector).bounding_box()
            if width == 390:
                assert box["x"] == 16 and box["width"] == width - 32
            assert (
                page.locator(selector).evaluate("el=>getComputedStyle(el).borderRadius") == "20px"
            )
            assert pill_styles(page, selector) == page.evaluate("window.boardPills")
            # Both screens use the board's seg control, with the same selected inversion.
            for key in ["approvals", "decisions", "reviews", "all"]:
                page.locator(f'[data-needs-filter="{key}"]').click()
                assert (
                    page.locator(f'[data-needs-filter="{key}"]').get_attribute("aria-pressed")
                    == "true"
                )
                assert_contrast(page, selector)
    finally:
        context.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_deck_populated_needs_list_is_readable(browser, width):
    context, page = open_screen(browser, "needs", width, populated=True)
    try:
        page.wait_for_selector(".needs-me .row-item")
        assert_contrast(page, ".needs-me")
        if width == 1280:
            assert_contrast(page, ".needs-detail")
    finally:
        context.close()
