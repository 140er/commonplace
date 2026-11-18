"""Disposable cold-load measurements: python -m tests.cold_modules_browser --output /tmp/cold.json.

Requires scripts/requirements-release.txt and Chromium. No existing service or user data is used.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, help="Optional archived revision to measure")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assert-lazy", action="store_true")
    return parser.parse_args()


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
    from commonplace.web import STATIC
    from tests._commonplace_helpers import Hub

    expected_styles = re.findall(
        r'<link rel="stylesheet" href="([^"]+)"', (STATIC / "index.html").read_text()
    )

    def unavailable(*_args):
        raise RuntimeError("Fixture memory worker unavailable")

    results = []
    with tempfile.TemporaryDirectory(prefix="cold-modules-browser-") as directory:
        hub = Hub(Path(directory), SimpleNamespace(start=unavailable))
        try:
            principal = sessions.person_principal(hub.db, hub.admin, hub.workspace)
            service = BoardService(ToolContext(principal, hub.gateway))
            service.board_add(slug="project", wing="shared")
            service.task_add(board="project", title="Cold fixture card")
            session = sessions.redeem_login_link(
                hub.db, sessions.create_login_link(hub.db, "owner@example.com", hub.workspace)
            )
            origin = f"http://127.0.0.1:{hub.port}"
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
                try:
                    for route, key in [
                        ("#/settings", ".settings"),
                        ("#/board/project", ".workbench-board"),
                        ("#/bots", ".cb-bot-row"),
                        ("#/memory", ".memory-search"),
                        ("#/card/1", "#card-panel .workbench-details"),
                    ]:
                        context = await browser.new_context()
                        try:
                            await context.add_cookies(
                                [{"name": sessions.COOKIE, "value": session, "url": origin}]
                            )
                            page = await context.new_page()
                            await page.add_init_script("""(() => {
                              const observer = new MutationObserver(() => {
                                if (!document.querySelector('.shell')) return;
                                window.coldShellStyles = [...document.querySelectorAll('link[rel="stylesheet"]')]
                                  .map(link => ({path:new URL(link.href).pathname,ready:!!link.sheet}));
                                observer.disconnect();
                              });
                              observer.observe(document, {childList:true,subtree:true});
                            })();""")
                            errors = []
                            requested = []
                            page.on(
                                "request",
                                lambda request, requested=requested: requested.append(
                                    urlsplit(request.url).path
                                ),
                            )
                            page.on(
                                "pageerror", lambda error, errors=errors: errors.append(str(error))
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
                            await page.goto(origin + "/app/" + route, wait_until="domcontentloaded")
                            await page.wait_for_selector(".shell", timeout=20000)
                            shell = await page.evaluate("performance.now()")
                            try:
                                await page.wait_for_selector(key, timeout=20000)
                            except Exception:
                                args.output.parent.mkdir(parents=True, exist_ok=True)
                                args.output.with_suffix(".failure.html").write_text(
                                    await page.content()
                                )
                                await page.screenshot(
                                    path=str(args.output.with_suffix(".failure.png"))
                                )
                                raise
                            result = await page.evaluate("""() => ({key:performance.now(),
                              shellStyles:window.coldShellStyles,
                              styles:[...document.querySelectorAll('link[rel="stylesheet"]')]
                                .map(link => ({path:new URL(link.href).pathname,ready:!!link.sheet})),
                              resources:performance.getEntriesByType('resource').map(r => ({
                                path:new URL(r.name).pathname+new URL(r.name).search,
                                end:r.responseEnd,ms:r.duration}))})""")
                            result.update(
                                route=route, shell=shell, errors=errors, requested=requested.copy()
                            )
                            results.append(result)
                            assert not errors, errors
                            for styles in (result["shellStyles"], result["styles"]):
                                # Route controllers can add their own extra styles; the document's
                                # original links must already be ready and keep their cascade order.
                                pinned = [s for s in styles if s["path"] in expected_styles]
                                assert [s["path"] for s in pinned] == expected_styles, styles
                                assert all(s["ready"] for s in pinned), styles
                            if args.assert_lazy:
                                unused = {
                                    "admin.js",
                                    "instance-admin.js",
                                    "console.js",
                                    "invites.js",
                                    "memory.js",
                                    "chat.js",
                                    "details.js",
                                    "topology-map.js",
                                }
                                if route == "#/memory":
                                    unused.remove("memory.js")
                                if route == "#/card/1":
                                    unused.remove("details.js")
                                if route == "#/settings":
                                    unused.add("bots.js")
                                fetched = {path.rsplit("/", 1)[-1] for path in requested}
                                assert not unused & fetched, (route, sorted(unused & fetched))
                                # Revalidation must keep cached executable bodies usable on reload.
                                await page.reload(wait_until="domcontentloaded")
                                await page.wait_for_selector(key, timeout=20000)
                                assert not errors, errors
                        finally:
                            await context.close()
                    if args.assert_lazy:
                        # Fail a required module before app.js can run, then recover through Retry.
                        recovery = []
                        for failure in (429, 503, "network"):
                            context = await browser.new_context()
                            try:
                                await context.add_cookies(
                                    [{"name": sessions.COOKIE, "value": session, "url": origin}]
                                )
                                page = await context.new_page()
                                reads = []
                                page.on(
                                    "request",
                                    lambda req, reads=reads: reads.append(urlsplit(req.url).path),
                                )

                                async def refuse(route, _request, failure=failure):
                                    if failure == "network":
                                        await route.abort()
                                    else:
                                        await route.fulfill(status=failure, body="Unavailable")

                                await page.route("**/app/api.js", refuse)
                                await page.goto(
                                    origin + "/app/#/settings", wait_until="domcontentloaded"
                                )
                                await page.wait_for_selector("[data-module-retry]:visible")
                                text = await page.locator("#root").inner_text()
                                assert "Could not reach Commonplace, retrying..." in text
                                assert "Sign in" not in text and "Email a sign-in link" not in text
                                assert not any(p.startswith("/api/") for p in reads), reads
                                await page.unroute("**/app/api.js", refuse)
                                await page.locator("[data-module-retry]").click()
                                await page.wait_for_selector(".settings", timeout=20000)
                                recovery.append({"failure": failure, "retry_recovered": True})
                            finally:
                                await context.close()
                        args.output.with_suffix(".recovery.json").write_text(
                            json.dumps(recovery, indent=2) + "\n"
                        )
                finally:
                    await browser.close()
        finally:
            hub.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, indent=2) + "\n")
    for result in results:
        modules = sum(
            r["path"].endswith(".js") and r["end"] <= result["shell"] for r in result["resources"]
        )
        print(
            f"{result['route']}: shell={result['shell']:.0f}ms key={result['key']:.0f}ms modules={modules}"
        )


if __name__ == "__main__":
    asyncio.run(measure(arguments()))
