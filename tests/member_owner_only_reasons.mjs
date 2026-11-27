import assert from "node:assert/strict";
import { test } from "node:test";
import { escape as e } from "../commonplace/web/static/api.js";
import { renderDetails } from "../commonplace/web/static/details.js";
import { releaseBlock } from "../commonplace/web/static/release-block.js";
import { PROJECT_COPY, escalationHTML, modelsHTML, projectSettingsHTML, workspaceModelsHTML, workspaceOrchestrationHTML } from "../commonplace/web/static/projects.js";
import { mountToolPolicy } from "../commonplace/web/static/tool-policy.js";
import { mountAgentSchedules } from "../commonplace/web/static/agent-schedules.js";
import { createAdmin } from "../commonplace/web/static/admin.js";
import * as ownerOnly from "../commonplace/web/static/owner-only.js";

const text = html => html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
const reasons = html => [...html.matchAll(/<(?:p|small)[^>]*data-owner-only[^>]*>([^<]*)</g)].map(match => match[1]);
const project = owner => ({ board: "atlas", name: "Atlas", can_configure: owner, main: { name: "bot:atlas", connected: false }, models: {}, model_list: [], escalation: null, limits: {} });
const card = { number: 7, version: 3, board: "atlas", title: "Ship it", body: "", events: [], status: "todo", need: "idle", priority: "high" };

test("a member is told why a control is missing; an owner sees no reason line", () => {
  const cases = {
    "project settings": [projectSettingsHTML(project(false), e), projectSettingsHTML(project(true), e)],
    "project models": [modelsHTML(project(false), e), modelsHTML(project(true), e)],
    "project escalation": [escalationHTML(project(false), e), escalationHTML(project(true), e)],
    "workspace models": [workspaceModelsHTML({ can_configure: false, models: {}, model_list: [] }, e), workspaceModelsHTML({ can_configure: true, models: {}, model_list: [] }, e)],
    "workspace bots": [workspaceOrchestrationHTML({ enabled: true, owner: false }, e), workspaceOrchestrationHTML({ enabled: true, owner: true }, e)],
    "card priority": [renderDetails({ ...card, can_prioritize: false }, { deck: true }), renderDetails({ ...card, can_prioritize: true }, { deck: true })],
    "waiting for release": [releaseBlock({ ...card, awaiting_release: true, can_approve: false, creator: { name: "Secret Bot", trust: "untrusted" } }), releaseBlock({ ...card, awaiting_release: true, can_approve: true })],
  };
  for (const [name, [member, owner]] of Object.entries(cases)) {
    assert.ok(reasons(member).some(line => /Only the workspace owner/.test(line)), `${name}: member sees no owner-only reason`);
    assert.deepEqual(reasons(owner), [], `${name}: owner sees a reason line`);
  }
});

test("a member's reason names no data and says who to ask", () => {
  const html = releaseBlock({ ...card, awaiting_release: true, can_approve: false, creator: { name: "Secret Bot", trust: "untrusted" } });
  assert.doesNotMatch(html, /Secret Bot|untrusted/);
  assert.match(text(html), /Waiting for release/);
  assert.doesNotMatch(html, /<input|<button/);
  assert.doesNotMatch(Object.values(ownerOnly.OWNER_ONLY_COPY).join(" "), /orchestrator/i);
  for (const copy of [ownerOnly.OWNER_ONLY_COPY.connections, PROJECT_COPY.ownerOnlyProject]) assert.match(copy, /ask the workspace owner|Ask the workspace owner/);
});

test("a refused settings section shows the reason to a member and the error to an owner", () => {
  const refused = Object.assign(new Error("Owner access required"), { status: 403 });
  const member = ownerOnly.ownerOnlySectionHTML("Connected apps", refused, { can_admin: false });
  assert.match(member, /data-owner-only/);
  assert.match(text(member), /Only the workspace owner can add or change connected apps/);
  assert.doesNotMatch(member, /Owner access required/);
  assert.equal(ownerOnly.ownerOnlySectionHTML("Connected apps", refused, { can_admin: true }), null);
  assert.equal(ownerOnly.ownerOnlySectionHTML("Connected apps", Object.assign(new Error("boom"), { status: 500 }), { can_admin: false }), null);
});

test("the workspace bot switch is disabled for a member", () => {
  assert.match(workspaceOrchestrationHTML({ enabled: true, owner: false }, e), /data-orchestration-enabled[^>]*disabled/);
  assert.doesNotMatch(workspaceOrchestrationHTML({ enabled: true, owner: true }, e), /data-orchestration-enabled[^>]*disabled/);
});

test("tool policy and scheduled prompts show the reason when the server refuses a member", async () => {
  const refuse = async () => { throw Object.assign(new Error("Owner access required"), { status: 403 }); };
  for (const mount of [mountToolPolicy, mountAgentSchedules]) {
    const host = { isConnected: true, innerHTML: "loading", closest: () => ({ remove() { throw new Error("section removed"); } }), replaceChildren() { throw new Error("section emptied"); } };
    await mount(host, { api: refuse, e });
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.deepEqual(reasons(host.innerHTML), [ownerOnly.OWNER_ONLY]);
  }
});

test("Admin tells a member who may open it", () => {
  for (const [admin, expected] of [[false, /Only the workspace owner can open Admin\. Ask the workspace owner/], [true, null]]) {
    const host = { innerHTML: "", querySelector: () => null, addEventListener() {}, removeEventListener() {} };
    try { createAdmin({ mount: () => host, api: async () => ({}), e, current: () => ({ can_admin: admin, workspace: "w" }) }).show(); } catch { /* an owner's page needs more than this fixture */ }
    if (expected) assert.match(host.innerHTML, expected); else assert.doesNotMatch(host.innerHTML, /data-owner-only/);
  }
});
