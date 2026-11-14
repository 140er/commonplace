// Browser-free proof that a failed Add card keeps the words, says why in plain words, and never adds twice.
// argv: <case>; env STATIC_DIR points at the static directory to test.
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const dir = process.env.STATIC_DIR || new URL("../commonplace/web/static", import.meta.url).pathname;
const board = await import(pathToFileURL(`${dir}/board.js`).href);
const which = process.argv[2];
const ME = "user:maya", BOARD = { slug: "home", name: "Home" };
const BUSY = "The workspace is busy with another change, so this did not go through. Try again in a moment.";
const failure = (status, message) => Object.assign(new Error(message), { status });
// Timers and promise chains settle over a few turns; never count on one.
const settle = async () => { for (let i = 0; i < 5; i++) await new Promise((resolve) => setImmediate(resolve)); };
globalThis.FormData = class { constructor(form) { this.form = form; } [Symbol.iterator]() { return Object.entries(this.form.values)[Symbol.iterator](); } };

// One composer as the page draws it: a submit button, a status line and the typed values.
function fakeForm(values) {
  const status = { textContent: "", attrs: new Map(), setAttribute(name, value) { this.attrs.set(name, value); }, scrollIntoView() {} };
  const button = { disabled: false };
  return { values, status, button, matches: (selector) => selector === "[data-board-add]", querySelector: (selector) => selector === '[type="submit"]' ? button : selector === "[data-add-status]" ? status : null };
}
function page(api, onAdded = () => {}) {
  const handlers = {}, forms = [];
  const root = { addEventListener: (type, fn) => { handlers[type] = fn; }, removeEventListener() {}, querySelector: (selector) => selector === "[data-board-add]" ? forms.at(-1) : null, querySelectorAll: () => [] };
  const stop = board.bindBoard(root, { api, board: BOARD, me: ME, onAdded });
  // The board re-renders on every poll: a new composer replaces the old one.
  const draw = (values = { title: "Pay rent", body: "Exact\nwords" }) => { forms.push(fakeForm(values)); return forms.at(-1); };
  const submit = (form) => handlers.submit({ target: form, preventDefault() {} });
  return { draw, submit, stop, forms };
}

if (which === "message_409") {
  // The failure is the server's own refusal: its reason and its number reach the person, the words stay put.
  const added = [], p = page(async () => { throw failure(409, BUSY); }, (card) => added.push(card));
  const form = p.draw();
  await p.submit(form); await settle();
  assert.deepEqual(added, []);
  assert.match(form.status.textContent, /Could not add card/);
  assert.ok(form.status.textContent.includes("did not go through") && form.status.textContent.includes("409"), form.status.textContent);
  assert.match(form.status.textContent, /Your words are kept here/);
  assert.deepEqual(form.values, { title: "Pay rent", body: "Exact\nwords" });
  assert.equal(form.button.disabled, false, "the form can be sent again");
  console.log("message_409: PASS");
} else if (which === "network") {
  const p = page(async () => { throw new TypeError("Failed to fetch"); });
  const form = p.draw();
  await p.submit(form); await settle();
  assert.match(form.status.textContent, /check your connection/);
  assert.ok(!form.status.textContent.includes("Failed to fetch"));
  console.log("network: PASS");
} else if (which === "survives_rerender") {
  // A poll redraws the composer after the failure: the message is drawn with it.
  const p = page(async () => { throw failure(409, BUSY); });
  await p.submit(p.draw()); await settle();
  const view = board.boardView({ board: BOARD, cards: [], me: ME, adding: true, sheet: true, deck: false });
  assert.ok(view.includes("did not go through") && view.includes("409"), "the redrawn composer lost the message");
  assert.match(view, /<p role="status" data-add-status data-failed>/);
  // Another board never shows it.
  const other = board.boardView({ board: { slug: "other", name: "Other" }, cards: [], me: ME, adding: true, sheet: true, deck: false });
  assert.ok(!other.includes("did not go through"), "another board shows this board's failure");
  console.log("survives_rerender: PASS");
} else if (which === "double_submit") {
  // Two quick taps, and a redraw between them that makes a fresh enabled composer: one request only.
  let calls = 0, finish;
  const p = page(() => { calls++; return new Promise((resolve) => { finish = () => resolve({ number: 7 }); }); });
  const first = p.draw();
  const sent = p.submit(first);
  await p.submit(first);
  const redrawn = p.draw();
  void p.submit(redrawn);
  await settle();
  assert.equal(calls, 1, `sent ${calls} create requests`);
  assert.ok(board.boardView({ board: BOARD, cards: [], me: ME, adding: true, sheet: true, deck: false }).includes('type="submit" disabled'), "the redrawn composer is not locked while the request is out");
  finish(); await sent; await settle();
  assert.equal(redrawn.button.disabled, false);
  console.log("double_submit: PASS");
} else if (which === "retry_clears") {
  let fail = true; const added = [];
  const p = page(async () => { if (fail) throw failure(409, BUSY); return { number: 3 }; }, (card) => added.push(card));
  const form = p.draw();
  await p.submit(form); await settle();
  fail = false;
  await p.submit(form); await settle();
  assert.equal(added.length, 1);
  assert.match(form.status.textContent, /added to Todo/);
  assert.ok(!board.boardView({ board: BOARD, cards: [], me: ME, adding: true, sheet: true, deck: false }).includes("did not go through"));
  console.log("retry_clears: PASS");
} else {
  throw new Error(`unknown case ${which}`);
}
