import assert from "node:assert/strict";
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
let current = true, pending, release;
const host = new Host(), calls = [];
let version = {version: 2, ref: "v2", status: "proposed", proposed_by: "user:author", sha256: "b".repeat(64)};
if (caseName === "escape") version.proposed_by = '<img src=x onerror="bad()">';
const api = async (path, options = {}) => {
  calls.push({path, ...options});
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
const posts = () => calls.filter(c => c.method === "POST");
const click = selector => {
  const button = host.querySelector(selector);
  assert.ok(button, `Missing ${selector}`);
  assert.equal(button.disabled, false);
  return button.onclick();
};
assert.match(host.innerHTML, /Changes/);
assert.match(host.innerHTML, /New prompt/);
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
