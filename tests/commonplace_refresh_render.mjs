import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";
import { createNeedLedger } from "../commonplace/web/static/deck-home-today.js";

const read = (name) => readFile(new URL(`../commonplace/web/static/${name}`, import.meta.url), "utf8");
const app = await read("app.js");
function slice(start, end) {
  const at = app.indexOf(start), stop = app.indexOf(end, at);
  assert.ok(at >= 0 && stop > at, `Missing slice: ${start} → ${end}`);
  return app.slice(at, stop);
}
const url = (source) => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const apiUrl = url(await read("api.js"));
const { planView } = await import(url((await read("plan.js")).replace('"./api.js"', `"${apiUrl}"`)));
const which = process.argv[2];
const consoleCase = which === "console_background_first";
const base = consoleCase ? "#/console" : "#/settings";
const state = { me: { actor: "user:maya", can_admin: true }, base, route: base, screen: 1, refreshRequest: 0 };
let html = "Loading…", renders = 0, navigationCalls = 0, holdSettings = false;
const content = {
  children: [{}],
  get innerHTML() { return html; },
  set innerHTML(value) { html = value; if (value.includes('class="settings"')) renders++; },
  querySelector: (selector) => selector === ".settings" && html.includes('class="settings"') ? {} : null,
};
const reads = [], settingsReads = [];
let needsSnapshot = { count: 0, waiting: 0 };
const free = { enabled: true, plan: "free", storage: { used: 12, limit: 1024 }, transcription: { used_today: 0, limit: 600 }, sync: { interval_seconds: 900 } };
const values = {
  "/api/connections": {}, "/api/webhooks": { webhooks: [] }, "/api/plan": free,
  "/api/phone-push": {}, "/api/chat-settings": { front: { enabled: false }, usage: { used_today: 0, user_used_today: 0 } },
  "/api/settings/orchestration": { enabled: false },
};
const context = {
  deckNeedsLedger: createNeedLedger(),
  state, root: { querySelector: (selector) => selector === "#content" ? content : null },
  api(path) {
    if (["/api/boards", "/api/needs-me", "/api/rules/count"].includes(path)) {
      return new Promise((resolve) => reads.push({ path, resolve }));
    }
    // The shell reads the bots roster with the shared reads to fill the Bots badge.
    if (path === "/api/bots") return Promise.resolve({ bots: [{ waiting: 2 }, { waiting: 1 }] });
    assert.ok(Object.hasOwn(values, path), `Unexpected API read: ${path}`);
    if (holdSettings) return new Promise((resolve) => settingsReads.push(() => resolve(values[path])));
    return Promise.resolve(values[path]);
  },
  remember: () => ({}), capture: () => () => {}, restore() {},
  navigation() { navigationCalls++; },
  mountToolPolicy() {},
  waitingTotal: (bots) => bots.reduce((total, bot) => total + bot.waiting, 0),
  title() {}, message: (text) => text, e: (value) => String(value ?? ""), planView,
  agentsView: () => "", phonePushView: () => "", voice: { settings: () => "" },
  mountWorkspaceOrchestration() {}, mountAgentSchedules() {},
  consoleScreen() { renders++; content.innerHTML = "Console controls"; },
  showError: (error) => assert.equal(error, ""),
};
// Use the same production-function slicing as the other shell integration cases.
// refresh() closes over the real queue ledger and roster helper in the complete shell.
runInNewContext(slice("function rememberDeckBots(", "function setBotsWaiting(") +
  slice("async function settingsView(", "async function memoryView(") +
  slice("async function refresh(", "function decideStart("), context);
function batch() {
  const pending = reads.splice(0);
  assert.equal(pending.length, 3, "Each refresh must start its three shared reads");
  const boards = [{ slug: `project-${state.refreshRequest}` }];
  return () => pending.forEach(({ path, resolve }) => resolve(path === "/api/boards" ? { boards } : path === "/api/needs-me" ? needsSnapshot : { count: 0, waiting: 0 }));
}
function completeView() {
  assert.equal(renders, 1, `First complete view never rendered: ${html}`);
  assert.doesNotMatch(html, /Loading/);
  assert.equal(state.botsWaiting, 3, "The roster read fills the Bots badge");
  assert.equal(state.deckRoster.actor, state.me.actor);
  assert.equal(state.deckRoster.bots.length, 2, "Refresh retains the authorized roster for the palette");
  if (consoleCase) assert.equal(html, "Console controls");
  else {
    assert.match(html, /<h2>Free plan<\/h2>/);
    assert.match(html, /data-action="logout">Sign out/);
    assert.match(html, /data-form="webhook"/);
  }
}
function navigateAway() {
  // Routing advances this epoch before another screen can render.
  state.screen++; state.base = state.route = "#/boards";
  content.innerHTML = "Another page";
}
const settled = { number: 7, version: 1 };
if (which === "settled_needs") {
  needsSnapshot = { count: 1, approvals: [settled] };
  context.deckNeedsLedger.settle(settled, state.me);
}
const foreground = context.refresh(), finishForeground = batch();
if (["foreground_only", "settled_background", "settled_needs"].includes(which)) {
  finishForeground(); await foreground; completeView();
  if (which === "settled_needs") {
    assert.equal(state.needs.count, 0, "A stale read cannot restore the settled count");
    assert.equal(state.needs.approvals.length, 0);
    needsSnapshot = { count: 1, approvals: [{ ...settled, version: 2 }] };
    const changed = context.refresh({ background: true }); batch()(); await changed;
    assert.equal(state.needs.count, 1, "A newer version is still actionable");
    state.me = { ...state.me, workspace: "another" };
    needsSnapshot = { count: 1, approvals: [settled] };
    const switched = context.refresh({ background: true }); batch()(); await switched;
    assert.equal(state.needs.count, 1, "Receipts cannot cross workspace boundaries");
  }
  if (which === "settled_background") {
    const saved = html;
    const background = context.refresh({ background: true, events: [{ kind: "board.created" }] });
    batch()(); await background;
    assert.equal(html, saved); completeView();
  }
} else if (["navigate_during_settings", "background_during_settings"].includes(which)) {
  holdSettings = true;
  finishForeground();
  // Let the shared reads reach the real settingsView's deferred reads.
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(settingsReads.length, 6);
  if (which === "navigate_during_settings") navigateAway();
  else {
    const background = context.refresh({ background: true, events: [{ kind: "board.created" }] });
    batch()(); await background;
    assert.equal(settingsReads.length, 6, "Background refresh must not start a duplicate Settings render");
  }
  settingsReads.splice(0).forEach((resolve) => resolve());
  await foreground;
  if (which === "navigate_during_settings") { assert.equal(renders, 0); assert.equal(html, "Another page"); }
  else completeView();
} else {
  const background = context.refresh({ background: true, events: [{ kind: "board.created" }] });
  const finishBackground = batch();
  const leaving = which.startsWith("navigate_");
  if (leaving) navigateAway();
  if (which.endsWith("foreground_first")) {
    finishForeground(); await foreground;
    finishBackground(); await background;
  } else {
    finishBackground(); await background;
    finishForeground(); await foreground;
  }
  if (leaving) { assert.equal(renders, 0); assert.equal(html, "Another page"); assert.equal(navigationCalls, 0); }
  else { completeView(); assert.equal(state.boards[0].slug, "project-2", "Stale foreground data must not overwrite the background result"); }
}
console.log(`${which}: PASS`);
