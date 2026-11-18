// Runs the document's early script and the client's one-shot take of its reads (no browser).
// usage: node early_reads_harness.mjs <early script file> <api.js file>
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { pathToFileURL } from "node:url";

const [scriptFile, apiFile] = process.argv.slice(2);
const script = readFileSync(scriptFile, "utf8");
const settle = () => new Promise((resolve) => setTimeout(resolve, 5));

async function early(hash, { storage = {}, boards = [], fail = false } = {}) {
  const fetched = [], links = [];
  const entries = Object.entries(storage);
  const context = {
    location: { hash },
    localStorage: { get length() { return entries.length; }, key: (i) => entries[i]?.[0] ?? null, getItem: (k) => storage[k] ?? null },
    document: { createElement: () => ({}), head: { append: (link) => links.push(link.href) } },
    fetch: (path) => {
      fetched.push(path);
      if (fail) return Promise.reject(new Error("offline"));
      const body = path.startsWith("/api/boards?") ? { boards } : {};
      return Promise.resolve({ clone: () => ({ json: async () => body }), json: async () => body });
    },
    Date, Object, JSON, Promise, encodeURIComponent, decodeURIComponent, globalThis: null,
  };
  context.globalThis = context;
  vm.runInNewContext(script, context);
  await settle();
  return { links, fetched };
}

const out = {};
out.board_slug = await early("#/board/project");
out.board_encoded = await early("#/board/p%20q");
out.board_saved = await early("#/board", { storage: { "cmmn.board.last.w1": "ops" }, boards: [{ slug: "a" }, { slug: "ops" }] });
out.board_saved_gone = await early("#/board", { storage: { "cmmn.board.last.w1": "ops" }, boards: [{ slug: "a" }] });
out.board_unsaved = await early("#/board", { boards: [{ slug: "a" }, { slug: "b" }] });
out.board_ambiguous = await early("#/board", { storage: { "cmmn.board.last.w1": "ops", "cmmn.board.last.w2": "x" }, boards: [{ slug: "a" }] });
out.board_empty = await early("#/board", { boards: [] });
out.board_chat = await early("#/board/project/chat");
out.settings = await early("#/settings");
out.offline = await early("#/board/project", { fail: true });

// api.js takes a matching early GET once; writes, stale, aborted and failed ones read afresh.
globalThis.document = { dispatchEvent: (event) => { out.expired = (out.expired || 0) + 1; } };
globalThis.Event = class { constructor(type) { this.type = type; } };
const { api } = await import(pathToFileURL(apiFile).href);
const network = [];
globalThis.fetch = async (path, init) => {
  network.push(`${init?.method || "GET"} ${path}`);
  return { ok: true, status: 200, json: async () => ({ from: "network" }) };
};
const early_response = (status = 200, body = { from: "early" }) => Promise.resolve({ ok: status < 400, status, json: async () => body });
const put = (path, response, at = Date.now()) => { (globalThis.commonplaceEarly ||= {})[path] = { at, response }; };

put("/api/me", early_response());
out.first = await api("/api/me");
out.second = await api("/api/me");
put("/api/boards/p/cards", early_response());
out.post = await api("/api/boards/p/cards", { method: "POST", body: { title: "x" } });
out.get_after_post = await api("/api/boards/p/cards");
put("/api/boards?view=shell", early_response(), Date.now() - 20000);
out.stale = await api("/api/boards?view=shell");
put("/api/stalled", Promise.reject(new Error("offline")));
out.failed = await api("/api/stalled");
put("/api/gone", early_response(401, { error: "unauthorized" }));
try { await api("/api/gone"); } catch (error) { out.refused = error.status; }
put("/api/timed", early_response());
out.timed = await api("/api/timed", { timeoutMs: 3000 });
out.network = network;
console.log(JSON.stringify(out));
