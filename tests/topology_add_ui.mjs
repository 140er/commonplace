import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const { ADD_PATHS, add, addAction, addLines, copyText } = await import("../commonplace/web/static/map-add.js");
const read = (name) => readFile(new URL(`../commonplace/web/static/${name}`, import.meta.url), "utf8");

const STARTED = { kind: "machine", mode: "started", machines: [{ machine: "mach_1", label: "one", outcome: "started", card: 7 }] };
const PROMPT = { kind: "machine", mode: "prompt", machines: [], prompt: "READY PROMPT", command: "run-this --now" };
const harness = (result, overrides = {}) => {
  const seen = [], notes = [], button = { disabled: false }, copied = [];
  const api = async (path, options) => { seen.push({ path, options, off: button.disabled }); if (result instanceof Error) throw result; return result; };
  const copy = async (text) => { copied.push(text); return overrides.copied ?? true; };
  return { seen, notes, button, copied, options: { api, button, note: (text) => notes.push(text), copy, ...overrides.options } };
};

test("a started run is said in words, with the card", () => {
  assert.match(addLines(STARTED), /^Started on one \(card #7\)\. What it finds waits for your approval\.$/);
  const running = { ...STARTED, machines: [{ ...STARTED.machines[0], outcome: "running" }] };
  assert.match(addLines(running), /^Already running on one/);
});

test("Add machine asks nothing: one POST with an empty body, the button off meanwhile", async () => {
  const h = harness(STARTED);
  const result = await add("machine", h.options);
  assert.equal(result, STARTED);
  assert.deepEqual(h.seen, [{ path: ADD_PATHS.machine, options: { method: "POST", body: {} }, off: true }]);
  assert.equal(h.button.disabled, false);
  assert.deepEqual(h.copied, []);
  assert.match(h.notes[0], /Started on one/);
});

test("without an agent the prompt is copied and the short command is shown", async () => {
  const h = harness(PROMPT);
  await add("machine", h.options);
  assert.deepEqual(h.copied, ["READY PROMPT"]);
  assert.match(h.notes[0], /No agent is available\. The prompt is copied/);
  assert.match(h.notes[0], /run-this --now/);
});

test("when the clipboard refuses the prompt is shown instead", async () => {
  const h = harness(PROMPT, { copied: false });
  await add("machine", h.options);
  assert.match(h.notes[0], /The prompt is below/);
  assert.match(h.notes[0], /READY PROMPT$/);
  assert.equal(await copyText("x", { writeText: async () => { throw new Error("denied"); } }), false);
  assert.equal(await copyText("x", null), false);
  const written = []; assert.equal(await copyText("x", { writeText: async (t) => written.push(t) }), true); assert.deepEqual(written, ["x"]);
});

test("Add site asks for the domain only, and sends nothing when none is given", async () => {
  const site = { kind: "site", mode: "started", machines: [{ machine: "mach_1", label: "one", outcome: "started", card: 9 }] };
  const h = harness(site, { options: { ask: () => "  example.com " } });
  await add("site", h.options);
  assert.deepEqual(h.seen.map(({ path, options }) => [path, options]), [[ADD_PATHS.site, { method: "POST", body: { site: "example.com" } }]]);
  for (const answer of [null, "", "   "]) {
    const none = harness(site, { options: { ask: () => answer } });
    assert.equal(await add("site", none.options), null);
    assert.deepEqual(none.seen, []);
  }
});

test("a site prompt fallback names the route trace and shows no machine command", async () => {
  const h = harness({ kind: "site", mode: "prompt", machines: [], prompt: "TRACE PROMPT", command: null }, { options: { ask: () => "example.com" } });
  await add("site", h.options);
  assert.deepEqual(h.copied, ["TRACE PROMPT"]);
  assert.match(h.notes[0], /trace the route/);
  assert.doesNotMatch(h.notes[0], /Or run this/);
});

test("a refused or failed request is said, not swallowed, and the button comes back", async () => {
  const h = harness(new Error("a signed-in workspace owner is required"));
  assert.equal(await add("machine", h.options), null);
  assert.match(h.notes[0], /Could not add the machine: a signed-in workspace owner is required/);
  assert.equal(h.button.disabled, false);
});

test("the handler maps the two actions and ignores others", async () => {
  const h = harness(STARTED, { options: { ask: () => "example.com" } });
  const act = addAction(h.options);
  assert.equal(act("map", h.button), null);
  await act("add-machine", h.button); await act("add-site", h.button);
  assert.deepEqual(h.seen.map((s) => s.path), [ADD_PATHS.machine, ADD_PATHS.site]);
});

test("the Compute screen wires both buttons to this module and no screen has a form for them", async () => {
  const deck = await read("deck-compute.js");
  assert.match(deck, /from '\.\/map-add\.js'/);
  assert.match(deck, /data-compute-action="add-machine"/);
  assert.match(deck, /data-compute-action="add-site"/);
  assert.doesNotMatch(await read("map-add.js"), /<form|<input|innerHTML/);
});
