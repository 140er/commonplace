// The Deck stage carries its final dim state on the first paint of a deep link: shell() draws it before
// any route or data read has run, so navigation() must derive the value from the address, not state.base.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

const STATIC = new URL("../commonplace/web/static/", import.meta.url);
const app = readFileSync(new URL("app.js", STATIC), "utf8");
const { createDeck, UI_KEY } = await import(new URL("deck.js", STATIC));
const rail = await import(new URL("rail.js", STATIC));

const slice = (start, end) => {
  const at = app.indexOf(start), stop = app.indexOf(end, at);
  assert.ok(at >= 0 && stop > at, `Missing slice: ${start} → ${end}`);
  return app.slice(at, stop);
};
const node = (extra = {}) => ({ innerHTML: "", hidden: true, dataset: {}, attrs: {}, children: [], setAttribute(k, v) { this.attrs[k] = v; }, querySelector: () => null, querySelectorAll: () => [], addEventListener() {}, ...extra });
// A canvas the real StageDither can draw on without a browser: every drawing call is a no-op.
const win = { innerWidth: 390, innerHeight: 844, devicePixelRatio: 1, performance: { now: () => 0 }, setInterval: () => 1, clearInterval() {}, matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }), ResizeObserver: class { observe() {} disconnect() {} } };
const context2d = () => new Proxy({}, { get: (t, k) => k === "createImageData" ? (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }) : k === "getImageData" ? (x, y, w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }) : k in t ? t[k] : () => ({ addColorStop() {} }), set: (t, k, v) => { t[k] = v; return true; } });
const makeElement = (tag) => node({ tag, width: 0, height: 0, className: "", getContext: context2d, getBoundingClientRect: () => ({ width: 0, height: 0 }), append(...kids) { this.children.push(...kids); }, remove() {}, isConnected: true, ownerDocument: doc });
const doc = { defaultView: win, createElement: makeElement, documentElement: { dataset: {}, style: { setProperty() {}, removeProperty() {} } }, addEventListener() {} };

// shell() is the first thing boot() runs after /api/me: the stage is created by this first navigation(),
// before route() has set state.base or refresh() has read a single board.
function firstPaint(hash, { home = "#/" } = {}) {
  const center = node({ prepend(stage) { this.stage = stage; stage.parentElement = this; } });
  const nodes = { "#board-nav": node(), "#phone-bar": node(), "#phone-tabs": node(), ".shell": node(), ".center": center };
  const root = { querySelector: (selector) => nodes[selector] || null, addEventListener() {} };
  const store = { getItem: (key) => key === UI_KEY ? "deck" : null };
  const screens = { today: () => 0, bots: () => 0, board: () => 0, memory: () => 0, compute: () => 0, ...(home === "#/" ? { home: () => 0 } : {}) };
  const deck = createDeck({ root, context: () => ({ me: { actor: "user:maya", workspace: "work" } }), title() {}, screens, storage: store, doc });
  const state = { me: { actor: "user:maya", workspace: "work", people: {} }, needs: { count: 0 }, boards: [], screen: 0, route: "#/", base: "#/", deck, rulesWaiting: 0, botsWaiting: 0 };
  const sandbox = { state, root, location: { hash }, ...rail, currentBoard: () => null, shellSync: null };
  runInNewContext(slice("function navigation()", "function showError"), sandbox);
  sandbox.navigation();
  return { stage: center.stage, sandbox, state, center };
}
const dimmed = (stage) => stage.attrs["data-dimmed"];

test("a deep link to Today paints the stage dimmed on the first navigation, before any route or read", () => {
  const { stage, state } = firstPaint("#/today");
  assert.equal(state.base, "#/", "route() has not run yet: the stage cannot lean on state.base");
  assert.equal(dimmed(stage), "true");
});

for (const hash of ["#/board/atlas", "#/memory", "#/compute", "#/bots", "#/bots/atlas", "#/settings", "#/needs", "#/card/5"]) {
  test(`a deep link to ${hash} paints the stage dimmed on the first navigation`, () => {
    assert.equal(dimmed(firstPaint(hash).stage), "true");
  });
}

test("Home keeps the undimmed hero on the first paint", () => {
  for (const hash of ["#/", "#/home", ""]) assert.equal(dimmed(firstPaint(hash).stage), "false", `hash ${JSON.stringify(hash)}`);
});

test("without a Home screen the bare address already shows the dimmed Today fallback", () => {
  assert.equal(dimmed(firstPaint("#/", { home: "#/today" }).stage), "true");
});

test("after the first route the stage follows state.base again", () => {
  const { stage, state, sandbox } = firstPaint("#/today");
  state.screen = 1; state.base = "#/"; sandbox.location.hash = "#/today";
  sandbox.navigation();
  assert.equal(dimmed(stage), "false", "a settled route is read from state.base, not the address");
});
