import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

// A small document: enough markup parsing and selector matching for the project dialogs.
const VOID = new Set(["input", "br", "img", "hr", "meta", "link"]);
const unescape = (value) => value.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");

class Text {
  constructor(text) { this.text = text; this.parent = null; }
  get textContent() { return this.text; }
}

class El {
  constructor(tag, doc) {
    Object.assign(this, { localName: tag.toLowerCase(), doc, attrs: new Map(), children: [], parent: null, listeners: {}, _value: null });
  }
  get tagName() { return this.localName.toUpperCase(); }
  get isConnected() { let node = this; while (node.parent) node = node.parent; return node === this.doc.documentElement; }
  getAttribute(name) { return this.attrs.has(name) ? this.attrs.get(name) : null; }
  hasAttribute(name) { return this.attrs.has(name); }
  setAttribute(name, value) { this.attrs.set(name, String(value)); }
  removeAttribute(name) { this.attrs.delete(name); }
  toggleAttribute(name, on) { if (on) this.setAttribute(name, ""); else this.removeAttribute(name); }
  get id() { return this.getAttribute("id") || ""; }
  set id(value) { this.setAttribute("id", value); }
  get className() { return this.getAttribute("class") || ""; }
  set className(value) { this.setAttribute("class", value); }
  get classList() { return { contains: (name) => this.className.split(/\s+/).includes(name) }; }
  get dataset() {
    return new Proxy({}, { get: (_, key) => this.getAttribute(`data-${String(key).replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`) ?? undefined });
  }
  get disabled() { return this.hasAttribute("disabled"); }
  set disabled(value) { this.toggleAttribute("disabled", !!value); }
  get hidden() { return this.hasAttribute("hidden"); }
  set hidden(value) { this.toggleAttribute("hidden", !!value); }
  get value() { return this._value ?? this.getAttribute("value") ?? ""; }
  set value(value) { this._value = String(value); }
  get textContent() { return this.children.map((child) => child.textContent).join(""); }
  set textContent(value) { this.replaceChildren(new Text(String(value))); }
  set innerHTML(html) { this.replaceChildren(...parse(html, this.doc)); }
  get innerHTML() { return this.textContent; }
  append(...nodes) {
    for (const node of nodes) {
      node.parent?.children.splice(node.parent.children.indexOf(node), 1);
      node.parent = this; this.children.push(node);
    }
  }
  replaceChildren(...nodes) { for (const child of this.children) child.parent = null; this.children = []; this.append(...nodes); }
  remove() { if (this.parent) { this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; } }
  contains(node) { for (let n = node; n; n = n.parent) if (n === this) return true; return false; }
  elements() { return this.children.filter((c) => c instanceof El).flatMap((c) => [c, ...c.elements()]); }
  querySelectorAll(selector) { return this.elements().filter((el) => matches(el, selector)); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  matches(selector) { return matches(this, selector); }
  closest(selector) { for (let n = this; n instanceof El; n = n.parent) if (matches(n, selector)) return n; return null; }
  getClientRects() { return this.isConnected && !this.closest("[hidden]") ? [{}] : []; }
  focus() { if (this.isConnected) this.doc.activeElement = this; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((f) => f !== fn); }
  dispatch(type, extra = {}) {
    const event = { type, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {}, stopImmediatePropagation() {}, ...extra };
    for (const fn of this.listeners[type] || []) fn(event);
    return event;
  }
  showModal() { this.setAttribute("open", ""); }
  click() { return this.onclick?.({ currentTarget: this, target: this, preventDefault() {}, stopPropagation() {} }); }
}

function parse(html, doc) {
  const root = new El("root", doc), stack = [root];
  for (const m of html.matchAll(/<!--[\s\S]*?-->|<\/([a-zA-Z0-9]+)\s*>|<([a-zA-Z0-9]+)((?:\s+[^\s=>/]+(?:="[^"]*")?)*)\s*\/?>|([^<]+)/g)) {
    const [, close, open, attrs, text] = m;
    if (text) stack.at(-1).append(new Text(unescape(text)));
    else if (close) { while (stack.length > 1 && stack.pop().localName !== close.toLowerCase()); }
    else if (open) {
      const el = new El(open, doc);
      for (const a of (attrs || "").matchAll(/([^\s=]+)(?:="([^"]*)")?/g)) el.setAttribute(a[1], unescape(a[2] ?? ""));
      stack.at(-1).append(el);
      if (!VOID.has(el.localName)) stack.push(el);
    }
  }
  const nodes = [...root.children]; root.replaceChildren(); return nodes;
}

function compound(el, part) {
  let rest = part;
  const tag = rest.match(/^[a-z0-9]+/i);
  if (tag) { if (el.localName !== tag[0].toLowerCase()) return false; rest = rest.slice(tag[0].length); }
  while (rest) {
    let m;
    if ((m = rest.match(/^:not\(([^()]*)\)/))) { if (compound(el, m[1])) return false; }
    else if ((m = rest.match(/^#([\w-]+)/))) { if (el.id !== m[1]) return false; }
    else if ((m = rest.match(/^\.([\w-]+)/))) { if (!el.classList.contains(m[1])) return false; }
    else if ((m = rest.match(/^\[([\w-]+)(?:="([^"]*)")?\]/))) { if (!el.hasAttribute(m[1]) || (m[2] !== undefined && el.getAttribute(m[1]) !== m[2])) return false; }
    else throw new Error(`unsupported selector ${part}`);
    rest = rest.slice(m[0].length);
  }
  return true;
}

function matches(el, selector) {
  return selector.split(",").some((one) => {
    const parts = one.trim().replace(/\s*>\s*/g, " > ").split(/\s+/);
    const test = (node, i) => {
      if (!(node instanceof El) || !compound(node, parts[i])) return false;
      if (i === 0) return true;
      if (parts[i - 1] === ">") return test(node.parent, i - 2);
      for (let up = node.parent; up instanceof El; up = up.parent) if (test(up, i - 1)) return true;
      return false;
    };
    return test(el, parts.length - 1);
  });
}

function makeDocument() {
  const doc = { listeners: {}, adoptedStyleSheets: [] };
  doc.documentElement = new El("html", doc);
  doc.body = new El("body", doc); doc.documentElement.append(doc.body);
  doc.activeElement = doc.body;
  doc.createElement = (tag) => new El(tag, doc);
  doc.querySelector = (s) => doc.documentElement.querySelector(s);
  doc.querySelectorAll = (s) => doc.documentElement.querySelectorAll(s);
  doc.addEventListener = (type, fn) => (doc.listeners[type] ||= []).push(fn);
  doc.removeEventListener = (type, fn) => { doc.listeners[type] = (doc.listeners[type] || []).filter((f) => f !== fn); };
  doc.key = (key, extra = {}) => {
    const event = { key, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {}, stopImmediatePropagation() {}, ...extra };
    for (const fn of [...(doc.listeners.keydown || [])]) fn(event);
    return event;
  };
  return doc;
}

const doc = makeDocument();
globalThis.document = doc;
globalThis.window = { addEventListener() {}, removeEventListener() {} };
globalThis.CSSStyleSheet = class { replaceSync() {} };
globalThis.CSS = { escape: (value) => String(value) };
globalThis.confirm = () => { throw new Error("window.confirm must not be used"); };

const ui = await import(new URL("../commonplace/web/static/projects.js", import.meta.url));
const e = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const tick = async (n = 10) => { for (let i = 0; i < n; i++) await new Promise((resolve) => setImmediate(resolve)); };
const openDialog = () => doc.querySelector("dialog[open]");
const project = { board: "ledger", name: "Ledger", can_configure: true, main: { name: "orchestrator:ledger", connected: true, seen: false, connectable: true } };
const result = { token: "synthetic-token", mcp_url: "http://localhost/mcp", setup: { webhook_add: { board: "ledger" } } };

function opener(label = "opener") {
  const button = doc.createElement("button");
  button.setAttribute("data-project-connect", ""); button.textContent = label;
  doc.body.append(button); button.focus();
  return button;
}

function assertTrapped(dialog) {
  const controls = dialog.querySelectorAll("button:not([disabled]), input:not([disabled])");
  assert.ok(controls.length >= 2, "the dialog has controls to cycle");
  controls.at(-1).focus();
  assert.equal(doc.key("Tab").defaultPrevented, true, "Tab on the last control stays in the dialog");
  assert.equal(doc.activeElement, controls[0]);
  assert.equal(doc.key("Tab", { shiftKey: true }).defaultPrevented, true, "Shift+Tab on the first control stays in the dialog");
  assert.equal(doc.activeElement, controls.at(-1));
  doc.activeElement = doc.body;
  doc.key("Tab");
  assert.ok(dialog.contains(doc.activeElement), "Tab from outside lands inside the dialog");
}

const cases = {
  // 03c: the empty board's Connect an agent is a modal dialog; Esc returns to the opener.
  async connect_dialog() {
    assert.equal(typeof ui.openConnectDialog, "function", "openConnectDialog missing");
    const button = opener();
    const calls = [];
    const dismiss = ui.openConnectDialog({ slug: "ledger", api: async (path, options) => { calls.push({ path, ...options }); return { ...project, main: { ...project.main, connected: false } }; }, e, opener: button });
    assert.equal(typeof dismiss, "function");
    const dialog = openDialog();
    assert.ok(dialog, "a dialog is open");
    assert.equal(dialog.getAttribute("aria-modal"), "true");
    assert.ok(doc.activeElement.matches("[data-project-create]"), "the primary action has focus");
    await tick();
    assert.match(dialog.querySelector("#project-connect-title").textContent, /Connect an agent to Ledger/);
    assertTrapped(dialog);
    dialog.dispatch("cancel");
    assert.equal(openDialog(), null);
    assert.equal(doc.activeElement, button);
    assert.ok(!calls.some((c) => c.method === "POST"), "Cancel creates no setup");
    button.remove();
  },
  // 03c → 03c2: the primary action makes the setup in the same dialog.
  async connect_continues() {
    assert.equal(typeof ui.openConnectDialog, "function", "openConnectDialog missing");
    const button = opener();
    const calls = [];
    const api = async (path, options = {}) => {
      calls.push({ path, ...options });
      if (options.method === "POST") return result;
      return { ...project, main: { ...project.main, connected: false } };
    };
    const later = [];
    const realTimeout = globalThis.setTimeout;
    globalThis.setTimeout = (fn) => { later.push(fn); return 0; };
    try {
      ui.openConnectDialog({ slug: "ledger", api, e, opener: button });
      const dialog = openDialog();
      await dialog.querySelector("[data-project-create]").click();
      await tick();
      assert.equal(openDialog(), dialog, "the same dialog continues");
      assert.deepEqual(calls.filter((c) => c.method === "POST").map((c) => c.path), ["/api/projects/ledger/orchestrator/connect"]);
      assert.equal(dialog.querySelector("[data-project-token]").value, "synthetic-token");
      assert.ok(doc.activeElement.matches("[data-project-token]"), "the setup has focus");
      assert.match(dialog.textContent, /Waiting for the agent/);
      dialog.dispatch("cancel");
      assert.equal(openDialog(), null);
      assert.equal(doc.activeElement, button);
    } finally { globalThis.setTimeout = realTimeout; button.remove(); }
  },
  // 03c2: Tab stays inside the setup dialog; Esc closes it and focus returns to Connect.
  async setup_trap() {
    assert.equal(typeof ui.openConnectDialog, "function", "openConnectDialog missing");
    const button = opener();
    const realTimeout = globalThis.setTimeout;
    globalThis.setTimeout = () => 0;
    try {
      ui.showConnectResult(result, e, { api: async () => project, path: "/api/projects/ledger", project, opener: button });
      const dialog = openDialog();
      assert.ok(doc.activeElement.matches("[data-project-token]"));
      assertTrapped(dialog);
      assert.equal(doc.key("Escape").defaultPrevented, true, "the dialog takes Escape");
      assert.equal(openDialog(), null);
      assert.equal(doc.activeElement, button);
    } finally { globalThis.setTimeout = realTimeout; button.remove(); }
  },
  // 03d: the connected phase moves focus to its primary button.
  async done_focus() {
    assert.equal(typeof ui.openConnectDialog, "function", "openConnectDialog missing");
    const button = opener();
    ui.showConnectResult(result, e, { api: async () => ({ ...project, main: { ...project.main, seen: true } }), path: "/api/projects/ledger", project, opener: button });
    await tick();
    const dialog = openDialog();
    assert.match(dialog.querySelector("#project-connect-title").textContent, /Orchestrator connected/);
    assert.ok(doc.activeElement.matches(".btn.primary") && dialog.contains(doc.activeElement), `focus: ${doc.activeElement.tagName}`);
    dialog.dispatch("cancel");
    assert.equal(openDialog(), null);
    button.remove();
  },
  // 03e: after 120 s the failed phase names the dialog and focuses its primary button.
  async failed_focus() {
    assert.equal(typeof ui.openConnectDialog, "function", "openConnectDialog missing");
    const button = opener();
    const realNow = Date.now, realTimeout = globalThis.setTimeout;
    let now = 1_000_000, later = null;
    Date.now = () => now;
    globalThis.setTimeout = (fn) => { later = fn; return 0; };
    try {
      ui.showConnectResult(result, e, { api: async () => project, path: "/api/projects/ledger", project, opener: button });
      await tick();
      assert.ok(later, "the next check is scheduled");
      now += 121_000;
      later(); await tick();
      const dialog = openDialog();
      assert.match(doc.querySelector("#project-connect-title").textContent, /not connected/);
      assert.equal(doc.querySelectorAll("#project-connect-title").length, 1, "one element names the dialog");
      assert.ok(doc.activeElement.matches(".btn.primary") && dialog.contains(doc.activeElement), `focus: ${doc.activeElement.tagName}`);
      dialog.dispatch("cancel");
      assert.equal(openDialog(), null);
    } finally { Date.now = realNow; globalThis.setTimeout = realTimeout; button.remove(); }
  },
  // 10c/10d: Disconnect asks in a drawn dialog with Cancel focused; window.confirm is never used.
  async disconnect_drawn() {
    assert.equal(typeof ui.openConnectDialog, "function", "openConnectDialog missing");
    assert.equal(typeof ui.mountProjectAgents, "function", "mountProjectAgents missing");
    const host = doc.createElement("div"); doc.body.append(host);
    const calls = [];
    const api = async (path, options = {}) => {
      calls.push({ path, ...options });
      if (path === "/api/connections") return { connections: [{ id: "c-1", name: "orchestrator:ledger", last_used_at: null }] };
      if (path.endsWith("/cards")) return { cards: [], linked: [] };
      return { ...project, main: { ...project.main, connected: false } };
    };
    await ui.mountProjectAgents(host, { project, api, e, current: () => true, onUpdate() {} });
    const disconnect = host.querySelector('[data-project-disconnect="c-1"]');
    disconnect.focus();
    const pending = disconnect.click();
    const dialog = doc.querySelector('[aria-modal="true"], dialog[open]');
    assert.ok(dialog, "a drawn dialog is open");
    assert.match(dialog.textContent, /Disconnect Ledger orchestrator\?/);
    assert.match(dialog.textContent, /What happens:/);
    assert.match(dialog.textContent, /It is the only agent in Ledger/);
    assert.ok(doc.activeElement.matches('[data-confirm="cancel"]'), "Cancel has focus");
    assertTrapped(dialog);
    assert.equal(doc.key("Escape").defaultPrevented, true, "the dialog takes Escape");
    await pending;
    assert.equal(openDialog(), null);
    assert.equal(doc.activeElement, host.querySelector('[data-project-disconnect="c-1"]'));
    assert.ok(!calls.some((c) => c.method === "DELETE"), "Esc disconnects nothing");
    const again = host.querySelector('[data-project-disconnect="c-1"]').click();
    openDialog().querySelector('[data-confirm="ok"]').click();
    await again; await tick();
    assert.deepEqual(calls.filter((c) => c.method === "DELETE").map((c) => c.path), ["/api/connections/c-1"]);
    host.remove();
  },
  // 10f: settings opened to restart the agent draw the amber heading with the times.
  async restart_heading() {
    assert.equal(typeof ui.openConnectDialog, "function", "openConnectDialog missing");
    const { deriveChatState } = await import(new URL("../commonplace/web/static/chatstate.js", import.meta.url));
    const html = ui.projectSettingsHTML(project, e, { origin: "#/board/ledger/chat", restart: true });
    assert.match(html, /class="orch-card warn"/);
    assert.doesNotMatch(ui.projectSettingsHTML(project, e, { origin: "#/board/ledger" }), /orch-card warn/);
    const asked = "2026-01-05T14:02:00", seen = "2026-01-05T14:03:00";
    const page = { me: { user: "user:maya" }, messages: [{ id: 7, actor: "user:maya", text: "Please draft the quarterly summary", at: asked }] };
    const chat = deriveChatState(page, { orchestrator: { visible: true, last_seen: seen } }, {}, Date.parse(asked) + 600_000);
    const text = ui.notAnsweringText(project, chat, page);
    const hhmm = (value) => new Date(value).toTimeString().slice(0, 5);
    assert.equal(text, `Not answering since ${hhmm(asked)} · connected · last active ${hhmm(seen)}`);
    assert.match(ui.mainHTML(project, e, { notAnswering: text }), /Not answering since/);
  },
  // I1: settings hand Back and Esc to the caller's onBack.
  async on_back() {
    assert.equal(typeof ui.openConnectDialog, "function", "openConnectDialog missing");
    const source = await readFile(new URL("../commonplace/web/static/projects.js", import.meta.url), "utf8");
    assert.match(source, /export async function mountProject\(host, \{[^}]*\bonBack\b/);
    assert.match(source, /bindSettingsNavigation\(host, \{[^}]*\bonBack\b/);
  },
};

assert.ok(process.argv[2] in cases, "unknown case");
await cases[process.argv[2]]();
console.log(`PASS ${process.argv[2]}`);
