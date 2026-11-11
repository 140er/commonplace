// Script of tests/topology_harness.html: the real map over a scripted API. See that file.
import { createTopologyMap } from "/commonplace/web/static/topology-map.js";
import { renderRail, renderPhoneBar, renderPhoneTabs } from "/commonplace/web/static/rail.js";
import { fixture, largeFixture } from "/tests/topology_fixture.mjs";

const params = new URLSearchParams(location.search);
const scene = params.get("scene") || "map";
const steps = Number(params.get("steps") || 99);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const board = { slug: "atlas", name: "Atlas", agents_working: 1 };
document.querySelector("#board-nav").outerHTML = renderRail({ workspace: "Northwind", user: "Maya", view: "map", needs: 2, can_admin: true }).replace('<nav class="rail"', '<nav class="rail" id="board-nav"');
document.querySelector("#phone-bar").innerHTML = renderPhoneBar({ board, needs: 2, can_admin: true });
document.querySelector("#phone-tabs").innerHTML = renderPhoneTabs({ view: "map" });

const EMPTY = { graph: { wing: "topology", nodes: [], links: [], routes: [] }, status: { nodes: {}, links: {} } };
let payload = scene === "empty" ? EMPTY : scene === "large" ? largeFixture(300) : fixture();
const workspace = `harness-${scene}`;
const memoryKey = `commonplace.topology.${workspace}`;
localStorage.removeItem(memoryKey);
const content = document.querySelector("#content");
const open = () => { const map = createTopologyMap({ mount: () => content, api: async () => structuredClone(payload), workspace: () => workspace, interval: 400 }); map.show(); return map; };
let map = open();
const ready = async (selector) => { for (let i = 0; i < 100 && !content.querySelector(selector); i++) await sleep(20); return content.querySelector(selector); };

if (scene === "route") {
  const select = await ready('[data-topo="route"] option[value="orchard.example"]').then((option) => option.parentElement);
  select.value = "orchard.example";
  select.dispatchEvent(new Event("change", { bubbles: true }));
}

if (scene === "walk") {
  const list = document.querySelector("#walk");
  list.hidden = false;
  let failed = false, step = 0;
  const check = (label, pass, detail = "") => { failed ||= !pass; const item = document.createElement("li"); item.className = pass ? "ok" : "fail"; item.textContent = `${pass ? "ok" : "FAIL"}: ${label}${detail ? ` (${detail})` : ""}`; list.append(item); };
  const more = () => (step += 1) <= steps;
  const place = (key) => /translate\((\S+) (\S+)\)/.exec(content.querySelector(`[data-key="${key}"]`).getAttribute("transform")).slice(1).map(Number);
  const view = () => { const [, x, y, k] = /translate\((\S+) (\S+)\) scale\((\S+)\)/.exec(content.querySelector('[data-topo="world"]').getAttribute("transform")).map(Number); return { x, y, k }; };
  const pointer = (target, type, x, y) => target.dispatchEvent(new PointerEvent(type, { pointerId: 1, button: 0, clientX: x, clientY: y, bubbles: true, cancelable: true }));
  const stage = () => content.querySelector('[data-topo="stage"]');
  await ready('[data-key="maple"]');
  check("the fixture is drawn: 14 devices, 13 links", content.querySelectorAll("[data-key]").length === 14 && content.querySelectorAll("[data-link]").length === 13, `${content.querySelectorAll("[data-key]").length} devices, ${content.querySelectorAll("[data-link]").length} links`);
  let dropped = null;
  if (more()) {
    const before = place("maple"), node = content.querySelector('[data-key="maple"]'), box = node.querySelector(".topo-plate").getBoundingClientRect();
    const x = box.left + box.width / 2, y = box.top + box.height / 2;
    pointer(node, "pointerdown", x, y);
    pointer(stage(), "pointermove", x + 60, y + 20);
    pointer(stage(), "pointermove", x + 150, y + 50);
    pointer(stage(), "pointerup", x + 150, y + 50);
    dropped = place("maple");
    const k = view().k, saved = JSON.parse(localStorage.getItem(memoryKey) || "{}").placed?.maple;
    check("dragging Maple moves it with the pointer", Math.abs(dropped[0] - before[0] - 150 / k) <= 1 && Math.abs(dropped[1] - before[1] - 50 / k) <= 1, `${before} to ${dropped}`);
    check("the new place is remembered for this workspace", saved?.x === dropped[0] && saved?.y === dropped[1]);
    const after = content.querySelector('[data-key="maple"] .topo-plate').getBoundingClientRect();
    const dot = content.querySelector('[data-link="gate>maple:reverse_proxy"] [data-end="to"]').getBoundingClientRect();
    check("its links follow: the dot at Maple's end stays beside Maple", Math.hypot(dot.left - after.left, dot.top - after.top) < 90 * k + 30);
  }
  if (more()) {
    const before = view(), box = stage().getBoundingClientRect();
    stage().dispatchEvent(new WheelEvent("wheel", { deltaY: -260, clientX: box.left + box.width / 2, clientY: box.top + box.height / 2, bubbles: true, cancelable: true }));
    const zoomed = view();
    check("the wheel zooms in around the pointer", zoomed.k > before.k * 1.3, `${before.k.toFixed(2)} to ${zoomed.k.toFixed(2)}`);
    pointer(stage(), "pointerdown", box.left + 8, box.bottom - 8);
    pointer(stage(), "pointermove", box.left + 48, box.bottom - 38);
    pointer(stage(), "pointerup", box.left + 48, box.bottom - 38);
    const panned = view();
    check("dragging the background pans", panned.x === zoomed.x + 40 && panned.y === zoomed.y - 30);
    stage().focus();
    stage().dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true, cancelable: true }));
    check("arrow keys pan", view().x === panned.x + 60);
  }
  if (more()) {
    payload = fixture({ gate: "down" });
    const started = performance.now();
    const title = () => content.querySelector('[data-link="nimbus-cdn>gate:reverse_proxy"] [data-end="to"] title')?.textContent;
    while (title() !== "Gate end: Down" && performance.now() - started < 5000) await sleep(50);
    check("a status change shows without a reload", title() === "Gate end: Down", `${Math.round(performance.now() - started)} ms at a 400 ms harness interval`);
    check("the changed dot is a square, not only red", !!content.querySelector('[data-link="nimbus-cdn>gate:reverse_proxy"] [data-end="to"].s-down rect'));
  }
  if (more() && dropped) {
    const kept = view();
    map.stop();
    map = open();
    await ready('[data-key="maple"]');
    check("after a reload Maple is where it was dropped", place("maple").join() === dropped.join(), place("maple").join());
    check("after a reload the view is where it was left", JSON.stringify(view()) === JSON.stringify(kept));
  }
  document.documentElement.dataset.walk = failed ? "fail" : "pass";
}
