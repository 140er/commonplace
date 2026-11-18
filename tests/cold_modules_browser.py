"""Disposable cold-load measurements: python -m tests.cold_modules_browser --output /tmp/cold.json.

Requires scripts/requirements-release.txt and Chromium. No existing service or user data is used.
"""

import argparse
import asyncio
import json
import os
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
    from tests._commonplace_helpers import Hub

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
                            await page.wait_for_selector(key, timeout=20000)
                            result = await page.evaluate("""() => ({key:performance.now(),
                              resources:performance.getEntriesByType('resource').map(r => ({
                                path:new URL(r.name).pathname+new URL(r.name).search,
                                end:r.responseEnd,ms:r.duration}))})""")
                            result.update(
                                route=route, shell=shell, errors=errors, requested=requested.copy()
                            )
                            results.append(result)
                            assert not errors, errors
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
