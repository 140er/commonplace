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
const payload = { rules: [first, board, retired, pending, all], suggestions: [{ id: 8, rule_id: 1, text: "A clearer rule", from_agent: "reader" }], agents: ["writer", "reader"] };
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
const readonly = rulesView(payload, { owner: false });
for (const action of ["accept", "retire", "restore", "edit", "accept_agent", "suggestion-accept"]) assert.ok(!readonly.includes(`data-rule-action="${action}"`));
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
  assert.deepEqual(requests.at(-1), ["/api/rules/suggestions/8", { method: "POST", body: { action } }]);
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
