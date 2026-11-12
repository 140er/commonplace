// Rendering and saving of the Tool policy settings section. Run: node tests/tool_policy_view.mjs
import assert from "node:assert/strict";

const { policyRow, sortActions, summary, saveMode } = await import(new URL("../commonplace/web/static/tool-policy.js", import.meta.url));
const e = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const modes = ["allowed", "ask", "off"];
const actions = [
  { name: "task_add", kind: "write", mode: "allowed" },
  { name: "mempalace_search", kind: "read", mode: "off" },
  { name: "ask", kind: "write", mode: "allowed" },
  { name: "memory_save", kind: "write", mode: "ask" },
];

const row = policyRow(actions[3], modes, e);
assert.match(row, /data-tool-policy-mode="memory_save"/);
assert.match(row, /<option value="ask" selected>Ask first<\/option>/);
assert.match(row, /<option value="allowed">Allowed<\/option><option value="ask" selected>/);
assert.match(row, /<option value="off">Off<\/option>/);
assert.match(row, /Changes things/);
assert.match(policyRow(actions[1], modes, e), /Reads/);

const hostile = policyRow({ name: '<img src=x onerror="1">', kind: "unknown", mode: "off" }, modes, e);
assert.doesNotMatch(hostile, /<img/);
assert.match(hostile, /No longer served/);

// Narrowed actions come first, so the owner sees what differs from the default.
assert.deepEqual(sortActions(actions).map((a) => a.name), ["memory_save", "mempalace_search", "ask", "task_add"]);
assert.deepEqual(sortActions(actions, " MEM ").map((a) => a.name), ["memory_save", "mempalace_search"]);
assert.deepEqual(sortActions(actions, "nothing"), []);
assert.equal(actions[0].name, "task_add", "sorting leaves the loaded list alone");

assert.equal(summary(actions), "1 ask first · 1 off · the rest allowed");
assert.equal(summary(actions.map((a) => ({ ...a, mode: "allowed" }))), "Every action is allowed");

const calls = [];
await saveMode(async (path, options) => { calls.push([path, options]); return {}; }, "task_add", "off");
assert.deepEqual(calls, [["/api/tool-policy", { method: "POST", body: { actions: { task_add: "off" } } }]]);
console.log("ok");
