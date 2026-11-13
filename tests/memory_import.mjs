import assert from "node:assert/strict";
import { test } from "node:test";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { createMemoryImport, renderImport } from "../commonplace/web/static/memory-import.js";

const bytes = Buffer.from(' \r\n[{"role":"user","content":" café 🦉 "}]\r\n');
const file = { name: "export.json", size: bytes.length, arrayBuffer: async () => bytes };
const checked = { source: "cursor", scope: "threads", total: 1, messages: 1, source_bytes: bytes.length, preview: "checked" };
const tick = () => new Promise((resolve) => setImmediate(resolve));
function fixture(api, selected = file, look = "classic") {
  const listeners = new Map();
  const mount = { innerHTML: "", addEventListener(type, fn) { listeners.set(type, fn); }, removeEventListener(type, fn) { assert.equal(listeners.get(type), fn); listeners.delete(type); }, querySelector() { return { focus() {} }; } };
  let closed = 0;
  const ui = createMemoryImport({ mount, api, me: {}, look, onClose() { closed++; ui.stop(); } });
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
  assert.ok(!renderImport({}).includes("Memory text (.md or .txt)"));
  assert.ok(renderImport({}, { can_admin: true }).includes("Memory text (.md or .txt)"));
});

test("classic import rendering stays byte-identical in every phase", () => {
  const hashes = {
    choose: "57b9f48eee2de5414ccc72b4b812d4eb10256977e9177dba2f9c3f69fd2359c2",
    reading: "5d1be7703bc41cc9eebcb5b07691f0d747573c4495d5b9197918d4a743b8b0cd",
    previewing: "d4e867e40b9883eeec54927effc3376d48013be0fcc45400fbb4771a0c4bbed9",
    preview: "6e0318f267d987b6fc5c259b178890ea19a083242d29c79aeadfc9a9141ae69d",
    importing: "18bc7378f383ee0c56725bd0f3bc4dc1f625cc9eff97be8ebeee3312e8f9c1be",
    error: "f5a872dc5f8d54cfd2028e18d35ae9523799e45094a7900cf60c8bb834ece7f9",
    report: "1db7d92d2dbc111ef0be57476edc8cf68fdaaa201156d415401c9c8092c9a2ef",
  };
  for (const [phase, hash] of Object.entries(hashes)) {
    const state = { phase, preview: { source: "chatgpt", scope: "threads", total: 1, messages: 1, source_bytes: 100, preview: "checked" }, filename: "conversation.json", source: "chatgpt", format: "auto", error: "Retry safely.", report: { processed: 1, total: 1, imported: 0, duplicates: 1, errors: 0, results: [] } };
    assert.equal(createHash("sha256").update(renderImport(state, { can_admin: true })).digest("hex"), hash, phase);
  }
});

test("Deck chooser uses plain words and an accessible custom file control", () => {
  const html = renderImport({}, {}, "deck");
  assert.ok(html.includes("One text file, up to 4 MB. No ZIP files or attachments."));
  assert.ok(html.includes("Choose file"));
  assert.ok(html.includes("No file chosen"));
  assert.match(html, /type="file"[^>]*required/);
  assert.ok(!html.includes("Memory text (.md or .txt)"));
  assert.ok(renderImport({}, { can_admin: true }, "deck").includes("Memory text (.md or .txt)"));
});

test("Deck preview preserves the chosen source name and explains where the file stays", () => {
  const html = renderImport({ phase: "preview", preview: checked, filename: "export.json", source: "chatgpt", format: "auto" }, {}, "deck");
  assert.match(html, /<dt>Source<\/dt><dd>ChatGPT conversation \(\.json\)<\/dd>/);
  assert.ok(html.includes("1 file · 1 message"));
  assert.ok(html.includes("The file stays on this server. No AI model reads it."));
});

test("Deck report groups results without redundant counts or zero errors", () => {
  const state = { phase: "report", preview: checked, filename: "export.json", source: "cursor", format: "messages", report: { processed: 1, total: 1, imported: 0, duplicates: 1, errors: 0, results: [] } };
  const html = renderImport(state, {}, "deck");
  for (const label of ["File", "Source", "Destination", "Added", "Already imported"]) assert.ok(html.includes(`<dt>${label}</dt>`), label);
  for (const label of ["Count", "Size", "Files processed", "Errors"]) assert.ok(!html.includes(`<dt>${label}</dt>`), label);
  const failed = renderImport({ ...state, report: { ...state.report, errors: 1, results: [{ status: "error", error: "Retry safely." }] } }, {}, "deck");
  assert.ok(failed.includes("<dt>Errors</dt><dd>1</dd>"));
  assert.ok(failed.includes("Nothing was saved twice. Try again."));
  const privateReport = renderImport({ ...state, report: { processed: 1, total: 1, accepted: 1, errors: 0, results: [] } }, {}, "deck");
  assert.ok(privateReport.includes("Accepted (including exact retries)"));
  assert.ok(!privateReport.includes("<dt>Already imported</dt>"));
  assert.ok(!privateReport.includes("<dt>Added</dt>"));
});

test("Deck errors keep retry wording plain and metadata escaped", () => {
  const html = renderImport({ phase: "error", error: "<script>Retry safely.</script>" }, {}, "deck");
  assert.ok(html.includes("Import did not finish"));
  assert.ok(html.includes("Nothing was saved twice. Try again."));
  assert.ok(!html.includes("<script>"));
  assert.ok(renderImport({ phase: "error", error: "File import unavailable; retry safely" }, {}, "deck").includes("Nothing was saved twice. Try again."));
  const f = fixture(async () => {}, file, "deck");
  assert.ok(f.mount.innerHTML.includes("No file chosen"));
  const filename = { textContent: "" };
  f.mount.querySelector = () => filename;
  f.listeners.get("change")({ target: { matches: () => true, files: [{ name: "<b>export.json" }] } });
  assert.equal(filename.textContent, "<b>export.json");
  f.ui.stop();
  assert.equal(f.listeners.size, 0);
});

test("Deck import styles use the established control and progress tokens", () => {
  const css = readFileSync(new URL("../commonplace/web/static/deck.css", import.meta.url), "utf8");
  const control = css.match(/:root\[data-ui="deck"\] \.memory-import select\s*\{([^}]+)\}/)?.[1] || "";
  for (const token of ["height: 44px", "border: 0", "border-radius: var(--r)", "background: var(--well)", "box-shadow: none"]) assert.ok(control.includes(token), token);
  assert.match(css, /\.memory-import select:focus-visible[^}]+outline: 2px solid var\(--line-strong\)/);
  assert.match(css, /\.memory-import progress\s*\{[^}]+height: 4px[^}]+border: 0/);
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
