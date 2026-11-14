// The Look switch (Deck | Classic): where it sits in Settings, that it flips both ways without
// freezing, and that Rotate/Revoke live apart from the everyday settings. Browser-free.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const STATIC = new URL("../commonplace/web/static/", import.meta.url);
const read = (name) => readFileSync(new URL(name, STATIC), "utf8");
const deck = await import(new URL("deck.js", STATIC));
const capy = await import(new URL("deck-settings-capy.js", STATIC));
const dither = await import(new URL("deck-dither.js", STATIC));
const agents = await import(new URL("agents.js", STATIC));

const storage = (initial = {}) => {
  const data = new Map(Object.entries(initial));
  return { getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => data.set(k, String(v)), data };
};
// A document that records the order of data-ui changes, so a test can see what ran before them.
function doc(log = []) {
  const dataset = new Proxy({}, { set(t, k, v) { log.push(`set ${k}`); t[k] = v; return true; }, deleteProperty(t, k) { log.push(`delete ${k}`); delete t[k]; return true; } });
  return { documentElement: { dataset }, listeners: [], addEventListener(type, fn) { this.listeners.push([type, fn]); } };
}
function settingsBox() {
  const parts = [];
  const box = { querySelector: (q) => (parts.some(([, m]) => m.includes(`id="${q.slice(1)}"`)) ? {} : null), insertAdjacentHTML: (where, markup) => parts.push([where, markup]) };
  return { parts, content: { querySelector: (q) => (q === ".settings" ? box : null) } };
}
const choice = (look) => ({ target: { closest: (q) => (q === '[data-deck-action="look"]' ? { dataset: { look } } : null) } });

test("Look reads Deck | Classic in plain words and marks the one in use", () => {
  const on = deck.lookHTML(true), off = deck.lookHTML(false);
  for (const html of [on, off]) {
    assert.match(html, /<h2>Look<\/h2>/);
    assert.match(html, /role="group" aria-label="Look"/);
    assert.ok(html.indexOf(">Deck</button>") < html.indexOf(">Classic</button>"), "Deck, then Classic");
    assert.doesNotMatch(html, /Go back to the classic look|Try the Deck look/);
  }
  assert.match(on, /data-look="deck" aria-pressed="true">Deck/);
  assert.match(on, /data-look="classic" aria-pressed="false">Classic/);
  assert.match(off, /data-look="deck" aria-pressed="false">Deck/);
  assert.match(off, /data-look="classic" aria-pressed="true">Classic/);
});

for (const look of ["deck", "classic"]) test(`Settings puts the Look switch at the top in ${look}, not after Sign out`, () => {
  const { parts, content } = settingsBox();
  const ui = deck.createDeck({ root: {}, context: () => ({}), title() {}, screens: {}, storage: storage({ [deck.UI_KEY]: look }), doc: doc() });
  ui.settings(content); ui.settings(content);
  const looks = parts.filter(([, markup]) => markup.includes('id="look"'));
  assert.equal(looks.length, 1, "added once");
  assert.equal(looks[0][0], "afterbegin", "first in the page, never appended last");
  assert.ok(!parts.some(([where, markup]) => where === "beforeend" && markup.includes('id="look"')));
});

test("the Settings page template ends with Sign out and carries no Look markup of its own", () => {
  const app = read("app.js");
  const view = app.slice(app.indexOf("async function settingsView("), app.indexOf("async function memoryView("));
  assert.ok(view.includes('data-action="logout">Sign out</button></section></div>`'), "Sign out still closes the page");
  assert.doesNotMatch(view, /id="look"|data-deck-action/, "the switch is added by deck.js at the top");
});

test("in the Deck layout Look is the first section, ahead of every other one", () => {
  const ids = ["phone-push", "deck-connections", "deck-tools", "plan", "tool-policy", "deck-settings-nav", "look", "deck-bot-sharing", "deck-second-review", "sign-out"];
  const ordered = capy.settingsOrder(ids.map((id) => ({ id })));
  assert.equal(ordered[0].id, "look");
  assert.equal(ordered.length, ids.length);
});

test("choosing a look stores it, both ways, and choosing the current one does nothing", () => {
  const s = storage(), log = [];
  let reloads = 0;
  const ui = deck.createDeck({ root: {}, context: () => ({}), title() {}, reload: () => reloads++, screens: {}, storage: s, doc: doc(log) });
  ui.choose("classic");
  assert.deepEqual([ui.on(), s.getItem(deck.UI_KEY), reloads], [false, "classic", 1]);
  ui.choose("classic");
  assert.deepEqual([s.getItem(deck.UI_KEY), reloads], ["classic", 1], "no second reload for the look already shown");
  ui.choose("deck");
  assert.deepEqual([ui.on(), s.getItem(deck.UI_KEY), reloads], [true, "deck", 2]);
  ui.choose("deck");
  assert.equal(reloads, 2);
});

test("a tap on either button switches once; the page-wide click handler is registered once", () => {
  const s = storage({ [deck.UI_KEY]: "classic" }), d = doc();
  let reloads = 0;
  const ui = deck.createDeck({ root: {}, context: () => ({}), title() {}, reload: () => reloads++, screens: {}, storage: s, doc: d });
  assert.equal(d.listeners.length, 1, "one document listener, not one per Settings render");
  ui.settings(settingsBox().content); ui.settings(settingsBox().content);
  assert.equal(d.listeners.length, 1);
  const [, click] = d.listeners[0];
  click(choice("classic"));
  assert.equal(reloads, 0, "classic is already shown");
  click(choice("deck"));
  assert.deepEqual([ui.on(), s.getItem(deck.UI_KEY), reloads], [true, "deck", 1]);
  click(choice("classic"));
  assert.deepEqual([ui.on(), s.getItem(deck.UI_KEY), reloads], [false, "classic", 2]);
  click({ target: { closest: () => null } });
  assert.equal(reloads, 2);
});

test("switching keeps the route: the address is never touched and the current screen redraws once", () => {
  const hash = "#/settings";
  const location = { hash }; globalThis.location = location;
  const history = { replaceState() { throw new Error("the switch must not rewrite the address"); } }; globalThis.history = history;
  try {
    const drawn = [];
    const ui = deck.createDeck({ root: {}, context: () => ({}), title() {}, reload: () => drawn.push(location.hash), screens: {}, storage: storage(), doc: doc() });
    ui.choose("classic"); ui.choose("deck");
    assert.deepEqual(drawn, [hash, hash]);
    assert.equal(location.hash, hash);
  } finally { delete globalThis.location; delete globalThis.history; }
  const app = read("app.js");
  assert.match(app, /reload: \(\) => \{ if \(state\.me\) void route\(\); \}/, "the redraw is the router, on the same hash");
});

test("the stage and previews stop BEFORE data-ui changes, or their canvases lose their CSS size", () => {
  const log = [];
  const ui = deck.createDeck({ root: {}, context: () => ({}), title() {}, reload: () => log.push("route"), screens: {}, storage: storage(), doc: doc(log) });
  const stop = ui.appearance.stop;
  ui.appearance.stop = () => { log.push("stop"); stop(); };
  ui.choose("classic");
  assert.deepEqual(log, ["stop", "delete ui", "route"]);
  log.length = 0;
  ui.choose("deck");
  assert.deepEqual(log, ["stop", "set ui", "route"]);
});

test("a canvas that grows with its own bitmap cannot grow without bound", () => {
  // Classic has no CSS for the Deck canvases, so a canvas is as wide as its bitmap: every render would double it.
  const win = { innerWidth: 390, innerHeight: 844, devicePixelRatio: 2, performance: { now: () => 0 }, matchMedia: () => ({ matches: true, addEventListener() {}, removeEventListener() {} }), ResizeObserver: class { observe() {} disconnect() {} } };
  const own = { createImageData: (w, h) => { if (w * h > 20e6) throw new RangeError("Out of memory at ImageData creation"); return { data: new Uint8ClampedArray(w * h * 4) }; }, getImageData: (x, y, w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }), createRadialGradient: () => ({ addColorStop() {} }), createLinearGradient: () => ({ addColorStop() {} }) };
  // Any other drawing call is a no-op, so the real scenes paint onto nothing.
  const context = () => new Proxy({}, { get: (t, k) => k in own ? own[k] : k in t ? t[k] : () => {}, set: (t, k, v) => { t[k] = v; return true; } });
  const made = () => ({ width: 0, height: 0, getContext: context, dataset: {} });
  const canvas = made();
  canvas.getBoundingClientRect = () => ({ width: canvas.width || 100, height: canvas.height || 60 });
  canvas.parentElement = null;
  canvas.ownerDocument = { defaultView: win, createElement: () => made() };
  const stage = new dither.StageDither(canvas, { scene: "capy-cyber", dot: 4 });
  for (let i = 0; i < 12; i++) stage.render();
  assert.ok(canvas.width <= 390 * 2, `width ${canvas.width} stays inside the window`);
  assert.ok(canvas.height <= 844 * 2, `height ${canvas.height} stays inside the window`);
});

test("Rotate and Revoke sit in their own Access tokens group, away from the everyday controls", () => {
  const pat = { id: "p1", name: "my-agent", auth: "pat", client: "codex", read_wings: [], decide_grantable: true, bots_grantable: true };
  const oauth = { id: "o1", name: "signed-in", auth: "oauth", client: "claude-ai", read_wings: [] };
  const html = agents.agentsView({ connections: [pat, oauth], wings: ["shared"], decide_consent: "x", bots_consent: {} });
  const row = (id) => html.split('<div class="setting-row">').find((part) => part.includes(`data-agent-id="${id}"`)).split("</div>")[0];
  for (const id of ["p1", "o1"]) {
    const html1 = row(id), group = /<details class="agent-tokens" data-agent-tokens><summary>Access tokens<\/summary>(.*?)<\/details>/.exec(html1);
    assert.ok(group, `${id}: the token buttons are in a group`);
    assert.match(group[1], /data-action="agent-revoke"/);
    const outside = html1.replace(group[0], "");
    assert.doesNotMatch(outside, /data-action="agent-(rotate|revoke)"/, `${id}: nothing token-cutting outside the group`);
  }
  assert.match(row("p1"), /<details class="agent-tokens"[^>]*><summary>Access tokens<\/summary><button[^>]*agent-rotate[^>]*>Rotate<\/button><button[^>]*agent-revoke[^>]*>Revoke<\/button><\/details>/);
  assert.match(row("p1"), /data-action="agent-decide"|data-action="agent-bots"/, "the everyday toggles are still there");
  assert.doesNotMatch(row("o1"), /agent-rotate/);
});
