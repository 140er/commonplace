"""Cold first-paint and revisit evidence on disposable stores (Playwright 1.65.2)."""

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit


async def measure(args):
    from playwright.async_api import async_playwright

    if args.source_root:
        sys.path.insert(0, str(args.source_root.resolve()))
    for name in list(os.environ):
        if name.startswith("COMMONPLACE_"):
            os.environ.pop(name)
    from commonplace import sessions
    from commonplace.boards.service import BoardService
    from commonplace.extensions import ToolContext
    from tests._commonplace_helpers import Hub
    from tests.test_commonplace_boards import api

    def unavailable(*_args):
        raise RuntimeError("Fixture memory worker unavailable")

    results = []
    with tempfile.TemporaryDirectory(prefix="cold-bundle-") as directory:
        hub = Hub(Path(directory), SimpleNamespace(start=unavailable))
        try:
            service = BoardService(
                ToolContext(
                    sessions.person_principal(hub.db, hub.admin, hub.workspace), hub.gateway
                )
            )
            service.board_add(slug="project", wing="shared")
            for n in range(args.cards):
                service.task_add(
                    board="project",
                    title=f"Fixture card {n}",
                    body="Exact fixture words $& $' $`",
                    status="todo" if n % 3 else "backlog",
                )
            if args.cards > 1:
                with hub.db._lock:
                    hub.db._conn.executemany(
                        "INSERT INTO events(at,workspace_id,kind,subject) VALUES (?,?,?,?)",
                        [
                            ("2026-01-01T00:00:00Z", hub.workspace, "card.updated", str(n))
                            for n in range(20000)
                        ],
                    )
                    hub.db._conn.commit()
            session = sessions.redeem_login_link(
                hub.db, sessions.create_login_link(hub.db, "owner@example.com", hub.workspace)
            )
            cookie = {"Cookie": f"{sessions.COOKIE}={session}"}
            status, roster = api(hub, cookie, "GET", "/api/bots")
            assert status == 200
            bot = next(b for b in roster["bots"] if b.get("board") == "project")
            origin = f"http://127.0.0.1:{hub.port}"
            async with async_playwright() as p:
                browser = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
                try:
                    for route, key in [
                        ("#/", ".bh-form"),
                        (f"#/bots/{bot['id']}", ".ready-to-start"),
                        ("#/memory", ".memory-search"),
                        ("#/card/1", "#card-panel .workbench-details"),
                        ("#/board/project", ".workbench-board"),
                        ("#/settings", ".settings"),
                    ]:
                        if args.route and not route.startswith(args.route):
                            continue
                        samples = []
                        for _ in range(args.samples):
                            context = await browser.new_context(
                                viewport={"width": 1280, "height": 800}
                            )
                            try:
                                await context.add_cookies(
                                    [{"name": sessions.COOKIE, "value": session, "url": origin}]
                                )
                                page = await context.new_page()
                                requests, errors = [], []
                                page.on(
                                    "request",
                                    lambda r, requests=requests: requests.append(
                                        {
                                            "at": time.time() * 1000,
                                            "path": urlsplit(r.url).path
                                            + (
                                                "?" + urlsplit(r.url).query
                                                if urlsplit(r.url).query
                                                else ""
                                            ),
                                        }
                                    ),
                                )
                                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                                await page.add_init_script(
                                    """(() => {
                                  window.boardRenders = 0;
                                  const descriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
                                  Object.defineProperty(Element.prototype, 'innerHTML', {...descriptor, set(value) {
                                    if (this.id === 'content' && String(value).includes('workbench-board')) window.boardRenders++;
                                    descriptor.set.call(this, value);
                                  }});
                                  window.keySelector = """
                                    + json.dumps(key)
                                    + """;
                                  const observer = new MutationObserver(() => {
                                    if (!document.querySelector(window.keySelector)) return;
                                    requestAnimationFrame(() => requestAnimationFrame(() => {
                                      window.keyAt ??= performance.now(); observer.disconnect();
                                    }));
                                  }); observer.observe(document, {childList:true,subtree:true});
                                })();"""
                                )
                                cdp = await context.new_cdp_session(page)
                                await cdp.send("Emulation.setCPUThrottlingRate", {"rate": 4})
                                await cdp.send("Network.enable")
                                await cdp.send(
                                    "Network.emulateNetworkConditions",
                                    {
                                        "offline": False,
                                        "latency": 100,
                                        "downloadThroughput": 1250000,
                                        "uploadThroughput": 1250000,
                                    },
                                )
                                await page.goto(
                                    origin + "/app/" + route, wait_until="domcontentloaded"
                                )
                                await page.wait_for_function(
                                    "() => window.keyAt > 0", timeout=30000
                                )
                                timing = await page.evaluate(
                                    "({ms:keyAt,at:performance.timeOrigin+keyAt})"
                                )
                                before = [r["path"] for r in requests if r["at"] <= timing["at"]]
                                resources = await page.evaluate(
                                    "() => performance.getEntriesByType('resource').map(r => ({path:new URL(r.name).pathname,ms:r.duration,bytes:r.encodedBodySize}))"
                                )
                                sample = {
                                    "resources": resources,
                                    "ms": round(timing["ms"], 1),
                                    "requests": len(before),
                                    "paths": before,
                                    "errors": errors.copy(),
                                }
                                assert not errors, errors
                                if route.startswith("#/board/"):
                                    await page.evaluate(
                                        "window.originalBoardCard = document.querySelector('.workbench-board [data-card]')"
                                    )
                                    await page.evaluate("location.hash = '#/memory'")
                                    await page.wait_for_selector(".memory-search")
                                    start = time.monotonic()
                                    await page.evaluate("location.hash = '#/board/project'")
                                    await page.wait_for_selector(".workbench-board")
                                    sample["revisit_ms"] = round(
                                        (time.monotonic() - start) * 1000, 1
                                    )
                                    sample["board_renders"] = await page.evaluate(
                                        "window.boardRenders"
                                    )
                                    sample["same_card"] = await page.evaluate(
                                        "window.originalBoardCard === document.querySelector('.workbench-board [data-card]')"
                                    )
                                    await page.goto("about:blank")
                                    await page.goto(
                                        origin + "/app/#/board", wait_until="domcontentloaded"
                                    )
                                    await page.wait_for_selector(
                                        ".workbench-board, #content .queue", timeout=30000
                                    )
                                    sample["bare_board_hash"] = await page.evaluate("location.hash")
                                await page.wait_for_load_state("networkidle", timeout=15000)
                                samples.append(sample)
                            finally:
                                await context.close()
                        result = {
                            "route": route,
                            "cards": args.cards,
                            "median_ms": round(statistics.median(s["ms"] for s in samples), 1),
                            "median_requests": statistics.median(s["requests"] for s in samples),
                            "samples": samples,
                        }
                        results.append(result)
                        args.output.write_text(json.dumps(results, indent=2) + "\n")
                        print(
                            f"{route}: {result['median_ms']} ms, {result['median_requests']} requests",
                            flush=True,
                        )
                    if args.recovery:
                        recovery = []
                        for failure in (429, 503, "network"):
                            context = await browser.new_context()
                            try:
                                await context.add_cookies(
                                    [{"name": sessions.COOKIE, "value": session, "url": origin}]
                                )
                                page = await context.new_page()

                                async def refuse(route, _request, failure=failure):
                                    if failure == "network":
                                        await route.abort()
                                    else:
                                        await route.fulfill(status=failure, body="Unavailable")

                                pattern = "**/app/build/entry-settings.*.js"
                                await page.route(pattern, refuse)
                                await page.goto(
                                    origin + "/app/#/settings", wait_until="domcontentloaded"
                                )
                                await page.wait_for_selector(
                                    "[data-module-retry]:visible", timeout=10000
                                )
                                text = await page.locator("#root").inner_text()
                                assert "Could not reach Commonplace, retrying..." in text
                                assert "Sign in" not in text
                                await page.unroute(pattern, refuse)
                                await page.locator("[data-module-retry]").click()
                                await page.wait_for_selector(".settings", timeout=20000)
                                await page.wait_for_load_state("networkidle", timeout=15000)
                                recovery.append({"failure": failure, "recovered": True})
                            finally:
                                await context.close()
                        args.output.with_suffix(".recovery.json").write_text(
                            json.dumps(recovery, indent=2) + "\n"
                        )
                finally:
                    await browser.close()
        finally:
            hub.close()
    if args.check:
        for result in results:
            assert result["median_requests"] < 40, result
            assert result["median_ms"] < 2500, result
            if result["route"].startswith("#/board/"):
                for sample in result["samples"]:
                    assert sample["bare_board_hash"] == "#/board/project", sample
                    assert sample["same_card"] and sample["board_renders"] == 1, sample


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--route")
    parser.add_argument("--cards", type=int, default=1)
    parser.add_argument("--samples", type=int, default=3)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--recovery", action="store_true")
    asyncio.run(measure(parser.parse_args()))
