import assert from "node:assert/strict";
import { test } from "node:test";
import { createMemoryImport, renderImport } from "../commonplace/web/static/memory-import.js";

const bytes = Buffer.from(' \r\n[{"role":"user","content":" café 🦉 "}]\r\n');
const file = { name: "export.json", size: bytes.length, arrayBuffer: async () => bytes };
const checked = { source: "cursor", scope: "threads", total: 1, messages: 1, source_bytes: bytes.length, preview: "checked" };
const tick = () => new Promise((resolve) => setImmediate(resolve));
function fixture(api, selected = file) {
  const listeners = new Map();
  const mount = { innerHTML: "", addEventListener(type, fn) { listeners.set(type, fn); }, removeEventListener(type, fn) { assert.equal(listeners.get(type), fn); listeners.delete(type); }, querySelector() { return { focus() {} }; } };
  let closed = 0;
  const ui = createMemoryImport({ mount, api, me: {}, onClose() { closed++; ui.stop(); } });
  const choose = () => listeners.get("submit")({ preventDefault() {}, target: { matches: () => true, elements: { file: { files: [selected] }, format: { value: "2" }, scope: { value: "threads" } } } });
  const click = (action) => listeners.get("click")({ target: { closest: () => ({ dataset: { importAction: action } }) } });
  return { mount, listeners, ui, choose, click, closed: () => closed };
}

test("all displayed upload metadata is escaped", () => {
  const html = renderImport({ phase: "preview", preview: checked, filename: '<img src=x onerror="bad">', source: "cursor", format: "messages" });
  assert.ok(html.includes("&lt;img"));
  assert.ok(!html.includes("<img"));
  const error = renderImport({ phase: "error", error: "<script>bad</script>" });
  assert.ok(!error.includes("<script>"));
  assert.ok(renderImport({}).includes('type="file"'));
  assert.ok(!renderImport({}).includes('value="native-memory"'));
});

test("preview cancellation submits no confirmation and removes handlers", async () => {
  const calls = [];
  const f = fixture(async (path, options) => { calls.push([path, options.body]); return checked; });
  await f.choose();
  assert.equal(calls.length, 1);
  assert.equal(calls[0][1].transcript, bytes.toString("utf8"));
  assert.ok(f.mount.innerHTML.includes("Confirm import"));
  f.click("close");
  assert.equal(f.closed(), 1);
  assert.equal(f.listeners.size, 0);
  assert.equal(calls.length, 1);
});

test("confirmation cannot double-submit and retry sends identical bytes and consent", async () => {
  const calls = [];
  let resolve, attempt = 0;
  const f = fixture(async (path, options) => {
    calls.push([path, options.body]);
    if (path.endsWith("preview")) return checked;
    if (++attempt === 1) return new Promise((done) => { resolve = done; });
    return { processed: 1, total: 1, imported: 0, duplicates: 1, errors: 0, results: [] };
  });
  await f.choose();
  f.click("confirm");
  f.click("confirm");
  assert.equal(calls.length, 2);
  assert.ok(f.mount.innerHTML.includes("<progress"));
  resolve({ processed: 1, total: 1, imported: 0, errors: 1, results: [{ status: "error", error: "Try again" }] });
  await tick();
  f.click("retry");
  await tick();
  assert.equal(calls.length, 3);
  assert.deepEqual(calls[1], calls[2]);
  assert.equal(calls[2][1].consent, true);
  assert.equal(calls[2][1].preview, "checked");
  assert.equal(calls[2][1].transcript, bytes.toString("utf8"));
  assert.ok(f.mount.innerHTML.includes("Already imported. No new copy was added."));
  f.ui.stop();
});

test("navigation discards a late preview result", async () => {
  let finish;
  const f = fixture(() => new Promise((resolve) => { finish = resolve; }));
  const loading = f.choose();
  await tick();
  const screen = f.mount.innerHTML;
  f.ui.stop();
  finish(checked);
  await loading;
  assert.equal(f.mount.innerHTML, screen);
  assert.equal(f.listeners.size, 0);
});

test("invalid UTF-8 and oversized files are refused before any upload", async () => {
  for (const bad of [{ ...file, arrayBuffer: async () => Uint8Array.of(0xff) }, { ...file, size: 4 * 1024 * 1024 + 1 }]) {
    let calls = 0;
    const f = fixture(async () => { calls++; }, bad);
    await f.choose();
    assert.equal(calls, 0);
    assert.ok(f.mount.innerHTML.includes('role="alert"'));
    f.ui.stop();
  }
});
