import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {readFileSync} from "node:fs";
import {mountProjectReview} from "../commonplace/web/static/projects.js";

const e = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
class Host {
  isConnected = true;
  set innerHTML(html) {
    this.html = html;
    this.nodes = [...html.matchAll(/<(button|p)\b([^>]*)>([^<]*)/g)].map(([, tag, attrs, textContent]) => {
      const a = Object.fromEntries([...attrs.matchAll(/([\w-]+)(?:="([^"]*)")?/g)].map(m => [m[1], m[2] ?? ""]));
      return {tag, a, textContent, disabled: "disabled" in a, getAttribute: key => a[key], focus() {}};
    });
  }
  get innerHTML() { return this.html; }
  querySelectorAll(selector) {
    const match = selector.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
    assert.ok(match, selector);
    return this.nodes.filter(n => match[1] in n.a && (match[2] === undefined || n.a[match[1]] === match[2]));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  replaceChildren() { this.innerHTML = ""; }
}

const caseName = process.argv[2];
const deck = caseName.startsWith("deck_");
if (deck) globalThis.document = {documentElement: {dataset: {ui: "deck"}}};
const snapshots = [];
const snapshot = () => snapshots.push(createHash("sha256").update(host.innerHTML).digest("hex"));
let current = true, pending, release;
const host = new Host(), calls = [];
let version = {version: 2, ref: "v2", status: "proposed", proposed_by: "user:author", sha256: "b".repeat(64)};
if (caseName === "escape") version.proposed_by = '<img src=x onerror="bad()">';
const api = async (path, options = {}) => {
  calls.push({path, ...options});
  if (path === "/api/me") return {actor: "user:owner", people: {"user:author": caseName === "deck_escape" ? '<img src=x onerror="bad()">' : "Config author", "user:owner": "Config owner"}};
  if (options.method === "POST") {
    if (caseName === "failure") throw new Error("Could not reject this version. Try again.");
    if (caseName === "busy" || caseName === "stale") await new Promise(resolve => {release = resolve;});
    assert.ok(path.endsWith("/reject"), "confirmation must reject, never accept");
    version = {...version, status: "rejected", decided_by: "user:owner"};
    return version;
  }
  if (path.endsWith("/configs")) {
    if (caseName === "refresh_failure" && calls.some(c => c.method === "POST")) throw new Error("Refresh failed");
    return {accepted: caseName === "first" ? null : {version: 1}, versions: [version]};
  }
  if (path.endsWith("/configs/2")) return {version: {...version, body: "New prompt"}};
  if (path.includes("/diff?")) return {diff: "-Old prompt\n+New prompt"};
  if (path.endsWith("/cards")) return {cards: []};
  throw new Error(`Unexpected read: ${path}`);
};
await mountProjectReview(host, {project: {board: "ledger", can_configure: caseName !== "member"}, api, e, current: () => current});
if (caseName === "classic_bytes") snapshot();
const posts = () => calls.filter(c => c.method === "POST");
const click = selector => {
  const button = host.querySelector(selector);
  assert.ok(button, `Missing ${selector}`);
  assert.equal(button.disabled, false);
  return button.onclick();
};
assert.match(host.innerHTML, /Changes/);
assert.match(host.innerHTML, /New prompt/);
if (deck) {
  if (caseName === "deck_unknown") {
    // Re-mount without a directory entry: unknown actors remain identifiable.
    await mountProjectReview(host, {project: {board: "ledger", can_configure: false}, api: async (path, options) => path === "/api/me" ? {actor: "user:reader", people: {}} : api(path, options), e});
    assert.match(host.innerHTML, /Proposed by user:author/);
    assert.doesNotMatch(host.innerHTML, /data-config-accept/);
  } else if (caseName === "deck_escape") {
    assert.doesNotMatch(host.innerHTML, /<img/);
    assert.match(host.innerHTML, /&lt;img/);
  } else if (caseName === "deck_wrap") {
    const css = readFileSync(new URL("../commonplace/web/static/deck.css", import.meta.url), "utf8");
    const rules = css.match(/:root\[data-ui="deck"\] \.workbench-project \[data-config-diff\]\s*\{([^}]+)\}/)?.[1];
    assert.ok(rules, "config diff needs a Deck-only wrapping rule");
    assert.match(rules, /white-space:\s*pre-wrap/);
    assert.match(rules, /overflow-wrap:\s*anywhere/);
    assert.match(rules, /font-size:\s*13px/);
    assert.match(rules, /padding:\s*12px/);
    assert.match(rules, /border-radius:\s*12px/);
  } else {
    assert.match(host.innerHTML, /Proposed by Config author/);
    assert.doesNotMatch(host.innerHTML, /user:author/);
    await click('[data-config-reject="2"]');
    await click('[data-config-confirm-reject="2"]');
    assert.match(host.innerHTML, /Rejected by you/);
    assert.doesNotMatch(host.innerHTML, /user:owner|data-config-accept|data-config-reject/);
    assert.ok(host.innerHTML.indexOf("data-project-review-status") < host.innerHTML.indexOf("Waiting for release"));
  }
  process.exit(0);
}
if (caseName === "member") {
  assert.doesNotMatch(host.innerHTML, /data-config-(accept|reject|confirm-reject)/);
  assert.doesNotMatch(host.innerHTML, /data-project-release/);
  assert.equal(posts().length, 0);
} else if (caseName === "escape") {
  assert.doesNotMatch(host.innerHTML, /<img/);
  assert.match(host.innerHTML, /&lt;img/);
} else {
  assert.match(host.innerHTML, /data-config-accept/);
  await click('[data-config-reject="2"]');
  if (caseName === "classic_bytes") snapshot();
  assert.equal(posts().length, 0, "Reject must ask before writing");
  assert.match(host.innerHTML, /Reject Coworker config v2\?/);
  assert.match(host.innerHTML, /current config/);
  assert.equal(host.querySelector('[data-config-accept="2"]').disabled, true);
  if (caseName === "cancel") {
    await click("[data-config-cancel-reject]");
    assert.equal(posts().length, 0);
    assert.equal(host.querySelector("[data-config-confirm-reject]"), null);
    assert.equal(host.querySelector('[data-config-accept="2"]').disabled, false);
  } else {
    pending = click('[data-config-confirm-reject="2"]');
    if (caseName === "busy" || caseName === "stale") {
      assert.equal(posts().length, 1);
      assert.equal(host.querySelector('[data-config-reject="2"]').disabled, true);
      if (caseName === "stale") {current = false; host.innerHTML = "New page";}
      release();
    }
    await pending;
    if (caseName === "classic_bytes") snapshot();
    assert.deepEqual(posts().map(c => ({path: c.path, body: c.body})), [{path: "/api/orch/boards/ledger/configs/2/reject", body: {}}]);
    if (caseName === "failure") {
      assert.match(host.innerHTML, /Could not reject/);
      assert.ok(host.querySelector('[data-config-accept="2"]'), "failed write keeps proposal");
    } else if (caseName === "stale") {
      assert.equal(host.innerHTML, "New page");
    } else {
      assert.match(host.innerHTML, /Rejected/);
      assert.match(host.innerHTML, /user:owner/);
      assert.equal(host.querySelector('[data-config-accept="2"]'), null);
      assert.equal(host.querySelector('[data-config-reject="2"]'), null);
    }
  }
}
if (caseName === "classic_bytes") assert.deepEqual(snapshots, [
  "035449945ce241cca9dafe7040dd898435ce912fb93d82243dd3e772be69d300",
  "ccc8efe383f5531c48b83f653d1d6b61c34c92f54acc1cf701cb03d699ec8b16",
  "e5bebf5a75c73c7832f092c1915f818a5e762fdffaa4b3367de03d2834400a21",
]);
