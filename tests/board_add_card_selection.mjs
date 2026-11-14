// Browser-free proof for the Add card / side panel / loading behaviours of the shell.
// argv: <case>; env STATIC_DIR points at the static directory to test (the mutation tests pass a patched copy).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { pathToFileURL } from "node:url";

const dir = process.env.STATIC_DIR || new URL("../commonplace/web/static", import.meta.url).pathname;
const read = (name) => readFileSync(`${dir}/${name}`, "utf8");
const load = (name) => import(pathToFileURL(`${dir}/${name}`).href);
const app = read("app.js");
function slice(start, end) {
  const at = app.indexOf(start), stop = app.indexOf(end, at + start.length);
  assert.ok(at >= 0 && stop > at, `Missing slice: ${start} -> ${end}`);
  return app.slice(at, stop);
}
const board = await load("board.js"), loading = await load("loading.js").catch(() => ({})), routes = await load("routes.js"), deckBoard = await load("deck-board.js");
const which = process.argv[2];
const ME = "user:maya";
const BOARD = { slug: "home", name: "Home" };
const NEW = { number: 9, version: 1, board: "home", status: "todo", title: "Pay rent", assignee: ME };
const OLD = [
  { number: 4, version: 2, board: "home", status: "review", need: "you", title: "Fresh-user test", ask: "Ship?", ask_to: ME, ask_options: ["Approve", "Hold"] },
  { number: 5, version: 1, board: "home", status: "doing", need: "working", title: "Build the thing" },
];

// A panel or content node as the shell sees it: innerHTML and hidden.
function fakePanel(html = "") { return { hidden: true, innerHTML: html }; }

// Runs the real route() against stubs: reads never come back, so what the screen shows is what shows before the first read.
function routeHarness({ hash, base, panelHtml, card, contentHtml = "<p>old</p>", deck = true }) {
  const timers = [], reads = [], never = new Promise(() => {});
  globalThis.setTimeout = (fn, ms) => { timers.push([fn, ms]); return timers.length; };
  const content = fakePanel(contentHtml); content.children = [{}];
  const panel = fakePanel(panelHtml); panel.hidden = !card;
  const location = { hash };
  const state = { me: { actor: ME, can_admin: true }, deck: { on: () => deck, home: () => "#/", leave() {}, owns: (value) => deck && ["#/", "#/memory", "#/compute"].includes(value) }, screen: 4, route: base, base, boards: [], card, selected: null, addingCard: false, boardFilter: "all", justAdded: null, panelRequest: 0, workbenchChat: null, needsUI: null, memoryUI: null, queue: null, chatVisible: new Set(), queueFocus: "" };
  const context = {
    state, location, history: { state: {}, replaceState: (_s, _t, value) => { location.hash = value; } }, localStorage: { getItem: () => null, setItem() {} },
    api: (path) => { reads.push(path); return never; },
    root: { querySelector: (selector) => selector === "#content" ? content : selector === "#card-panel" ? panel : null },
    remember() {}, clearAgentSecret() {}, voice: { stop() {} }, boardPreference: () => "k", boardRoute: routes.boardRoute, resolveRoute: routes.resolveRoute, 
    decideStart: () => undefined, queueFlush() {}, queueStop() {}, createSession() {}, queueArmSlow() {}, markOpenCard() {}, showLoading: loading.showLoading, message: (text) => `<div class="message" role="status">${text}</div>`,
    showError() {}, speech: { stop() {} }, adminScreen() {}, instanceAdminScreen() {}, refresh: () => never, boardDispose: null, detailsUI: null, adminUI: null, instanceAdminUI: null, botsUI: null, consoleUI: null,
    decodeURIComponent, URLSearchParams, enc: encodeURIComponent, askedRead: () => new Set(),
  };
  runInNewContext(slice("async function route(", "// The current view's list first"), context);
  return { state, content, panel, reads, timers, start: () => { void context.route(); }, tick: () => new Promise((resolve) => setImmediate(resolve)) };
}

if (which === "add_flow" || which === "add_flow_classic") {
  const deck = which === "add_flow";
  const state = { me: { actor: ME }, deck: { on: () => deck }, addingCard: true, drafts: new Map([["board-title", "Pay rent"]]), cards: structuredClone(OLD), children: [], selected: 5, boardFilter: "state:review", boardColumn: null, justAdded: null };
  const location = { hash: "#/board/home" };
  let html = "", focused = null, scrolled = null;
  const newCard = { dataset: { card: "9" }, scrollIntoView: () => { scrolled = 9; }, focus: () => { focused = 9; } };
  const context = {
    state, location, cardColumn: board.cardColumn,
    // The production boardView passes these same fields to the real renderer.
    boardView() {
      html = board.boardView({ board: BOARD, cards: [...state.cards, ...state.children], me: ME, filter: state.boardFilter || "all", selected: state.selected, adding: state.addingCard, added: state.justAdded, sheet: false, column: state.boardColumn || "todo", deck, bots: [], people: [] });
      state.boardColumn = null;
    },
    root: { querySelector: (selector) => selector === '.workbench-board .card[data-card="9"]' && html.includes('data-card="9"') ? newCard : null },
  };
  // Before this change the same handler was an inline callback of the board binding.
  const handler = app.includes("function cardAdded(") ? slice("function cardAdded(", "function markOpenCard(") : `function cardAdded(card) ${app.match(/onAdded\(card\) (\{.*\}),\n/)[1]}`;
  runInNewContext(handler, context);
  context.boardView();
  assert.match(html, /<form data-board-add/, "Precondition: the form is open before the add");
  context.cardAdded(NEW);
  assert.equal(state.addingCard, false);
  assert.doesNotMatch(html, /<form data-board-add/, "The form closes: no second submit is possible");
  assert.equal(state.drafts.has("board-title"), false, "The typed draft is dropped with the form");
  assert.equal(location.hash, "#/card/9", "The address names the new card");
  assert.equal(state.selected, 9, "The new card is the selected one");
  assert.equal(state.cards.filter((card) => card.number === 9).length, 1);
  assert.equal(state.boardFilter, "all", "A filter that would hide the new card is lifted");
  assert.match(html, /<article class="card is-selected"[^>]*data-card="9"[^>]*aria-current="true"/, "The new card is drawn highlighted");
  assert.doesNotMatch(html, /<article class="card[^"]*is-selected[^"]*"[^>]*data-card="5"/, "The old selection is not highlighted any more");
  assert.equal(focused, 9); assert.equal(scrolled, 9);
  if (deck) {
    assert.match(html, /<p class="deck-board-intro" role="status" data-board-added>Added “Pay rent” to Active\.<\/p>/, "The notice names the column the card is in");
    assert.match(html, /<section class="col" id="col-doing"[^>]*data-active="true"/, "The phone shows the column the card landed in");
    assert.match(html, /id="col-doing"[\s\S]*data-card="9"[\s\S]*id="col-review"/, "The card sits in that column");
  } else {
    assert.match(html, /<section class="col" id="col-todo"[^>]*data-active="true"/);
    assert.match(html, /id="col-todo"[\s\S]*data-card="9"[\s\S]*id="col-doing"/);
  }
} else if (which === "stale_panel") {
  const stale = '<div class="deck-card-actions"><button data-deck-release>Approve</button></div> Fresh-user test';
  const h = routeHarness({ hash: "#/card/9", base: "#/board/home", panelHtml: stale, card: { number: 4 } });
  h.start();
  await h.tick();
  // The new card's read has not come back yet (the slow refresh): the panel must not still show the old card.
  assert.equal(h.panel.hidden, false, "The card route opens the panel");
  assert.doesNotMatch(h.panel.innerHTML, /Approve|Fresh-user/, "No other card's live buttons while the new card loads");
  assert.match(h.panel.innerHTML, /Loading card/, "The panel says the card is loading");
  // The same card keeps its content (a refresh of the open card).
  const same = '<p>card 9 detail</p>';
  const again = routeHarness({ hash: "#/card/9", base: "#/board/home", panelHtml: same, card: { number: 9 } });
  again.start(); await again.tick();
  assert.equal(again.panel.innerHTML, same);
  // A non-card address hides the panel.
  const away = routeHarness({ hash: "#/board/home", base: "#/board/home", panelHtml: stale, card: null });
  away.start(); await away.tick();
  assert.equal(away.panel.hidden, true);
} else if (which === "escape") {
  class Element {}
  const closed = [], handlers = [];
  const panel = { hidden: false, tag: "panel" };
  const field = (value, inPanel, tag = "textarea") => { const node = Object.assign(new Element(), { value, blurred: 0, blur() { this.blurred++; }, isContentEditable: false, matches: (selector) => selector.includes(tag) }); node.closest = (selector) => (selector === "#card-panel" ? (inPanel ? panel : null) : selector.startsWith("input") ? node : null); return node; };
  const plain = Object.assign(new Element(), { closest: () => null, matches: () => false });
  const state = { me: { actor: ME }, base: "#/board/home", route: "#/card/9", deck: { on: () => false } };
  const dialog = { open: false };
  const context = {
    Element, state, closeCardPanel: () => closed.push("closed"), escapeInPanel: undefined,
    root: { querySelector: (selector) => selector === "#card-panel" ? panel : selector === "#shell-dialog" ? dialog : null },
    document: { addEventListener: (type, handler) => type === "keydown" && handlers.push(handler), querySelector: () => null },
    shellMenu() {}, singleKeysEnabled: () => false, workbenchKey: () => false,
  };
  const from = app.includes("function escapeInPanel(") ? "function escapeInPanel(" : 'document.addEventListener("keydown", (event) => {\n  if (event.defaultPrevented) return;';
  const source = slice(from, "\n});\n") + "\n});\n";
  runInNewContext(source, context);
  assert.equal(handlers.length, 1, "The shell registers one keydown handler");
  const press = (target, extra = {}) => { const event = { key: "Escape", target, defaultPrevented: false, prevented: 0, preventDefault() { this.prevented++; }, ...extra }; handlers[0](event); return event; };
  // Focus in the card's comment box with nothing typed: Escape closes the card.
  closed.length = 0; let f = field("", true);
  let event = press(f);
  assert.deepEqual(closed, ["closed"]); assert.equal(event.prevented, 1); assert.equal(f.blurred, 0);
  // Typed text is kept: the first Escape leaves the field, the second (focus on the page) closes.
  closed.length = 0; f = field("half a reply", true);
  press(f);
  assert.deepEqual(closed, []); assert.equal(f.blurred, 1);
  press(plain);
  assert.deepEqual(closed, ["closed"], "Focus outside a field: Escape closes the open card");
  // A field outside the card (the board's own form) never closes the card from here.
  closed.length = 0;
  press(field("", false));
  assert.deepEqual(closed, []);
  // No card open: Escape does nothing to the panel.
  panel.hidden = true; closed.length = 0;
  press(field("", true));
  assert.deepEqual(closed, []);
} else if (which === "logo") {
  // Deck: the logo's address is the Home screen and the router draws it without waiting for the project list.
  const h = routeHarness({ hash: "#/", base: "#/memory", panelHtml: "", card: null, contentHtml: "<p>Memory</p>" });
  h.start();
  await h.tick();
  assert.deepEqual(h.reads, [], "The logo's route reads nothing before it draws");
  assert.equal(h.state.base, "#/", "The base moved to Home straight away");
  assert.match(h.content.innerHTML, /Loading…/, "Something says it is working at once");
  assert.doesNotMatch(h.content.innerHTML, /Still loading/);
  assert.ok(h.timers.length, "A slow-load hint is armed");
  assert.equal(h.timers.at(-1)[1], 1000);
  h.timers.at(-1)[0]();
  assert.match(h.content.innerHTML, /Still loading/, "After a second the screen says it is slow");
  const deckShell = await load("deck-shell.js"), deckMod = await load("deck.js");
  assert.equal(deckMod.deckHome(), "#/");
  const bar = deckShell.renderShellBar({ user: "Maya", sections: deckShell.SECTIONS, section: "memory", hash: "#/memory", bots: [], directory: {} });
  assert.match(bar, /<a class="deck-brand" href="#\/" aria-label="Commonplace home">/);
  assert.equal(routes.resolveRoute("#/", { boards: [], deck: true }), "#/");
  // Classic look: the bare visit still needs the project list first.
  const classic = routeHarness({ hash: "#/", base: "#/memory", panelHtml: "", card: null, deck: false });
  classic.start(); await classic.tick();
  assert.deepEqual(classic.reads, ["/api/boards"]);
} else if (which === "loading") {
  const timers = [];
  globalThis.setTimeout = (fn, ms) => { timers.push([fn, ms]); return timers.length; };
  assert.equal(loading.SLOW_AFTER_MS, 1000);
  let host = fakePanel("<p>old</p>");
  loading.showLoading(host, "Loading card…");
  assert.equal(host.innerHTML, '<div class="message" role="status">Loading card…</div>');
  assert.equal(timers.length, 1); assert.equal(timers[0][1], 1000, "The hint waits one second");
  timers[0][0]();
  assert.match(host.innerHTML, /Loading card… <span data-slow>Still loading\. This can take a few seconds\.<\/span><\/div>$/);
  // The card arrived before the second ran out: nothing is added to what replaced the loading text.
  host = fakePanel(); loading.showLoading(host, "Loading…");
  host.innerHTML = "<h1>Today</h1>";
  timers.at(-1)[0]();
  assert.equal(host.innerHTML, "<h1>Today</h1>");
  // A newer loading text is not hinted by the older timer.
  host = fakePanel(); loading.showLoading(host, "Loading…"); const first = timers.at(-1)[0];
  loading.showLoading(host, "Loading card…");
  first();
  assert.doesNotMatch(host.innerHTML, /Still loading/);
  // The same text drawn again by a newer load: the older timer leaves it alone.
  host = fakePanel(); loading.showLoading(host, "Loading…"); const older = timers.at(-1)[0];
  loading.showLoading(host, "Loading…");
  older();
  assert.doesNotMatch(host.innerHTML, /Still loading/);
  // The card read in the shell uses it.
  assert.match(slice("async function cardPanel(", "function newCardForm("), /showLoading\(panel, "Loading card…"\)/);
} else {
  throw new Error(`Unknown case ${which}`);
}
console.log(`${which}: PASS`);
