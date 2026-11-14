import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const { DISCOVERY_PATH, discoveryAction, discoveryLines, startDiscovery } = await import("../commonplace/web/static/map-discovery.js");
const read = (name) => readFile(new URL(`../commonplace/web/static/${name}`, import.meta.url), "utf8");

const RESULT = {
  machines: [
    { machine: "mach_1", label: "one", outcome: "started", reason: null, card: 7, job: "j1" },
    { machine: "mach_2", label: "two", outcome: "running", reason: null, card: 8, job: "j2" },
    { machine: "mach_3", label: "three", outcome: "unreachable", reason: "never_reported", card: null, job: null },
    { machine: "mach_4", label: "four", outcome: "refused", reason: "no_topology_wing", card: null, job: null },
  ],
};

test("every machine gets a line and an unreachable one says why", () => {
  assert.deepEqual(discoveryLines(RESULT), [
    "one: started (card #7).",
    "two: already running (card #8).",
    "three: not reachable, it has never reported to the hub.",
    "four: not started, it has no access to the topology wing.",
  ]);
  assert.match(discoveryLines({ machines: [] })[0], /No runner machines/);
});

test("the button is off while the request runs and the outcome is written", async () => {
  const button = { disabled: false }, note = { textContent: "" };
  let seen = null;
  const api = async (path, options) => { seen = { path, options, off: button.disabled }; return RESULT; };
  await startDiscovery({ api, button, note });
  assert.deepEqual(seen, { path: DISCOVERY_PATH, options: { method: "POST", body: {} }, off: true });
  assert.equal(button.disabled, false);
  assert.match(note.textContent, /three: not reachable/);
});

test("a failed request is said, not swallowed", async () => {
  const button = { disabled: false }, note = { textContent: "" };
  const result = await startDiscovery({ api: async () => { throw new Error("no: a signed-in workspace owner is required"); }, button, note });
  assert.equal(result, null);
  assert.match(note.textContent, /signed-in workspace owner/);
  assert.equal(button.disabled, false);
});

test("the handler finds the map's button and adds one note", async () => {
  const nodes = {};
  const button = { disabled: false, insertAdjacentHTML() { nodes.note = { textContent: "" }; } };
  const host = { querySelector: (sel) => (sel.includes("start") ? button : nodes.note || null) };
  await discoveryAction({ api: async () => RESULT, root: () => host })();
  assert.match(nodes.note.textContent, /one: started/);
});

test("both screens give the map the handler", async () => {
  assert.match(await read("app.js"), /onStartWork: discoveryAction\(/);
  assert.match(await read("deck-compute.js"), /onStartWork: discoveryAction\(/);
});
