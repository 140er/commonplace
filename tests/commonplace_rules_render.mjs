import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
const url = (source) => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const read = (name) => readFile(new URL(`../commonplace/web/static/${name}`, import.meta.url), "utf8");
const apiUrl = url(await read("api.js"));
const { clarifyPrompt, clarifySetPrompt, wordDiff, parseSetReply, desktopBridge, waitingCount, rulesView, rulesHandlers } = await import(url((await read("rules.js")).replace('"./api.js"', `"${apiUrl}"`)));
const rule = (id, fields = {}) => ({ id, agent: "conn:writer", key: `rule-${id}`, text: "Always preserve exact words.", agent_text: null, kind: "instruction", scope: "global", scope_ref: null, status: "proposed", source: "agent", pending: 0, version: 3, ...fields });
const first = rule(1);
assert.equal(clarifyPrompt(first), `Rewrite this rule for an AI agent so it has exactly one reading.
Keep its meaning and its strength (must / may / never). Don't add new obligations.
Name the trigger, the action and any exception. One to three sentences.

Agent: conn:writer    Kind: instruction    Scope: global
Rule (quoted, verbatim):
"""
Always preserve exact words.
"""

Return only the improved rule text, nothing else.`);
const board = rule(2, { scope: "board", scope_ref: "work", text: "Never lose\n  whitespace.\n" });
assert.ok(clarifyPrompt(board).includes('Scope: board work\nRule (quoted, verbatim):\n"""\nNever lose\n  whitespace.\n\n"""'));
assert.ok(!clarifyPrompt({ ...first, updated_by: "private-metadata", token: "private-metadata" }).includes("private-metadata"));
const retired = rule(3, { status: "retired", text: "Retired text" });
assert.equal(clarifySetPrompt("conn:writer", [first, board, retired]), `Rewrite these rules for an AI agent so each has exactly one reading.
Keep its meaning and its strength (must / may / never). Don't add new obligations.
Name the trigger, the action and any exception. One to three sentences.
Check for rules that conflict or overlap.

Agent: conn:writer
Rules (quoted, verbatim):
"""
rule-1: Always preserve exact words.
rule-2: Never lose
  whitespace.

"""

Return only <key>: <improved text> lines, nothing else.`);
assert.deepEqual(parseSetReply("rule-1: Improved: exact words.\r\nunknown: Ignore\nrule-2: Better\nrule-3: Old\nrule-1: Latest\nrule-2:   \nNo colon", [first, board, retired]), [{ rule_id: 1, text: "Latest" }, { rule_id: 2, text: "Better" }]);
assert.deepEqual(parseSetReply("An unstructured reply", [first]), []);
assert.equal(wordDiff("one two", "one three"), "one <del>two</del><ins>three</ins>");
assert.equal(wordDiff("  one\n", "  one\n"), "  one\n");
assert.equal(wordDiff("", "<script>"), "<ins>&lt;script&gt;</ins>");
assert.equal(wordDiff('<script> "&', "safe"), "<del>&lt;script&gt; &quot;&amp;</del><ins>safe</ins>");
assert.equal(wordDiff("gone", ""), "<del>gone</del>");
assert.equal(wordDiff(null, null), "");
// Both sides of a diff must recover the original verbatim texts.
for (const [before, after] of [["a b a", "b a b"], ["a  b\nc", "a b c"], ["a", "a a"], ["b a b a", "a b a b"]]) {
  const diff = wordDiff(before, after);
  assert.equal(diff.replace(/<ins>.*?<\/ins>/gs, "").replace(/<\/?del>/g, ""), before);
  assert.equal(diff.replace(/<del>.*?<\/del>/gs, "").replace(/<\/?ins>/g, ""), after);
}
assert.equal(desktopBridge(), null);
globalThis.window = {};
assert.equal(desktopBridge(), null);
window.commonplaceDesktop = { version: 2, ask() {} };
assert.equal(desktopBridge(), null);
window.commonplaceDesktop = { version: 1 };
assert.equal(desktopBridge(), null);
const pending = rule(4, { status: "edited", pending: 1, text: "Keep mine", agent_text: "Try theirs" });
const all = rule(5, { agent: "*", status: "accepted", text: '<script> "&' });
const payload = { waiting: 3, waiting_by_agent: { "conn:writer": 3 }, rules: [first, board, retired, pending, all], suggestions: [{ id: 8, rule_id: 1, base_version: first.version, text: "A clearer rule", from_agent: "reader" }], agents: ["writer", "reader"] };
assert.equal(waitingCount(payload.rules), 3);
assert.equal(waitingCount([rule(6, { pending: 1 })]), 1);
assert.equal(waitingCount([]), 0);
const html = rulesView(payload);
assert.ok(html.includes("All agents") && html.includes("conn:writer") && html.includes("conn:reader"));
assert.equal((html.match(/class="rule-tile"/g) || []).length, 3);
assert.ok(html.indexOf('data-rule-agent="conn:writer"') < html.indexOf('data-rule-agent="*"'));
assert.ok(!html.includes("Ask an agent") && html.includes("Copy prompt"));
assert.ok(!html.includes("<script>") && html.includes("&lt;script&gt;"));
assert.ok(html.includes('data-rule-action="accept_agent"') && html.includes('data-rule-action="keep_edit"'));
assert.ok(html.includes('<del>Try</del><ins>Keep</ins> <del>theirs</del><ins>mine</ins>'));
assert.ok(html.includes('data-rule-action="suggestion-accept"') && html.includes('data-rule-action="suggestion-dismiss"'));
assert.ok(html.includes('data-rule-form="new"') && html.includes('value="*">All agents'));
assert.ok(html.includes('<details class="rule-retired"><summary>Retired (1)'));
assert.ok(!html.includes('style="') && !html.includes("onclick="));
const proposedActions = html.match(/data-rule-id="1"[\s\S]*?<div class="rule-actions">([\s\S]*?)<details class="rule-clarify"/)[1];
assert.equal((proposedActions.match(/class="blockbtn"/g) || []).length, 1);
assert.ok(proposedActions.includes('data-rule-action="accept"'));
for (const action of ["edit", "retire", "history"]) assert.ok(proposedActions.includes(`class="rule-text-button" data-rule-action="${action}"`));
assert.ok(html.includes('<summary class="rule-text-button">Help me clarify</summary>'));
assert.ok(!html.includes('<span class="pill">0</span>'));
assert.ok(html.includes('<span class="pill you">3</span>'));
assert.ok(!html.includes('>New rule<') && html.includes('id="rule-new" hidden'));
const readonly = rulesView(payload, { owner: false });
for (const action of ["accept", "retire", "restore", "edit", "accept_agent", "keep_edit", "suggestion-accept", "suggestion-dismiss"]) assert.ok(!readonly.includes(`data-rule-action="${action}"`));
window.commonplaceDesktop = { version: 1, ask() {} };
assert.equal(desktopBridge(), window.commonplaceDesktop);
assert.ok(rulesView(payload).includes("Ask an agent"));
assert.ok(!rulesView(payload, { owner: false }).includes("Ask an agent"));
assert.ok(rulesView().includes("No rules"));

// Requests carry the displayed version, including when a newer payload arrives later.
const handlers = {}, errors = [], requests = [];
let refreshes = 0, fail = false;
const root = { addEventListener: (type, fn) => { handlers[type] = fn; }, querySelector: () => null };
const ui = rulesHandlers(root, async (path, options) => { requests.push([path, options]); if (fail) throw Object.assign(new Error("Stale"), { status: 409 }); return {}; }, async () => { refreshes++; }, (text) => errors.push(text));
ui.update(payload, { owner: true });
const row = { dataset: { ruleId: "1", version: "2" }, querySelectorAll: () => [] };
const control = { dataset: { ruleAction: "accept" }, closest: (selector) => selector === "[data-rule-id]" ? row : null };
const event = { target: { closest: () => control } };
await handlers.click(event);
assert.deepEqual(requests[0], ["/api/rules/1/action", { method: "POST", body: { action: "accept", version: 2 } }]);
assert.equal(refreshes, 1);
fail = true;
await handlers.click(event);
assert.equal(refreshes, 2);
assert.deepEqual(errors, ["Rule changed. Review it and try again."]);
ui.update(payload, { owner: false });
await handlers.click(event);
assert.equal(requests.length, 2);
console.log("Rules render and request checks passed");

// The header control toggles the inline form and obeys owner permissions.
let focused = false, reset = false;
const scopeInput = { required: true }, scopeField = { hidden: false };
const newPanel = { hidden: true, querySelector: (selector) => selector === "textarea" ? { focus() { focused = true; } } : selector === "form" ? { reset() { reset = true; } } : selector === "[data-scope-ref]" ? scopeField : scopeInput };
const newButton = { dataset: { ruleAction: "new" }, closest: () => null, setAttribute: (key, value) => { newButton[key] = value; }, focus() { focused = true; } };
root.querySelector = (selector) => selector === ".rule-new" ? newPanel : selector.includes('data-rule-action="new"') ? newButton : null;
const newEvent = { target: { closest: () => newButton } };
await handlers.click(newEvent);
assert.equal(newPanel.hidden, true);
ui.update(payload, { owner: true });
await handlers.click(newEvent);
assert.equal(newPanel.hidden, false);
assert.equal(newButton["aria-expanded"], "true");
assert.equal(focused, true);
const cancelButton = { dataset: { ruleAction: "cancel-new" }, closest: (selector) => selector === ".rule-new" ? newPanel : null };
await handlers.click({ target: { closest: () => cancelButton } });
assert.equal(newPanel.hidden, true);
assert.equal(newButton["aria-expanded"], "false");
assert.equal(reset, true);
assert.equal(scopeField.hidden, true);
assert.equal(scopeInput.required, false);
root.querySelector = () => null;

// All owner actions use the same versioned endpoint; suggestion decisions use their own.
fail = false;
ui.update(payload, { owner: true });
for (const action of ["retire", "restore", "accept_agent", "keep_edit"]) {
  control.dataset.ruleAction = action;
  await handlers.click(event);
  assert.deepEqual(requests.at(-1)[1].body, { action, version: 2 });
}
control.closest = (selector) => selector === "[data-rule-id]" ? row : selector === "[data-suggestion-id]" ? { dataset: { suggestionId: "8" } } : null;
for (const action of ["accept", "dismiss"]) {
  control.dataset.ruleAction = `suggestion-${action}`;
  await handlers.click(event);
  assert.deepEqual(requests.at(-1), ["/api/rules/suggestions/8", { method: "POST", body: { action, ...(action === "accept" ? { version: 2 } : {}) } }]);
}

// Bridge results are stored as proposals, and an unstructured set reply is kept in place.
const bridgeHandlers = {}, bridgePosts = [], bridgeCalls = [], bridgeErrors = [];
let bridgeRefreshes = 0, replyText = "A clearer instruction.";
window.commonplaceDesktop = {
  version: 1,
  ask: async (request) => { bridgeCalls.push(request); return { text: replyText }; },
};
const reply = { textContent: "", innerHTML: "" };
const select = { value: "reader-id", selectedOptions: [{ textContent: "reader" }] };
const panel = { dataset: { clarifyRule: "1" }, querySelectorAll: () => [], querySelector: (selector) => selector === "[data-ask-agent]" ? select : reply };
const askButton = { dataset: { ruleAction: "ask" }, closest: (selector) => selector === ".rule-clarify" ? panel : null };
const bridgeRoot = { addEventListener: (type, fn) => { bridgeHandlers[type] = fn; }, querySelector: () => null };
const bridgeUI = rulesHandlers(bridgeRoot, async (path, options) => { bridgePosts.push([path, options]); }, async () => { bridgeRefreshes++; }, (text) => bridgeErrors.push(text));
bridgeUI.update(payload);
const askEvent = { target: { closest: () => askButton } };
await bridgeHandlers.click(askEvent);
assert.deepEqual(bridgeCalls[0], { agent: "reader-id", prompt: clarifyPrompt(first), purpose: "rule-clarify" });
assert.deepEqual(bridgePosts[0], ["/api/rules/1/suggestions", { method: "POST", body: { text: replyText, from_agent: "reader" } }]);
assert.equal(bridgeRefreshes, 1);
panel.dataset = { clarifyAgent: "conn:writer" };
replyText = "rule-1: Clear first rule\nrule-2: Clear second rule\nrule-3: Retired\nunknown: Ignore";
await bridgeHandlers.click(askEvent);
assert.equal(bridgePosts.length, 3);
assert.equal(bridgeRefreshes, 2);
replyText = '<script>Unstructured reply</script>';
await bridgeHandlers.click(askEvent);
assert.equal(bridgePosts.length, 3);
assert.equal(bridgeRefreshes, 2);
assert.ok(reply.innerHTML.includes("&lt;script&gt;") && !reply.innerHTML.includes("<script>"));
assert.ok(reply.innerHTML.includes('data-rule-action="copy-reply"'));
window.commonplaceDesktop.ask = async () => { throw new Error("Agent unavailable"); };
await bridgeHandlers.click(askEvent);
assert.deepEqual(bridgeErrors, ["Agent unavailable"]);
assert.equal(reply.textContent, "");
assert.equal(bridgePosts.length, 3);
console.log("Rules actions and bridge checks passed");

// Owner-retired proposals remain outside the collapsed retired list.
const retiredProposal = rule(9, { status: "retired", retired_by: "owner", pending: 1, agent_text: "New proposal" });
const retiredHTML = rulesView({ rules: [retired, retiredProposal], waiting: 1, waiting_by_agent: { "conn:writer": 1 } });
assert.ok(retiredHTML.indexOf('data-rule-id="9"') < retiredHTML.indexOf('<details class="rule-retired">'));
assert.ok(retiredHTML.indexOf('data-rule-id="3"') > retiredHTML.indexOf('<details class="rule-retired">'));
assert.ok(retiredHTML.includes('data-waiting="1"'));
assert.equal(waitingCount([retired, retiredProposal]), 1);
assert.equal(waitingCount([rule(10, { status: "retired", retired_by: "agent", pending: 1 })]), 0);
// Counts and sorting are supplied by the server, independent of rule text.
const serverCounts = rulesView({ ...payload, waiting: 8, waiting_by_agent: { "*": 8 } });
assert.ok(serverCounts.includes('data-waiting="8"'));
assert.ok(serverCounts.indexOf('data-rule-agent="*"') < serverCounts.indexOf('data-rule-agent="conn:writer"'));
assert.ok(!readonly.includes("New rule") && readonly.includes("Copy prompt"));

// Suggestion conflicts use the same refresh-and-review path as rule writes.
control.dataset.ruleAction = "suggestion-accept";
fail = true;
const previousRefreshes = refreshes;
await handlers.click(event);
assert.equal(refreshes, previousRefreshes + 1);
assert.equal(errors.at(-1), "Rule changed. Review it and try again.");

// Exercise the shell refresh itself: other screens request only the count;
// rule events update the badge immediately and defer the screen while busy.
const shellSource = await read("app.js");
const refreshSource = shellSource.slice(shellSource.indexOf("async function refresh("), shellSource.indexOf("async function route()"));
const shellState = { me: {}, screen: 1, refreshRequest: 0, base: "#/rules", route: "#/rules" };
const shellCalls = [], updates = [], badgeCounts = [], shellErrors = [];
const content = { innerHTML: "", children: [{}] };
let shellPayload = { ...payload, can_edit: false };
const noWork = () => {};
let headerActions = "", shellBusy = true, shellRefresh;
const shellHandlers = {};
const shellUI = rulesHandlers({
  addEventListener: (type, fn) => { shellHandlers[type] = fn; },
  querySelector: () => shellBusy ? {} : null,
}, async () => {}, () => shellRefresh(), (error) => shellErrors.push(error));
const updateShellUI = shellUI.update;
shellUI.update = (...args) => { updates.push(args); updateShellUI(...args); };
const shellDeps = {
  state: shellState,
  api: async (path) => {
    shellCalls.push(path);
    if (path === "/api/rules") return shellPayload;
    if (path === "/api/rules/count") return { waiting: 7, seq: 12 };
    return { boards: [] };
  },
  root: { querySelector: () => content },
  rulesUI: shellUI,
  rulesView,
  navigation: () => badgeCounts.push(shellState.rulesWaiting),
  title: (_text, actions = "") => { headerActions = actions; }, remember: noWork, capture: () => noWork, restore: noWork,
  boardsView: noWork, showError: (error) => { if (error) shellErrors.push(error); },
};
shellRefresh = new Function(...Object.keys(shellDeps), `${refreshSource}; return refresh;`)(...Object.values(shellDeps));
assert.equal(await shellRefresh(), true);
assert.deepEqual(updates.at(-1)[1], { owner: false });
assert.ok(!content.innerHTML.includes("New rule") && !content.innerHTML.includes("Ask an agent"));
assert.ok(content.innerHTML.includes("Copy prompt"));
assert.equal(headerActions, "");
assert.ok(shellCalls.includes("/api/rules") && !shellCalls.includes("/api/rules/count"));
shellPayload = { ...shellPayload, can_edit: true, waiting: 4 };
const busyHTML = content.innerHTML;
for (const kind of ["rule.changed", "rule.suggestion"]) {
  assert.equal(await shellRefresh({ background: true, events: [{ kind }] }), true);
  assert.deepEqual(updates.at(-1)[1], { owner: false });
  assert.equal(content.innerHTML, busyHTML);
  assert.equal(shellDeps.rulesUI.stale, true);
  assert.equal(badgeCounts.at(-1), 4);
}
shellBusy = false;
await shellHandlers.toggle({ target: { matches: () => true, open: false } });
assert.deepEqual(updates.at(-1)[1], { owner: true });
assert.equal(shellDeps.rulesUI.stale, false);
assert.notEqual(content.innerHTML, busyHTML);
assert.ok(headerActions.includes('data-rule-action="new"'));
// A finished write renders even when a background refresh overtakes its own
// refresh while the edit form is still open; the force is used up by that render.
shellBusy = true;
shellPayload = { ...shellPayload, waiting: 2 };
const beforeRace = updates.length;
shellUI.force = true;
const own = shellRefresh();
const overtaking = shellRefresh({ background: true, events: [{ kind: "rule.changed" }] });
await Promise.all([own, overtaking]);
assert.equal(updates.length, beforeRace + 1);
assert.equal(shellUI.force, false);
assert.equal(shellUI.stale, false);
const afterRace = content.innerHTML;
shellPayload = { ...shellPayload, waiting: 3 };
assert.equal(await shellRefresh({ background: true, events: [{ kind: "rule.changed" }] }), true);
assert.equal(updates.length, beforeRace + 1);
assert.equal(content.innerHTML, afterRace);
assert.equal(shellUI.stale, true);
shellBusy = false;
shellState.base = shellState.route = "#/boards";
shellCalls.length = 0;
assert.equal(await shellRefresh({ background: true, events: [{ kind: "rule.changed" }] }), true);
assert.ok(shellCalls.includes("/api/rules/count") && !shellCalls.includes("/api/rules"));
assert.equal(badgeCounts.at(-1), 7);
assert.deepEqual(shellErrors, []);
console.log("Rules permissions, retirement, counts and shell refresh checks passed");


// Outdated and retired suggestions stay visible with Dismiss as their only action.
for (const [current, baseVersion, outdated] of [
  [first, first.version, false],
  [first, first.version - 1, true],
  [retired, retired.version, false],
  [retired, retired.version - 1, true],
  [retiredProposal, retiredProposal.version, false],
]) {
  const view = rulesView({ rules: [current], suggestions: [
    { id: 20, rule_id: current.id, base_version: baseVersion, text: "Suggestion", from_agent: "reader", status: "open" },
    { id: 21, rule_id: current.id, base_version: baseVersion, text: "Dismissed", from_agent: "reader", status: "dismissed" },
  ] });
  const proposal = view.match(/<section class="rule-proposal" data-suggestion-id="20">([\s\S]*?)<\/section>/)[1];
  assert.equal(proposal.includes('<span class="pill">outdated</span>'), outdated);
  assert.equal(proposal.includes('data-rule-action="suggestion-accept"'), !outdated && current.status !== "retired");
  assert.ok(proposal.includes('data-rule-action="suggestion-dismiss"'));
  assert.ok(!view.includes('data-suggestion-id="21"'));
}

// Each busy-state exit releases a deferred render, including a failed mutation.
const exitHandlers = {};
let exitBusy = false, exitRefreshes = 0;
const exitTrigger = { setAttribute() {}, focus() {} };
const exitPanel = { hidden: false, querySelector: (selector) => selector === "form" ? { reset() {} } : {} };
const exitHistory = { hidden: false };
const exitRow = { dataset: { ruleId: "1", version: "3" }, querySelector: () => exitHistory, querySelectorAll: () => [] };
const exitRoot = {
  addEventListener: (type, fn) => { exitHandlers[type] = fn; },
  querySelector: (selector) => selector === '[data-rule-action="new"]' ? exitTrigger : exitBusy ? {} : null,
};
let exitUI;
exitUI = rulesHandlers(exitRoot, async () => { throw new Error("Write failed"); }, async () => { exitRefreshes++; exitUI.update(payload); }, () => {});
exitUI.update(payload);
const exitClick = async (action) => {
  const button = { dataset: { ruleAction: action }, closest: (selector) => selector === ".rule-new" ? exitPanel : selector === "[data-rule-id]" ? exitRow : null };
  await exitHandlers.click({ target: { closest: () => button } });
};
for (const action of ["cancel-new", "cancel-edit", "history", "retire"]) {
  exitUI.stale = true;
  const before = exitRefreshes;
  await exitClick(action);
  assert.equal(exitRefreshes, before + 1, action);
  assert.equal(exitUI.stale, false);
}
for (const open of [true, false]) {
  exitUI.stale = true;
  const before = exitRefreshes;
  await exitHandlers.toggle({ target: { matches: () => true, open } });
  assert.equal(exitRefreshes, before + (open ? 0 : 1));
}
exitUI.stale = true;
exitBusy = true;
await exitHandlers.toggle({ target: { matches: () => true, open: false } });
assert.equal(exitUI.stale, true);
exitBusy = false;
await exitHandlers.toggle({ target: { matches: () => true, open: false } });
assert.equal(exitUI.stale, false);
for (const selector of ['.rule-clarify[open] [data-ask-agent]', '.rule-clarify[open] [data-reply-text]']) {
  exitRoot.querySelector = (query) => query.includes(selector) ? {} : null;
  assert.equal(exitUI.busy(), true);
}
console.log("Rules suggestion and deferred-render checks passed");

// Owners move active rules between agents; proposals can be accepted for all agents at once.
{
  const moveHTML = rulesView(payload);
  const row = (id) => moveHTML.match(new RegExp(`data-rule-id="${id}"[\\s\\S]*?<div data-rule-history hidden></div></article>`))[0];
  const targets = (id) => [...row(id).matchAll(/data-rule-action="move" data-agent="([^"]+)">([^<]+)</g)].map((m) => [m[1], m[2]]);
  assert.deepEqual(targets(1), [["*", "All agents"], ["conn:reader", "reader"]]);
  assert.deepEqual(targets(5), [["conn:reader", "reader"], ["conn:writer", "writer"]]);
  assert.ok(row(1).includes('<details class="rule-move"><summary class="rule-text-button">Applies to…</summary>'));
  assert.ok(row(1).includes('class="rule-text-button" data-rule-action="accept-all" >Accept for all agents<'));
  for (const id of [4, 5]) assert.ok(!row(id).includes('data-rule-action="accept-all"'));
  assert.ok(!row(3).includes("rule-move"));
  assert.ok(!rulesView(payload, { owner: false }).includes("rule-move") && !rulesView(payload, { owner: false }).includes("accept-all"));
  assert.ok(!rulesView({ rules: [all] }).includes("rule-move"));

  const sent = [];
  let moveRefreshes = 0;
  const moveHandlers = {};
  const moveUI = rulesHandlers({ addEventListener: (type, fn) => { moveHandlers[type] = fn; }, querySelector: () => null }, async (path, options) => { sent.push([path, options]); return {}; }, async () => { moveRefreshes++; }, () => {});
  moveUI.update(payload, { owner: true });
  const moveRow = { dataset: { ruleId: "1", version: "3" }, querySelectorAll: () => [] };
  const click = (dataset) => moveHandlers.click({ target: { closest: () => ({ dataset, closest: (selector) => selector === "[data-rule-id]" ? moveRow : null }) } });
  await click({ ruleAction: "move", agent: "*" });
  await click({ ruleAction: "accept-all" });
  assert.deepEqual(sent, [
    ["/api/rules/1/agent", { method: "POST", body: { agent: "*", version: 3 } }],
    ["/api/rules/1/agent", { method: "POST", body: { agent: "*", version: 3, accept: true } }],
  ]);
  assert.equal(moveRefreshes, 2);
  moveUI.update(payload, { owner: false });
  await click({ ruleAction: "move", agent: "*" });
  assert.equal(sent.length, 2);
}
console.log("Rules agent-target checks passed");
