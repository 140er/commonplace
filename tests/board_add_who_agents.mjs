// Browser-free proof that the Add card form's Who lists the board's agents next to Me and Any agent, and says plainly
// what choosing one does. argv: <case> | render <me> <agents json> [pick ref]; env STATIC_DIR points at the static directory.
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const dir = process.env.STATIC_DIR || new URL("../commonplace/web/static", import.meta.url).pathname;
const board = await import(pathToFileURL(`${dir}/board.js`).href);
const [, , which, ...rest] = process.argv;
const ME = "user:maya", BOARD = { slug: "atlas", name: "Atlas" };
const PEOPLE = [{ ref: ME, name: "Maya" }, { ref: "user:leon", name: "Leon" }];
const AGENTS = [{ ref: "conn:atlas-coworker", name: "Atlas Coworker", starts_run: true }, { ref: "conn:quill", name: "Quill", starts_run: false }];
// Timers and promise chains settle over a few turns; never count on one.
const settle = async () => { for (let i = 0; i < 5; i++) await new Promise((resolve) => setImmediate(resolve)); };
globalThis.FormData = class { constructor(form) { this.form = form; } [Symbol.iterator]() { return Object.entries(this.form.values)[Symbol.iterator](); } };

// What a browser shows for the Who select: every option with its value, text, hint and whether it is selected.
function options(html) {
  const select = html.match(/<select name="assignee"[^>]*>([\s\S]*?)<\/select>/)[1];
  return [...select.matchAll(/<option value="([^"]*)"([^>]*)>([^<]*)<\/option>/g)].map(([, value, attrs, label]) => ({
    value, label, selected: /\bselected\b/.test(attrs), hint: attrs.match(/data-hint="([^"]*)"/)?.[1] ?? null,
  }));
}
const form = (agents) => board.addCardForm({ me: ME, people: PEOPLE, agents, sheet: false });
const hintOf = (html) => html.match(/data-who-hint[^>]*>([^<]*)</)[1];

if (which === "render") {
  // The page's own form for a server answer: used by the API test to compare what the form says with what the server does.
  const [me, agents, pick] = rest;
  const html = board.addCardForm({ me, people: [{ ref: me, name: "Maya" }], agents: JSON.parse(agents), sheet: false });
  const list = options(html);
  const chosen = pick !== undefined ? pick : list.find((o) => o.selected).value;
  let body;
  await board.submitCard({ board: BOARD, me, values: { title: "Buy milk", assignee: chosen }, api: async (url, init) => { body = init.body; return { number: 1 }; } });
  const picked = list.find((o) => o.value === chosen);
  console.log(JSON.stringify({ options: list, chosen, body, hint: picked?.hint || hintOf(html) }));
} else if (which === "options") {
  const list = options(form(AGENTS));
  assert.deepEqual(list.map((o) => o.value), [ME, "any", "user:leon", "conn:atlas-coworker", "conn:quill"]);
  assert.deepEqual(list.filter((o) => o.selected).map((o) => o.value), [ME], "Me stays the default");
  assert.ok(list[0].label.startsWith("Me") && list[1].label.startsWith("Any agent"), "Me and Any agent stay");
  assert.match(form(AGENTS), /<optgroup label="Bots and coworkers">/);
  console.log("options: PASS");
} else if (which === "none") {
  // A board with no agents offers exactly what it offered before.
  const html = form([]);
  assert.deepEqual(options(html).map((o) => o.value), [ME, "any", "user:leon"]);
  assert.ok(!html.includes("optgroup"));
  assert.deepEqual(options(board.addCardForm({ me: ME, people: PEOPLE, sheet: false })).map((o) => o.value), [ME, "any", "user:leon"]);
  console.log("none: PASS");
} else if (which === "wording") {
  const list = options(form(AGENTS)), byValue = Object.fromEntries(list.map((o) => [o.value, o]));
  assert.equal(byValue["conn:atlas-coworker"].label, "Atlas Coworker (starts now)");
  assert.equal(byValue["conn:atlas-coworker"].hint, "Adding this card starts Atlas Coworker now. Choose Me to add it without starting anything.");
  assert.equal(byValue["conn:quill"].label, "Quill (ready for it, no run starts)");
  assert.equal(byValue["conn:quill"].hint, "Ready for Quill. Nothing starts when you add this card.");
  assert.equal(hintOf(form(AGENTS)), "Nothing starts when you add this card.", "the default says nothing starts");
  assert.equal(board.whoHint("conn:quill", AGENTS), "Ready for Quill. Nothing starts when you add this card.");
  assert.equal(board.whoHint("conn:atlas-coworker", AGENTS), byValue["conn:atlas-coworker"].hint);
  assert.equal(board.whoHint("any", AGENTS), board.WHO_HINTS.any);
  console.log("wording: PASS");
} else if (which === "escape") {
  const html = form([{ ref: 'conn:a"b', name: '<img src=x onerror=1>', starts_run: false }]);
  assert.ok(!html.includes("<img"), "a bot name is text, never markup");
  assert.ok(html.includes("&lt;img src=x onerror=1&gt;"));
  console.log("escape: PASS");
} else if (which === "submit") {
  const sent = [];
  const api = async (url, init) => { sent.push(JSON.parse(JSON.stringify(init.body))); return { number: 1 }; };
  await board.submitCard({ api, board: BOARD, me: ME, values: { title: "Buy milk", assignee: "conn:quill" } });
  await board.submitCard({ api, board: BOARD, me: ME, values: { title: "Buy milk" } });
  await board.submitCard({ api, board: BOARD, me: ME, values: { title: "Buy milk", assignee: "any" } });
  assert.deepEqual(sent.map((b) => b.assignee), ["conn:quill", ME, null], "a bot is sent as itself, a missing Who stays Me, any stays unassigned");
  console.log("submit: PASS");
} else if (which === "change") {
  // Picking an option rewrites the hint with that option's own words; Me and Any agent keep theirs.
  const hint = { textContent: "" }, handlers = {};
  const select = (value, optionHint) => ({ value, selectedOptions: [{ dataset: optionHint ? { hint: optionHint } : {} }], closest: (s) => s === "[data-board-add]" ? { querySelector: () => hint } : null });
  const root = { addEventListener: (type, fn) => { handlers[type] = fn; }, removeEventListener() {}, querySelector: () => null, querySelectorAll: () => [] };
  board.bindBoard(root, { api: async () => ({}), board: BOARD, me: ME });
  const pick = (el) => handlers.change({ target: { closest: (s) => s === '[data-board-add] select[name="assignee"]' ? el : null } });
  pick(select("conn:quill", "Ready for Quill. Nothing starts when you add this card."));
  assert.equal(hint.textContent, "Ready for Quill. Nothing starts when you add this card.");
  pick(select("any")); assert.equal(hint.textContent, board.WHO_HINTS.any);
  pick(select(ME)); assert.equal(hint.textContent, board.WHO_HINTS.me);
  console.log("change: PASS");
} else if (which === "failure_keeps_choice") {
  // A refused create (a busy bot, a 409) keeps the typed words and the chosen bot; the person can send it again.
  const values = { title: "Pay rent", body: "Exact\nwords", assignee: "conn:quill" };
  const status = { textContent: "", setAttribute() {}, scrollIntoView() {} }, button = { disabled: false };
  const composer = { values, matches: (s) => s === "[data-board-add]", querySelector: (s) => s === '[type="submit"]' ? button : s === "[data-add-status]" ? status : null };
  const handlers = {}, added = [];
  const root = { addEventListener: (type, fn) => { handlers[type] = fn; }, removeEventListener() {}, querySelector: (s) => s === "[data-board-add]" ? composer : null, querySelectorAll: () => [] };
  board.bindBoard(root, { api: async () => { throw Object.assign(new Error("agent_busy"), { status: 409 }); }, board: BOARD, me: ME, onAdded: (c) => added.push(c) });
  await handlers.submit({ target: composer, preventDefault() {} }); await settle();
  assert.deepEqual(added, []);
  assert.match(status.textContent, /Could not add card/);
  assert.match(status.textContent, /Your words are kept here/);
  assert.deepEqual(composer.values, { title: "Pay rent", body: "Exact\nwords", assignee: "conn:quill" });
  assert.equal(button.disabled, false);
  console.log("failure_keeps_choice: PASS");
} else if (which === "board_view") {
  // The whole board draws the agents in both looks, and the Deck keeps the same form.
  for (const deck of [false, true]) {
    const view = board.boardView({ board: BOARD, cards: [], me: ME, people: PEOPLE, agents: AGENTS, adding: true, sheet: true, deck, bots: [] });
    assert.ok(view.includes('value="conn:quill"') && view.includes('value="any"') && view.includes(`value="${ME}"`), `deck=${deck}`);
  }
  console.log("board_view: PASS");
} else throw new Error(`unknown case ${which}`);
