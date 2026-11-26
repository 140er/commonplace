// Coordinator status block of Project settings (web/static/orchstatus.js). Run: node tests/orchstatus.mjs <case>
import assert from "node:assert/strict";

const ui = await import(new URL("../commonplace/web/static/orchstatus.js", import.meta.url));
const e = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const NOW = Date.parse("2030-01-01T12:00:00Z");
const iso = seconds => new Date(NOW + seconds * 1000).toISOString().replace(".000", "");
const owner = {board: "atlas", name: "Atlas", can_configure: true, orchestration: {default_max_agents: 3}};
const member = {...owner, can_configure: false};
const ACK = "effects after 2030-01-01T00:00:00Z may be unknown";
const pending = (extra = {}) => ({restore_pending: true, after: "2030-01-01T00:00:00Z", ack_text: ACK, restored_at: iso(-600), acked_by: null, acked_at: null, ...extra});
const calm = (extra = {}) => ({restore_pending: false, after: null, ack_text: null, restored_at: null, acked_by: null, acked_at: null, ...extra});
const finding = (id, extra = {}) => ({id, board: "atlas", card: 12, reporter_role: "reviewer", step_key: `f${id}`, source: "review", detail: "Missing test", owner_role: "builder", state: "routed", reason: null, created_at: iso(-300), destination: {kind: "session", board: "atlas", role: "builder"}, ...extra});
const step = (key, kind, state, extra = {}) => ({step_key: key, kind, state, card: null, detail: null, voided: false, ...extra});
const view = (extra = {}) => ({
  board: "atlas", role: "main", config: {version: 3, sha256: "x"}, open: [], in_doubt: [], needs_person: [], unstarted: [], findings: [],
  lease: {epoch: 2, holder: "conn:builder-bot", expires_at: iso(180), live: true}, ...extra,
});

// A fake DOM: only what the module touches. Controls are found by their data-* attribute in the markup.
class Host {
  isConnected = true; hidden = false; attrs = {}; html = ""; nodes = []; nextElementSibling = null;
  setAttribute(name, value) { this.attrs[name] = value; }
  hasAttribute(name) { return name in this.attrs; }
  set innerHTML(html) {
    this.html = html;
    this.nodes = [...html.matchAll(/<(button|p|span|details|div|input|code)\b([^>]*)>/g)].map(([, tag, attrs]) => ({
      tag, attributes: Object.fromEntries([...attrs.matchAll(/([\w-]+)(?:="([^"]*)")?/g)].map(m => [m[1], m[2] ?? ""])),
      disabled: /\bdisabled\b/.test(attrs),
    }));
  }
  get innerHTML() { return this.html; }
  replaceChildren() { this.html = ""; this.nodes = []; }
  querySelector(selector) {
    const name = selector.match(/^\[([\w-]+)\]$/)?.[1];
    assert.ok(name, `unsupported selector ${selector}`);
    return this.nodes.find(n => name in n.attributes) || null;
  }
  get text() { return this.html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim(); }
}
class Anchor {
  nextElementSibling = null;
  ownerDocument = {createElement: () => new Host()};
  after(node) { this.nextElementSibling = node; }
}
const click = async (host, name) => {
  const node = host.querySelector(`[data-orch-${name}]`);
  assert.ok(node, `data-orch-${name} missing`);
  assert.equal(node.disabled, false, `data-orch-${name} disabled`);
  await node.onclick();
};
const has = (host, name) => host.querySelector(`[data-orch-${name}]`) !== null;

/** `responses`: the checkpoint answers in order (the last one repeats); an Error is thrown as is.
 *  `restore`: the restore-status answers in order (the last repeats); an Error is thrown as is. */
async function mount({project = owner, responses = [view()], restore = [calm()], post = {epoch: 2, released: true}} = {}) {
  const anchor = new Anchor(), calls = [], restoreReads = [];
  let reads = 0, restoreAt = 0;
  const api = async (path, options = {}) => {
    if (options.method === "POST") {
      calls.push({path, method: "POST", body: options.body});
      if (post instanceof Error) throw post;
      return typeof post === "function" ? post(path) : post;
    }
    if (path === "/api/orch/restore") {
      restoreReads.push(path);
      const next = restore[Math.min(restoreAt++, restore.length - 1)];
      if (next instanceof Error) throw next;
      return next;
    }
    calls.push({path, method: "GET"});
    const next = responses[Math.min(reads++, responses.length - 1)];
    if (next instanceof Error) throw next;
    return next;
  };
  await ui.mountOrchStatus(anchor, {project, api, e, now: () => NOW});
  return {anchor, host: anchor.nextElementSibling, calls, restoreReads};
}
const type = (host, text) => {
  const node = host.querySelector("[data-orch-restore-text]");
  assert.ok(node, "data-orch-restore-text missing");
  node.value = text;
  node.oninput({target: node});
};
const refusalWith = (status, code) => Object.assign(new Error("refused"), {status, payload: {error: "no: refused", code}});
const refusal = status => Object.assign(new Error("refused"), {status, payload: {error: "no"}});
const posts = calls => calls.filter(c => c.method === "POST");

const cases = {
  words() {
    assert.equal(ui.when(iso(-180), NOW), "3 min ago");
    assert.equal(ui.when(iso(120), NOW), "in 2 min");
    assert.equal(ui.when(iso(-2), NOW), "just now");
    assert.equal(ui.when("not a time", NOW), "");
    assert.equal(ui.leaseSentence(null, NOW), "Nobody is coordinating this project.");
  },
  async live_lease() {
    const {host, calls} = await mount({responses: [view({open: [step("m1", "merge", "started")], in_doubt: [step("t1", "thread", "in_doubt", {card: 7})], unstarted: [{id: 1}, {id: 2}]})]});
    assert.equal(calls[0].path, "/api/orch/boards/atlas/roles/main/checkpoint");
    assert.match(host.text, /Held by builder-bot\. The lease runs out in 3 min unless the agent renews it\./);
    assert.match(host.text, /1 step open · 1 in doubt · 2 Todo cards not started\. Accepted config: version 3\./);
    assert.match(host.text, /thread for card #7/);
    assert.ok(has(host, "end"));
    assert.equal(has(host, "stays"), false);
    assert.equal(posts(calls).length, 0);
  },
  async expired_lease() {
    const {host} = await mount({responses: [view({lease: {epoch: 2, holder: "conn:builder-bot", expires_at: iso(-180), live: false}, open: [step("m1", "merge", "started")]})]});
    assert.match(host.text, /Nobody is coordinating this project\. builder-bot held the lease until it ran out 3 min ago\./);
    assert.equal(has(host, "end"), false);
    assert.ok(has(host, "stays"));
  },
  async released_lease() {
    const {host} = await mount({responses: [view({lease: {epoch: 2, holder: "conn:builder-bot", expires_at: iso(180), live: false}})]});
    assert.match(host.text, /Nobody is coordinating this project\. builder-bot gave the lease up\./);
    assert.equal(has(host, "end"), false);
  },
  async no_lease() {
    const {host} = await mount({responses: [view({lease: null, config: null})]});
    assert.match(host.text, /Nobody is coordinating this project\./);
    assert.match(host.text, /No Coworker config is accepted yet\./);
    assert.equal(has(host, "end"), false);
  },
  async member_sees_state_without_button() {
    const {host, calls} = await mount({project: member});
    assert.match(host.text, /Held by builder-bot/);
    assert.equal(has(host, "end"), false);
    assert.equal(has(host, "refresh"), true);
    assert.equal(posts(calls).length, 0);
  },
  async no_orchestration_shows_nothing() {
    const {anchor, calls} = await mount({project: {...owner, orchestration: undefined}});
    assert.equal(anchor.nextElementSibling, null);
    assert.equal(calls.length, 0);
  },
  async unreadable_board_hides_the_block() {
    for (const status of [403, 404]) {
      const {host} = await mount({responses: [refusal(status)]});
      assert.equal(host.hidden, true);
      assert.equal(host.html, "");
    }
  },
  async failed_read_says_so() {
    const {host} = await mount({responses: [refusal(500)]});
    assert.equal(host.text, "Could not load the coordinator status.");
  },
  async end_needs_a_confirm() {
    const {host, calls} = await mount();
    await click(host, "end");
    assert.ok(has(host, "confirm"));
    assert.match(host.text, /Stop builder-bot\?/);
    assert.match(host.text, /keeps running and stays listed below/);
    assert.equal(posts(calls).length, 0);
    await click(host, "end-cancel");
    assert.equal(has(host, "confirm"), false);
    assert.ok(has(host, "end"));
    assert.equal(posts(calls).length, 0);
  },
  async end_lease_leaves_unfinished_work_listed() {
    const work = {open: [step("m1", "merge", "started")], in_doubt: [step("t1", "thread", "in_doubt", {detail: "builder running"})]};
    const ended = view({...work, lease: {epoch: 2, holder: "conn:builder-bot", expires_at: iso(180), live: false}});
    const {host, calls} = await mount({responses: [view(work), ended]});
    await click(host, "end");
    await click(host, "end-confirm");
    const sent = posts(calls);
    assert.equal(sent.length, 1);
    assert.equal(sent[0].path, "/api/orch/boards/atlas/roles/main/force-release");
    assert.deepEqual(sent[0].body, {});
    assert.match(host.text, /Nobody is coordinating this project\./);
    assert.match(host.text, /Lease ended\. Unfinished work stays listed below/);
    assert.match(host.text, /1 step open · 1 in doubt/);
    assert.match(host.text, /thread t1 — builder running/);
    assert.equal(has(host, "end"), false);
    assert.ok(has(host, "stays"));
  },
  async end_lease_once() {
    const {host, calls} = await mount();
    await click(host, "end");
    const node = host.querySelector("[data-orch-end-confirm]");
    const first = node.onclick();
    await node.onclick();
    await first;
    assert.equal(posts(calls).length, 1);
  },
  async nothing_to_end() {
    const {host} = await mount({responses: [view(), view({lease: {epoch: 2, holder: "conn:builder-bot", expires_at: iso(-5), live: false}})], post: {epoch: 2, released: false}});
    await click(host, "end");
    await click(host, "end-confirm");
    assert.match(host.text, /The lease had already ended\./);
  },
  async refusal_is_shown_and_the_lease_stays() {
    for (const [status, text] of [[403, "Only the workspace owner can end a lease."], [404, "This project is no longer available."], [500, "Could not end the lease. Nothing was changed. Try again."]]) {
      const {host} = await mount({post: refusal(status)});
      await click(host, "end");
      await click(host, "end-confirm");
      assert.match(host.text, new RegExp(text.replace(/\./g, "\\.")));
      assert.match(host.text, /Held by builder-bot/);
      assert.ok(has(host, "end"));
    }
  },
  async refresh_rereads() {
    const {host, calls} = await mount({responses: [view(), view({lease: null})]});
    await click(host, "refresh");
    assert.equal(calls.filter(c => c.method === "GET").length, 2);
    assert.match(host.text, /Nobody is coordinating this project\./);
  },
  async names_are_escaped() {
    const {host} = await mount({responses: [view({lease: {epoch: 1, holder: "conn:<img src=x onerror=alert(1)>", expires_at: iso(60), live: true}, in_doubt: [step("k", "thread", "in_doubt", {detail: "<b>x</b>"})]})]});
    assert.equal(host.html.includes("<img"), false);
    assert.equal(host.html.includes("<b>x"), false);
    assert.ok(host.html.includes("&lt;img"));
  },
  async merge_policy_is_listed_with_the_config() {
    const factory = {policy: {gate: {required: true}}, summary: ["Gate: required. Only a pass verdict counts; fail, error and budget never do.", "Check <b>unit</b> (affected tests): required."], gates: {open: 1, gate_pass: 4, gate_fail: 2, gate_error: 0, gate_budget: 0}};
    const {host} = await mount({responses: [view({factory})]});
    assert.ok(has(host, "factory"));
    assert.match(host.text, /Accepted config: version 3\. Merge policy Gate: required\. Only a pass verdict counts/);
    assert.match(host.text, /Gates on this board: 4 passed, 2 failed, 0 with an error, 0 out of budget, 1 open\./);
    assert.equal(host.html.includes("<b>unit"), false);
  },
  async no_merge_policy_shows_no_block() {
    const {host} = await mount();
    assert.equal(has(host, "factory"), false);
    assert.equal(host.text.includes("Merge policy"), false);
    assert.equal(ui.factoryHTML(undefined, e), "");
  },
  // ---- G20: restore state, findings, steps (read-only first), and the one safe action: acknowledge a restore
  async restore_pending_is_explained_before_any_action() {
    const {host, calls, restoreReads} = await mount({restore: [pending()]});
    assert.equal(restoreReads.length, 1);
    assert.match(host.text, /This data was restored from a backup taken at 2030-01-01T00:00:00Z\./);
    assert.match(host.text, /Until the owner acknowledges it, no Coworker can claim a lease or start work on any project\./);
    assert.ok(has(host, "restore"));
    assert.equal(posts(calls).length, 0);
    assert.equal(host.html.includes("data-orch-restore-confirm"), false);
  },
  async restore_ack_preview_names_the_effect_and_what_it_does_not_do() {
    const {host, calls} = await mount({restore: [pending()]});
    await click(host, "restore");
    assert.ok(has(host, "restore-confirm"));
    assert.match(host.text, /Every project with a Coworker config starts over from its Todo cards/);
    assert.match(host.text, /does not claim a lease, start a step or send a heartbeat/);
    assert.match(host.text, /Work that was running at the time of the backup may have run twice or not at all/);
    assert.ok(host.html.includes(ACK));
    assert.equal(posts(calls).length, 0);
  },
  async restore_ack_waits_for_the_exact_text() {
    const {host, calls} = await mount({restore: [pending()]});
    await click(host, "restore");
    assert.equal(host.querySelector("[data-orch-restore-confirm]").disabled, true);
    type(host, "effects after 2030-01-01T00:00:00Z");
    assert.equal(host.querySelector("[data-orch-restore-confirm]").disabled, true);
    type(host, ACK);
    assert.equal(host.querySelector("[data-orch-restore-confirm]").disabled, false);
    type(host, ACK + " ");
    assert.equal(host.querySelector("[data-orch-restore-confirm]").disabled, true);
    assert.equal(posts(calls).length, 0);
  },
  async restore_ack_cancel_sends_nothing_and_keeps_the_state() {
    const {host, calls} = await mount({restore: [pending()]});
    await click(host, "restore");
    type(host, ACK);
    await click(host, "restore-cancel");
    assert.equal(has(host, "restore-confirm"), false);
    assert.ok(has(host, "restore"));
    assert.equal(posts(calls).length, 0);
    assert.match(host.text, /This data was restored from a backup/);
    await click(host, "restore");
    assert.equal(host.querySelector("[data-orch-restore-confirm]").disabled, true, "typed text is not kept after a cancel");
  },
  async restore_ack_sends_the_text_once_and_shows_the_result() {
    const arming = [{board: "atlas", gen: 2, armed: true}, {board: "bay", gen: 1, armed: false}];
    const {host, calls} = await mount({restore: [pending(), calm({acked_by: "conn:owner", acked_at: iso(-1)})], post: {acknowledged: true, arming}});
    await click(host, "restore");
    type(host, ACK);
    const node = host.querySelector("[data-orch-restore-confirm]");
    const first = node.onclick();
    await node.onclick();
    await first;
    const sent = posts(calls);
    assert.equal(sent.length, 1);
    assert.equal(sent[0].path, "/api/orch/restore/ack");
    assert.deepEqual(sent[0].body, {text: ACK});
    assert.match(host.text, /Restore acknowledged\. 2 projects will re-check their Todo cards\. No lease was claimed and nothing was started\./);
    assert.match(host.text, /No restore is waiting\./);
    assert.match(host.text, /Last restore acknowledged by owner just now\./);
    assert.equal(has(host, "restore"), false);
    assert.equal(sent.every(c => /restore\/ack$/.test(c.path)), true, "never resume, heartbeat or release");
  },
  async restore_ack_refused_keeps_the_pending_state_and_says_so() {
    const table = [
      [refusalWith(403, "owner_only"), /Only the workspace owner can acknowledge a restore\. Nothing was changed\./],
      [refusalWith(403, "restore_text"), /That text does not match the one shown here\. Nothing was changed\./],
      [refusalWith(409, "restore_not_pending"), /This restore was already acknowledged\./],
      [refusalWith(500), /Could not acknowledge the restore\. Nothing was changed\. Try again\./],
    ];
    for (const [post, text] of table) {
      const after = post.payload.code === "restore_not_pending" ? calm({acked_by: "conn:other", acked_at: iso(-30)}) : pending();
      const {host, calls} = await mount({restore: [pending(), after], post});
      await click(host, "restore");
      type(host, ACK);
      await click(host, "restore-confirm");
      assert.match(host.text, text);
      assert.equal(posts(calls).length, 1);
      if (post.payload.code === "restore_not_pending") assert.match(host.text, /No restore is waiting\./);
      else {
        assert.match(host.text, /This data was restored from a backup taken at 2030-01-01T00:00:00Z\./);
        assert.ok(has(host, "restore-text"), "the owner can try again with the preview still open");
      }
    }
  },
  async restore_pending_member_sees_it_without_the_action() {
    const {host, calls} = await mount({project: member, restore: [pending()]});
    assert.match(host.text, /This data was restored from a backup taken at/);
    assert.match(host.text, /Only the workspace owner can acknowledge it\./);
    assert.equal(has(host, "restore"), false);
    assert.equal(host.html.includes("data-orch-restore-text"), false);
    assert.equal(posts(calls).length, 0);
  },
  async restore_status_unreadable_is_not_an_empty_all_clear() {
    const {host, calls} = await mount({restore: [refusalWith(500)]});
    assert.match(host.text, /Could not read the restore status\. Nothing here says whether a restore is waiting\./);
    assert.equal(has(host, "restore"), false);
    assert.equal(host.text.includes("No restore is waiting"), false);
    assert.match(host.text, /Held by builder-bot/, "the rest of the block still renders");
    assert.equal(posts(calls).length, 0);
  },
  async restore_status_with_an_unknown_shape_says_so() {
    const {host} = await mount({restore: [{surprise: true}]});
    assert.match(host.text, /Could not read the restore status\./);
    assert.equal(host.text.includes("No restore is waiting"), false);
  },
  async restore_calm_names_the_last_acknowledgement() {
    const {host} = await mount({restore: [calm({acked_by: "conn:owner", acked_at: iso(-7200)})]});
    assert.match(host.text, /No restore is waiting\. Last restore acknowledged by owner 2 h ago\./);
    const none = await mount({restore: [calm()]});
    assert.match(none.host.text, /No restore is waiting\. No restore has been recorded on this workspace\./);
  },
  async findings_show_card_destination_and_why_they_cannot_be_acknowledged_here() {
    const list = [
      finding(1),
      finding(2, {state: "parked", reason: "owner_session_gone", destination: {kind: "parked", reason: "owner_session_gone"}}),
      finding(3, {card: null, owner_role: null, state: "parked", reason: "no_owning_card", destination: {kind: "parked", reason: "no_owning_card"}}),
    ];
    const {host, calls} = await mount({responses: [view({findings: list, lease: null})]});
    assert.ok(has(host, "findings"));
    assert.match(host.text, /3 open findings/);
    assert.match(host.text, /Finding 1 · card #12 · Missing test/);
    assert.match(host.text, /Routed to the builder role\./);
    assert.match(host.text, /Parked: the role that owns this card has no live lease right now\./);
    assert.match(host.text, /Parked: this finding is not about a card, so no role owns it\./);
    assert.match(host.text, /Only the builder role can acknowledge it, with its own lease\./);
    assert.match(host.text, /Nobody can acknowledge this one because no role owns it\./);
    assert.equal(host.html.includes("data-orch-finding-ack"), false);
    assert.equal(posts(calls).length, 0);
  },
  async findings_unknown_reasons_and_states_are_spelled_out() {
    const list = [finding(4, {state: "parked", reason: "brand_new", destination: {kind: "parked", reason: "brand_new"}}), finding(5, {state: "weird", destination: {kind: "session", board: "atlas", role: "builder"}})];
    const {host} = await mount({responses: [view({findings: list})]});
    assert.match(host.text, /Parked for a reason this page does not know \(brand_new\)\./);
    assert.match(host.text, /State this page does not know \(weird\)\./);
  },
  async findings_none_unknown_and_capped_are_three_different_answers() {
    const none = await mount({responses: [view({findings: []})]});
    assert.match(none.host.text, /No open findings\./);
    const unknown = await mount({responses: [view({findings: undefined})]});
    assert.match(unknown.host.text, /Findings are not available from this server\./);
    assert.equal(unknown.host.text.includes("No open findings"), false);
    const many = Array.from({length: 200}, (_, i) => finding(i + 1));
    const capped = await mount({responses: [view({findings: many})]});
    assert.match(capped.host.text, /200\+ open findings/);
    assert.match(capped.host.text, /Only the newest are listed\./);
  },
  async findings_text_is_shown_verbatim_and_escaped() {
    const {host} = await mount({responses: [view({findings: [finding(6, {detail: "price is $& then $1 and $$ <b>x</b>", owner_role: "<i>b</i>"})]})]});
    assert.ok(host.html.includes("price is $&amp; then $1 and $$ &lt;b&gt;x&lt;/b&gt;"));
    assert.equal(host.html.includes("<b>x"), false);
    assert.equal(host.html.includes("<i>b"), false);
  },
  async steps_show_the_run_and_version_each_belongs_to() {
    const steps = {
      open: [step("m1", "merge", "started", {epoch: 2, created_epoch: 2, updated_at: iso(-120)})],
      in_doubt: [step("r1", "runner_start", "in_doubt", {card: 9, job_id: "job_77", epoch: 1, created_epoch: 1})],
      needs_person: [step("g1", "gate", "needs_person", {detail: "verdict lost"})],
      holds: [step("h1", "hold", "waiting", {detail: "waiting for the owner"})],
      finished: [step("d1", "thread", "done", {card: 4})],
      parked: [step("p1", "card", "failed", {destination: {kind: "parked", reason: "no_owning_card"}})],
      jobs: [{id: "job_77", state: "launched", step_key: "r1", task_number: 9}],
    };
    const {host, calls} = await mount({responses: [view(steps)]});
    assert.match(host.text, /Showing the main role on config version 3, lease epoch 2\./);
    assert.match(host.text, /runner_start for card #9 \(in doubt\)/);
    assert.match(host.text, /Run job_77 is launched\./);
    assert.match(host.text, /Started in lease epoch 1\./);
    assert.match(host.text, /Needs a person: the Coworker could not tell whether this took effect\./);
    assert.ok(has(host, "steps-open"));
    assert.match(host.text, /hold h1 — waiting for the owner/);
    assert.match(host.text, /thread for card #4 \(done\)/);
    assert.match(host.text, /Parked: this step is not about a card/);
    assert.equal(posts(calls).length, 0);
  },
  async steps_with_an_unknown_state_are_labelled_not_dropped() {
    const {host} = await mount({responses: [view({open: [step("x1", "merge", "mystery")]})]});
    assert.match(host.text, /merge x1 \(mystery\)/);
    assert.match(host.text, /State this page does not know \(mystery\)\./);
  },
  async no_steps_says_what_that_means() {
    const {host} = await mount({responses: [view({lease: null, config: null})]});
    assert.match(host.text, /No steps are recorded for the main role\./);
  },
  async only_the_safe_action_is_offered_and_the_rest_is_explained() {
    const rich = view({open: [step("m1", "merge", "started")], in_doubt: [step("t1", "thread", "in_doubt")], findings: [finding(1)]});
    const {host} = await mount({responses: [rich], restore: [pending()]});
    for (const name of ["void", "step-void", "reassign", "retire", "finding-ack", "resume", "heartbeat"]) assert.equal(has(host, name), false, name);
    assert.match(host.text, /Voiding a step, reassigning it and retiring an experiment are not offered on this page yet\. The owner does those through the Coworker tools\./);
    assert.ok(has(host, "end"), "End lease keeps its existing confirm");
    assert.ok(has(host, "restore"));
  },
  async member_gets_no_action_anywhere_and_no_calls_but_reads() {
    const {host, calls} = await mount({project: member, responses: [view({findings: [finding(1)], in_doubt: [step("t1", "thread", "in_doubt")]})], restore: [pending()]});
    for (const name of ["end", "restore", "restore-text", "finding-ack"]) assert.equal(has(host, name), false, name);
    assert.equal(posts(calls).length, 0);
  },
  async refresh_rereads_the_restore_status_too() {
    const {host, restoreReads} = await mount({restore: [pending(), calm()]});
    await click(host, "refresh");
    assert.equal(restoreReads.length, 2);
    assert.match(host.text, /No restore is waiting\./);
  },
  async stop_is_named_stop_and_confirms() {
    const {host} = await mount();
    assert.match(host.querySelector("[data-orch-end]") && host.text, /Stop Coworker…/);
    await click(host, "end");
    assert.match(host.text, /Stop Coworker/);
  },
  async resume_is_offered_to_the_owner_when_nobody_coordinates() {
    for (const lease of [null, {epoch: 2, holder: "conn:builder-bot", expires_at: iso(-180), live: false}]) {
      const {host} = await mount({responses: [view({lease})]});
      assert.ok(has(host, "resume"));
      assert.equal(has(host, "end"), false);
    }
    const live = await mount();
    assert.equal(has(live.host, "resume"), false);
    const other = await mount({project: member, responses: [view({lease: null})]});
    assert.equal(has(other.host, "resume"), false);
  },
  async resume_needs_a_confirm_then_posts_once_and_reloads() {
    const stopped = view({lease: {epoch: 2, holder: "conn:builder-bot", expires_at: iso(-5), live: false}});
    const {host, calls} = await mount({responses: [stopped, stopped], post: {epoch: 3, resumed: true}});
    await click(host, "resume");
    assert.ok(has(host, "resume-confirm"));
    assert.equal(posts(calls).length, 0);
    await click(host, "resume-cancel");
    assert.equal(has(host, "resume-confirm"), false);
    assert.equal(posts(calls).length, 0);
    await click(host, "resume");
    const node = host.querySelector("[data-orch-resume-confirm]");
    const first = node.onclick();
    await node.onclick();
    await first;
    const sent = posts(calls);
    assert.equal(sent.length, 1);
    assert.equal(sent[0].path, "/api/orch/boards/atlas/roles/main/owner-resume");
    assert.deepEqual(sent[0].body, {});
    assert.match(host.text, /Coworker resumed/);
    assert.equal(calls.filter(c => c.method === "GET").length, 2);
  },
  async resume_refusals_are_named_and_nothing_is_claimed() {
    const stopped = view({lease: null});
    for (const [error, text] of [[refusalWith(409, "held"), "A Coworker is already running"], [refusal(403), "Only the workspace owner can resume"], [refusal(404), "This project is no longer available."], [refusalWith(409, "restore_pending"), "Acknowledge the restore first"], [refusal(500), "Could not resume the Coworker. Nothing was changed. Try again."]]) {
      const {host} = await mount({responses: [stopped], post: error});
      await click(host, "resume");
      await click(host, "resume-confirm");
      assert.ok(host.text.includes(text), `${text} :: ${host.text}`);
      assert.ok(has(host, "resume"));
    }
  },
  async resume_never_shows_a_lease_token() {
    const {host} = await mount({responses: [view({lease: null})], post: {epoch: 3, resumed: true, token: "TOKEN-SENTINEL-1234"}});
    await click(host, "resume");
    await click(host, "resume-confirm");
    assert.equal(host.html.includes("TOKEN-SENTINEL"), false);
  },
  async failed_card_starts_show_their_reason() {
    const misses = [
      {id: 2, card: 12, code: "at_cap", created_at: iso(-120)},
      {id: 1, card: 9, code: "worker_unavailable", created_at: iso(-3600)},
      {id: 0, card: 4, code: "something_new", created_at: iso(-7200)},
    ];
    const {host} = await mount({responses: [view({misses})]});
    assert.ok(has(host, "misses"));
    assert.match(host.text, /3 card starts failed/);
    assert.match(host.text, /Card #12 did not start · .*agent limit.* · 2 min ago/);
    assert.match(host.text, /Card #9 did not start · .*machine.* · 1 h ago/);
    assert.match(host.text, /Card #4 did not start · a reason this page does not know \(something_new\)/);
  },
  async no_failed_starts_shows_nothing() {
    const {host} = await mount({responses: [view({misses: []})]});
    assert.equal(has(host, "misses"), false);
    const none = await mount();
    assert.equal(has(none.host, "misses"), false);
  },
  async history_lists_stop_and_resume_with_who_and_when() {
    const history = [
      {event: "owner_resume", epoch: 3, actor: "user:owner", at: iso(-60)},
      {event: "force_release", epoch: 2, actor: "user:owner", at: iso(-300)},
      {event: "claim", epoch: 2, actor: "conn:builder-bot", at: iso(-900)},
      {event: "mystery", epoch: 1, actor: "conn:x", at: iso(-9000)},
    ];
    const {host} = await mount({responses: [view({history})]});
    assert.ok(has(host, "history"));
    assert.match(host.text, /Resumed by a person · 1 min ago/);
    assert.match(host.text, /Stopped by a person · 5 min ago/);
    assert.match(host.text, /Claimed by builder-bot · 15 min ago/);
    assert.match(host.text, /mystery by x/);
  },
  async history_is_escaped() {
    const {host} = await mount({responses: [view({history: [{event: "claim", epoch: 1, actor: "conn:<img src=x onerror=alert(1)>", at: iso(-60)}]})]});
    assert.equal(host.html.includes("<img"), false);
  },
  async stale_mount_draws_nothing() {
    const anchor = new Anchor();
    const api = async () => view();
    await ui.mountOrchStatus(anchor, {project: owner, api, e, current: () => false, now: () => NOW});
    assert.equal(anchor.nextElementSibling.html, "");
  },
};

const name = process.argv[2];
assert.ok(cases[name], `unknown case ${name}`);
await cases[name]();
