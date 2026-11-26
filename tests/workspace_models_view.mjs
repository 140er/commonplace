import assert from "node:assert/strict";
import test from "node:test";

// Card #661: the workspace's model per role, the Grok suggestion, the account line and the
// person's own account items in the list of things waiting on them.
const ui = await import(new URL("../commonplace/web/static/projects.js", import.meta.url));
const needs = await import(new URL("../commonplace/web/static/needs.js", import.meta.url));
const e = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const grok = { provider: "grok", model: "grok-4.7" }, opus = { provider: "claude", model: "claude-opus-5-5" };
const view = {
  can_configure: true,
  models: { main: grok, worker: grok, reviewer: opus, gate: null },
  model_catalog: [opus], model_suggestions: [grok],
  models_effective: {
    main: { ...grok, source: "workspace", account: { mode: "personal", kind: "person", label: "my <Grok>", linked: true, reason: null } },
    worker: { ...grok, source: "workspace", account: { mode: "personal", kind: null, label: null, linked: false, reason: "This workspace uses each person's own account, and you have not connected your own grok account." } },
    reviewer: { ...opus, source: "workspace", account: { mode: "org", kind: "org", label: null, linked: false, reason: null } },
    gate: { provider: null, model: null, source: "none" },
  },
};

test("the workspace picker offers the list, then Grok 4.7 as a suggestion, per role", () => {
  const html = ui.workspaceModelsHTML(view, e);
  for (const role of ["main", "worker", "reviewer", "gate"]) assert.match(html, new RegExp(`name="${role}_model"`));
  assert.match(html, /<option value="grok grok-4\.7" selected>grok · grok-4\.7 \(suggested\)<\/option>/);
  assert.match(html, /<option value="claude claude-opus-5-5" selected>claude · claude-opus-5-5<\/option>/);
  assert.match(html, /No workspace choice/);
  assert.match(html, /Save workspace models/);
  assert.match(html, /data-workspace-model-add/);
  const member = ui.workspaceModelsHTML({ ...view, can_configure: false }, e);
  assert.doesNotMatch(member, /Save workspace models|data-workspace-model-add/);
  assert.match(member, /<fieldset disabled>/);
});

test("each role says whose account runs it and whether your own account is linked", () => {
  assert.equal(ui.accountWords(view, "main"), "On your own account (my <Grok>). Your own grok account: linked.");
  assert.match(ui.accountWords(view, "worker"), /not connected your own grok account\. Your own grok account: not linked\.$/);
  assert.equal(ui.accountWords(view, "reviewer"), "On the workspace account. Your own claude account: not linked.");
  assert.equal(ui.accountWords(view, "gate"), "");
  const html = ui.workspaceModelsHTML(view, e);
  assert.ok(html.includes("my &lt;Grok&gt;") && !html.includes("my <Grok>"));
});

test("your own accounts list shows labels and machines, never a credential field", () => {
  const html = ui.ownAccountsHTML({ connections: [{ id: "mc_0000000000000001", provider: "grok", label: "my Grok", machine: "own-1" }], use_own: false }, e);
  assert.match(html, /grok login --device-auth/);
  assert.match(html, /data-own-remove="mc_0000000000000001"/);
  assert.doesNotMatch(html, /type="password"|api key<\/label>|name="token"|name="key"/i);
  assert.match(ui.ownAccountsHTML({ connections: [] }, e), /No own account linked yet/);
  assert.equal(ui.ownAccountsHTML(null, e), "");
});

test("a missing connection and an expired sign-in each get one row for that person", () => {
  const items = {
    model_connections: [{ kind: "model_connection", board: "work", provider: "grok", message: "Connect it.", action: { label: "Connect your account" } }],
    model_signins: [{ kind: "model_signin", provider: "grok", machine: "own-1", message: "Sign in again.", action: { label: "Sign in again" } }],
  };
  assert.equal(needs.accountItems(items).length, 2);
  assert.equal(needs.accountItems(items, "approvals").length, 0);
  const html = needs.renderNeeds({ needs: items });
  assert.match(html, /Your accounts/);
  assert.match(html, /data-account-item="work:grok"/);
  assert.match(html, /data-account-item="grok:own-1"/);
  assert.match(html, /2 items/);
  assert.doesNotMatch(html, /Nothing needs you right now/);
});

test("the owner's account switch shows the current choice and says nothing falls back", () => {
  assert.equal(ui.modeHTML(undefined, e), "");
  const unset = ui.modeHTML(null, e);
  assert.match(unset, /<option value="unset" selected>Current setup \(nothing chosen\)<\/option>/);
  const personal = ui.modeHTML({ mode: "personal", allow_person_override: false }, e);
  assert.match(personal, /<option value="personal" selected>Each person&#39;s own account<\/option>/);
  assert.match(personal, /Nothing falls back to another account or provider/);
  assert.match(ui.modeHTML({ mode: "hybrid", allow_person_override: true }, e), /name="allow_person_override" checked/);
});
