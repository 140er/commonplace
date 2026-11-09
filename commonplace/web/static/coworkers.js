import { escape as e, actor } from "./api.js";

const BASE = "/api/coworkers";
const enc = encodeURIComponent;
const same = (a, b) => String(a) === String(b);
const merge = (old, rows) => [...new Map([...old, ...rows].map(row => [String(row.id), row])).values()];
const messageOrder = (a, b) => Number.isSafeInteger(Number(a.id)) && Number.isSafeInteger(Number(b.id)) ? Number(a.id) - Number(b.id) : String(a.created_at).localeCompare(String(b.created_at)) || String(a.id).localeCompare(String(b.id));
const activeJob = job => ["pending", "queued", "running"].includes(job.state);
const labels = { pending: "Queued", queued: "Queued", running: "Running", needs_configuration: "Needs configuration", unavailable: "Provider unavailable", failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted", uncertain: "Outcome unknown", completed: "Completed", done: "Completed", succeeded: "Completed" };
const runnerOutcomes = {
  runner_queued: "Runner admission queued", runner_leased: "Runner admission accepted",
  runner_starting: "Runner start requested", runner_started: "Worker launch recorded",
  runner_failed: "Runner start failed", runner_cancelled: "Runner admission cancelled",
  runner_uncertain: "Runner outcome unknown", runner_interrupted: "Runner outcome unknown",
};
const runnerReasons = {
  runner_not_configured: "No runner is configured. The task remains available.",
  runner_not_allowed: "Runner admission requires an authorized workspace owner.",
  runner_plan_pending: "The task still needs its plan approval.",
  runner_config_changed: "The runner configuration changed. Open the task to check its state.",
  runner_interrupted: "Admission could not be confirmed. Open the task before retrying.",
  runner_uncertain: "The runner outcome is unconfirmed. Open the task before retrying.",
};
const outcomeLabel = job => job.code === "task_created" ? "Task created" : job.code?.startsWith("runner_") ? runnerOutcomes[job.code] || "Task created · runner unavailable" : labels[job.state] || job.state || "Unknown state";
const outcomeDetail = job => job.code?.startsWith("runner_") ? runnerReasons[job.code] || "Admission response recorded. Open the task for current progress." : job.code;
const errorText = error => error?.message || "Request unavailable. Try again.";
const messageHref = (conversation, message) => `#/coworkers/${enc(conversation)}?message=${enc(message)}`;

// Resolve names only among this conversation's bots. A selection is explicit;
// unaddressed group discussion never broadcasts to every bot.
export function coworkerTarget(text, selected, bots) {
  if (selected) {
    const bot = bots.find(bot => same(bot.id, selected));
    return bot ? { botId: bot.id, label: `To ${bot.name}` } : { error: "This bot is no longer a participant. Choose a recipient." };
  }
  const mentioned = bots.filter(bot => {
    const name = String(bot.name).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    return new RegExp(`(?:^|\\s)@${name}(?=$|[\\s.,!?:;])`, "iu").test(text);
  });
  if (mentioned.length > 1) return { error: "More than one bot matches your mentions. Choose one recipient." };
  const bot = mentioned[0] || (bots.length === 1 ? bots[0] : null);
  return bot ? { botId: bot.id, label: `To ${bot.name}` } : { botId: null, label: "To the group. Select or @mention a bot for a reply." };
}

export function coworkerJobHTML(job, bots = [], busy = false) {
  const name = bots.find(bot => same(bot.id, job.bot_id))?.name || "Bot";
  const task = typeof job.task === "object" ? job.task?.number : job.task;
  const taskLink = /^[1-9]\d*$/.test(String(task)) ? `<a href="#/card/${enc(task)}">Open task #${e(task)}</a>` : "";
  return `<li class="cw-job" data-state="${e(job.state)}"><a href="${messageHref(job.conversation_id, job.message_id)}" data-cw-message="${e(job.message_id)}">${e(name)}</a><span>${e(outcomeLabel(job))}</span>${job.code ? `<small>${e(outcomeDetail(job))}</small>` : ""}${taskLink}${coworkerTaskProgressHTML(job.task_progress, task)}${activeJob(job) ? `<button type="button" class="btn sm" data-cw-action="cancel-job" data-id="${e(job.id)}"${busy ? " disabled" : ""}>${busy ? "Cancelling…" : "Cancel"}</button>` : ""}</li>`;
}

export function coworkerTaskProgressHTML(progress, task) {
  if (progress?.source !== "canonical_task") return "";
  if (progress.availability !== "available" || !/^[1-9]\d*$/.test(String(task))) return `<small>${progress.availability === "omitted" ? "Older task progress omitted from this view." : "Current task progress is unavailable for this conversation."}</small>`;
  const href = `#/card/${enc(task)}`;
  const state = { backlog: "Task in backlog", todo: "Task to do", doing: "Task in progress", review: "Task in review", done: "Task marked done" }[progress.status] || "Task status unavailable";
  const field = (label, text) => text ? `<p><strong>${label}</strong> <span style="white-space:pre-wrap;overflow-wrap:anywhere">${e(text)}</span></p>` : "";
  const omissions = item => `${item.omitted_fields?.length ? '<small>Oversized source fields omitted; open the task for the original.</small>' : ""}${item.redacted ? '<small>Credentials redacted.</small>' : ""}`;
  const eventNames = { comment: "Comment", ask: "Question", decide: "Decision recorded", handoff: "Handoff", release: "Release" };
  const events = (progress.events || []).map(item => `<li><small>${e(eventNames[item.kind] || "Task event")} · ${e(item.at || "")}</small>${field("", item.text)}${field("Choice:", item.choice)}${field("Context:", item.context)}${omissions(item)}</li>`).join("");
  const evidence = (progress.evidence || []).map(item => `<li><a href="${href}">${e(item.title || item.drawer_id || "Evidence on task")}</a>${item.invalidated ? " <small>Invalidated evidence</small>" : ""}${omissions(item)}</li>`).join("");
  const runner = progress.runner;
  const decision = progress.status === "review" || progress.proposal_status === "pending";
  return `<section aria-label="Canonical task progress"><p><strong>${state}</strong> · Canonical task #${e(task)} · version ${e(progress.version)}</p>${field("Task:", progress.title)}${field("Task body:", progress.body)}${field("Status:", progress.status_line)}${runner ? `<p>${e(runnerOutcomes[`runner_${runner.state}`] || "Runner state unavailable")}${runner.cancel_requested_at ? " · Cancellation requested; termination unconfirmed." : ""}</p>${field("Runner detail:", runner.outcome_code)}${omissions(runner)}` : ""}${field("Question:", progress.ask)}${field("Context:", progress.review_context)}${decision ? `<p><a href="${href}">Open task for the authorized decision</a></p>` : ""}${events ? `<ol aria-label="Canonical task events">${events}</ol>` : ""}${progress.events_omitted ? '<small>Older task events omitted; open the task for full history.</small>' : ""}${evidence ? `<ul aria-label="Canonical task evidence">${evidence}</ul>` : ""}${progress.evidence_bounded ? '<small>Evidence selection is bounded; open the task for full visible history.</small>' : ""}${field("Pull request recorded on task:", progress.pr_url)}${omissions(progress)}<a href="${href}">Full task history and artifacts</a></section>`;
}

export function createCoworkers({ mount, api, current, boards = () => [], navigate = hash => { location.hash = hash; }, timers = globalThis, doc = globalThis.document, events = globalThis.window }) {
  let host = null, live = false, workspace = null, identity = null, epoch = 0, generation = 0;
  let directory = null, conversation = null, selected = null, messages = [], jobs = [], memories = [];
  let loading = false, reading = false, directoryReading = false, directoryError = "", detailError = "", notice = "", cursor = 0;
  let timer = null, failures = 0, editor = "", formBusy = false, formError = "", formValues = {};
  let memoryRevision = 0, jobsRevision = 0, directoryRevision = 0, focusedMessage = null;
  let directoryExpanded = false, renderedHTML = "";
  const requests = new Set(), drafts = new Map(), cancelling = new Set();
  const eligible = () => live && current()?.workspace === workspace && current()?.actor === identity;
  const draft = () => {
    const key = String(selected);
    if (!drafts.has(key)) drafts.set(key, { text: "", target: "", memory: "", sending: false, savingMemory: false, attempt: null, uncertain: false });
    return drafts.get(key);
  };
  const participants = () => (directory?.bots || []).filter(bot => conversation?.bot_ids.some(id => same(id, bot.id)));
  const valid = (page, turn = generation, id = selected) => eligible() && epoch === page && generation === turn && same(selected, id);
  const path = id => `${BASE}/conversations/${enc(id)}`;
  const button = (action, label, extra = "") => `<button type="button" class="btn" data-cw-action="${action}" ${extra}>${label}</button>`;
  const person = id => current()?.people?.[id] || current()?.people?.[`user:${id}`] || actor(id);
  const conversationName = item => item.title || (item.kind === "dm" ? (directory?.bots || []).find(bot => item.bot_ids.some(id => same(id, bot.id)))?.name || "Direct message" : "Group conversation");

  async function request(url, options = {}, scope = "conversation") {
    const controller = new AbortController(), entry = { controller, scope };
    requests.add(entry);
    try { return await api(url, { ...options, signal: controller.signal, timeoutMs: 10000 }); }
    finally { requests.delete(entry); }
  }
  function abort(scope) {
    for (const entry of requests) if (!scope || entry.scope === scope) { entry.controller.abort(); requests.delete(entry); }
  }
  function schedule() {
    timers.clearTimeout(timer);
    if (eligible() && !doc?.hidden) timer = timers.setTimeout(() => { void refresh(); }, Math.min(30000, 3000 * 2 ** Math.min(failures, 4)));
  }
  function clearDetail() {
    conversation = null; messages = []; jobs = []; memories = []; cursor = 0;
  }
  function mergeJob(job, receipt = false) {
    const old = jobs.find(item => same(item.id, job.id));
    // An HTTP acknowledgement can arrive after a refresh already saw completion.
    if (old && ((!activeJob(old) && activeJob(job)) || (old.state === "running" && ["pending", "queued"].includes(job.state)) || (old.updated_at && job.updated_at && old.updated_at > job.updated_at))) return;
    // Mutation acknowledgements may predate a newer per-viewer GET revocation.
    // Only conversation reads replace canonical progress; receipts cannot revive it.
    if (receipt && job.task_progress) {
      const { task_progress, task, ...ack } = job;
      job = { ...ack, ...(old?.task ? { task: old.task } : {}), ...(old?.task_progress ? { task_progress: old.task_progress } : {}) };
    }
    jobs = merge(jobs, [job]);
  }
  function clearTaskProgress() {
    jobs = jobs.map(job => {
      if (!job.task_progress) return job;
      const { task, ...receipt } = job;
      return { ...receipt, task_progress: { source: "canonical_task", availability: "unavailable" } };
    });
  }
  function denied(error) {
    if (error?.status === 401) { stop(); return true; }
    if ([403, 404].includes(error?.status)) {
      generation++; abort("conversation"); reading = false; loading = false;
      clearDetail(); drafts.delete(String(selected)); cancelling.clear();
      if (directory) directory.conversations = directory.conversations.filter(item => !same(item.id, selected));
      detailError = "This conversation is unavailable or you no longer have access.";
      render();
      return true;
    }
    return false;
  }

  function sidebarHTML() {
    if (!directory) return `<p role="status">${directoryReading ? "Loading conversations…" : "Conversations unavailable."}</p>`;
    const rows = (items, empty) => items.length ? items.map(item => `<li><a href="#/coworkers/${enc(item.id)}"${same(item.id, selected) ? ' aria-current="page"' : ""}>${e(conversationName(item))}</a></li>`).join("") : `<li class="cw-quiet">${empty}</li>`;
    const conversations = directory.conversations;
    return `<nav aria-label="Conversations"><h2>Channels</h2><ul>${rows(conversations.filter(item => item.kind === "group" && item.board), "No project channels yet.")}</ul><h2>Direct messages</h2><ul>${rows(conversations.filter(item => item.kind === "dm"), "No direct messages yet.")}</ul><h2>Groups</h2><ul>${rows(conversations.filter(item => item.kind === "group" && !item.board), "No groups yet.")}</ul></nav><section aria-labelledby="cw-bots-heading"><h2 id="cw-bots-heading">Coworkers</h2><ul>${directory.bots.length ? directory.bots.map(bot => `<li class="cw-bot"><span>${e(bot.name)}</span><small>${bot.connection_id ? "Connection linked" : "Persona"}</small>${button("start-dm", "Message", `data-id="${e(bot.id)}"`)}</li>`).join("") : '<li class="cw-quiet">No bots created yet.</li>'}</ul>${directory.can_manage_bots === true ? button("new-bot", "Create bot") : ""}</section>`;
  }
  function editorHTML() {
    if (!editor) return "";
    const value = name => e(formValues[name] || "");
    const bot = editor === "bot";
    return `<section class="cw-editor" aria-labelledby="cw-editor-heading"><header><h2 id="cw-editor-heading">${bot ? "Create a coworker" : "New conversation"}</h2>${button("close-editor", "Close", formBusy ? "disabled" : "")}</header><form data-cw-form="${bot ? "bot" : "conversation"}">${bot ? `<label>Name<input name="name" maxlength="100" required value="${value("name")}"></label><label>Instructions<textarea name="instructions" rows="3" maxlength="4000">${value("instructions")}</textarea></label><label>Existing connection ID (optional)<input name="connection_id" value="${value("connection_id")}"></label><p class="cw-quiet">A persona keeps its name and instructions. Linking a connection does not start a worker.</p>` : `<label>Type<select name="kind"><option value="dm"${formValues.kind !== "group" ? " selected" : ""}>Direct message</option><option value="group"${formValues.kind === "group" ? " selected" : ""}>Group or project channel</option></select></label><label>Title (optional)<input name="title" maxlength="200" value="${value("title")}"></label><fieldset><legend>Bots</legend>${directory.bots.length ? directory.bots.map(item => `<label class="cw-check"><input type="checkbox" name="bot_ids" value="${e(item.id)}"${(formValues.bot_ids || []).some(id => same(id, item.id)) ? " checked" : ""}>${e(item.name)}</label>`).join("") : '<p>Create a bot before starting a DM.</p>'}</fieldset><label>Other workspace user IDs<textarea name="member_ids" rows="2" aria-describedby="cw-invite-help">${value("member_ids")}</textarea></label><p id="cw-invite-help" class="cw-quiet">You are included. For a group, separate invited user IDs with commas or new lines. A DM has just you and one bot.</p><label>Project slug (optional)<input name="board" value="${value("board")}" list="cw-projects"></label><datalist id="cw-projects">${boards().map(board => `<option value="${e(board.slug)}">${e(board.name || board.slug)}</option>`).join("")}</datalist>`}<p role="alert" data-cw-form-error>${e(formError)}</p><button class="btn primary"${formBusy ? " disabled" : ""}>${formBusy ? "Saving…" : bot ? "Create bot" : "Create conversation"}</button></form></section>`;
  }
  function messageHTML(item) {
    const bot = (directory?.bots || []).find(bot => same(bot.id, item.bot_id));
    const stamp = item.created_at ? `<time datetime="${e(item.created_at)}">${e(new Date(item.created_at).toLocaleString())}</time>` : "";
    const link = id => messageHref(selected, id);
    const reply = item.reply_to ? `<a href="${link(item.reply_to)}" data-cw-message="${e(item.reply_to)}">In reply to message ${e(item.reply_to)}</a>` : "";
    return `<li id="cw-message-${enc(item.id)}" tabindex="-1" class="cw-message${bot ? " cw-from-bot" : ""}"><header><strong>${e(bot?.name || person(item.actor))}</strong>${bot ? "<small>Bot</small>" : ""}${stamp}${reply}<a href="${link(item.id)}" data-cw-message="${e(item.id)}" aria-label="Link to message ${e(item.id)}">#${e(item.id)}</a></header><p>${e(item.text)}</p></li>`;
  }
  function detailHTML() {
    if (!conversation || !directory) return `<section class="cw-empty"><h2>${selected ? loading ? "Opening conversation…" : "Conversation unavailable" : "Talk to your coworkers"}</h2><p>${selected ? "" : "Choose a conversation, or start a DM or group."}</p>${detailError ? `<p role="alert">${e(detailError)}</p>${button("refresh", "Retry")}` : ""}</section>`;
    const bots = participants(), d = draft(), target = coworkerTarget(d.text, d.target, bots);
    return `<section class="cw-conversation" aria-label="${e(conversationName(conversation))}"><header class="cw-conversation-head"><div><h2>${e(conversationName(conversation))}</h2><p class="cw-quiet">${conversation.kind === "dm" ? "Direct message" : "Group"}${conversation.board ? ` · Project ${e(conversation.board)} · <a href="#/board/${enc(conversation.board)}/chat">Project chat</a>` : ""}</p></div>${button("refresh", "Refresh")}</header><div class="cw-timeline" tabindex="0" aria-label="Conversation timeline"><ol aria-label="Messages">${messages.length ? messages.map(messageHTML).join("") : '<li class="cw-quiet">No messages yet. Say what you would like to work on.</li>'}</ol></div><section class="cw-work" aria-label="Response jobs"><ul>${jobs.map(job => coworkerJobHTML(job, bots, cancelling.has(String(job.id)))).join("")}</ul></section><p class="cw-notice" role="status" data-cw-notice>${e(detailError || notice)}</p><form class="cw-composer" data-cw-form="message"><label>Reply to<select name="target" data-cw-field="target"><option value="">${bots.length === 1 ? "Conversation bot" : "Group / @mention"}</option>${bots.map(bot => `<option value="${e(bot.id)}"${same(d.target, bot.id) ? " selected" : ""}>${e(bot.name)}</option>`).join("")}</select></label><label for="cw-compose">Message</label><textarea id="cw-compose" name="text" data-cw-field="text" rows="3" maxlength="8000" aria-describedby="cw-address cw-shortcut"${d.uncertain ? "" : " required"}>${e(d.text)}</textarea><p id="cw-address"${target.error ? ' role="alert"' : ""}>${e(target.error || target.label)}</p>${conversation.board ? '<p class="cw-quiet">Use /task Title for project work. That request is shared with the project.</p>' : ""}<div class="cw-send-row"><small id="cw-shortcut">Ctrl / ⌘ + Enter to send</small><button class="btn primary" data-cw-send${d.sending || (!d.uncertain && (!d.text.trim() || target.error)) ? " disabled" : ""}>${d.sending ? "Sending…" : d.uncertain ? "Retry send" : "Send"}</button></div></form></section><aside class="cw-context" aria-label="Conversation context"><details open><summary>Members</summary><ul>${conversation.member_ids.map(id => `<li>${e(person(id))}${same(id, directory.user_id) ? " (you)" : ""}</li>`).join("")}${bots.map(bot => `<li>${e(bot.name)} <small>Bot${bot.connection_id ? " · connection linked" : " · persona"}</small></li>`).join("")}</ul></details><details open data-cw-memory><summary>Conversation memory</summary><p class="cw-quiet">Visible to this conversation. Hiding a memory keeps the original record and messages.</p><ul>${memories.length ? memories.map(item => `<li><p>${e(item.text)}</p>${button("hide-memory", "Hide", `data-id="${e(item.id)}"${d.savingMemory ? " disabled" : ""}`)}</li>`).join("") : '<li class="cw-quiet">No saved memory in this conversation.</li>'}</ul><form data-cw-form="memory"><label for="cw-memory-text">Remember exact words</label><textarea id="cw-memory-text" name="text" data-cw-field="memory" rows="3" maxlength="4000" required>${e(d.memory)}</textarea><button class="btn"${d.savingMemory ? " disabled" : ""}>${d.savingMemory ? "Saving…" : "Add memory"}</button></form></details></aside>`;
  }

  function render() {
    if (!eligible() || !host) return;
    const active = doc?.activeElement, field = host.contains?.(active) && active?.dataset?.cwField;
    const editorField = host.contains?.(active) && active?.closest?.(".cw-editor") && active.name;
    const action = host.contains?.(active) && active?.dataset?.cwAction;
    const actionId = action && active.dataset.id;
    const selection = (field || editorField) && [active.selectionStart, active.selectionEnd];
    const timeline = host.querySelector(".cw-timeline"), top = timeline?.scrollTop || 0;
    const bottom = !timeline || timeline.scrollHeight - top - timeline.clientHeight < 60;
    const closed = [...host.querySelectorAll(".cw-context details")].map(item => !item.open);
    // Form state is captured on input so refreshing never reads a replaced form.
    const expanded = directoryExpanded || !selected;
    const html = `<div class="coworkers"><aside class="cw-sidebar" data-expanded="${expanded}"><header><h2>Conversations</h2>${button("toggle-directory", "Browse", `aria-controls="cw-directory-list" aria-expanded="${expanded}"`)}${button("new-conversation", "New", !directory ? "disabled" : "")}</header>${directoryError ? `<p role="alert">${e(directoryError)}</p>${button("refresh", "Retry")}` : ""}<div id="cw-directory-list">${sidebarHTML()}<a class="cw-project-link" href="#/chat">Open project chat</a></div></aside><div class="cw-body">${editorHTML()}<div class="cw-panes">${detailHTML()}</div></div></div>`;
    if (html === renderedHTML && host.querySelector(".coworkers") && !focusedMessage) return;
    host.innerHTML = html; renderedHTML = html;
    const next = host.querySelector(".cw-timeline");
    if (next) next.scrollTop = bottom ? next.scrollHeight : top;
    host.querySelectorAll(".cw-context details").forEach((item, index) => { if (closed[index]) item.open = false; });
    if (field) {
      const input = host.querySelector(`[data-cw-field="${field}"]`);
      input?.focus({ preventScroll: true });
      if (input?.tagName === "TEXTAREA" && selection) input.setSelectionRange(...selection);
    }
    if (editorField) {
      const input = host.querySelector(`.cw-editor [name="${editorField}"]`);
      input?.focus({ preventScroll: true });
      if (input?.setSelectionRange && selection?.every(Number.isInteger)) input.setSelectionRange(...selection);
    }
    if (action) [...host.querySelectorAll("[data-cw-action]")].find(item => item.dataset.cwAction === action && item.dataset.id === actionId)?.focus({ preventScroll: true });
    if (focusedMessage && messages.some(item => same(item.id, focusedMessage))) {
      const node = doc?.getElementById(`cw-message-${enc(focusedMessage)}`);
      node?.scrollIntoView({ block: "nearest" }); node?.focus({ preventScroll: true }); focusedMessage = null;
    }
  }
  function captureForm(form) {
    const values = new FormData(form);
    formValues = Object.fromEntries(values);
    formValues.bot_ids = values.getAll("bot_ids");
  }
  function composeState() {
    if (!conversation) return;
    const d = draft(), target = coworkerTarget(d.text, d.target, participants());
    const hint = host.querySelector("#cw-address"), send = host.querySelector("[data-cw-send]");
    if (hint) { hint.textContent = target.error || target.label; if (target.error) hint.setAttribute("role", "alert"); else hint.removeAttribute("role"); }
    if (send) send.disabled = d.sending || (!d.uncertain && (!d.text.trim() || Boolean(target.error)));
  }

  async function loadDirectory() {
    if (!eligible() || directoryReading) return;
    const page = epoch, revision = directoryRevision;
    directoryReading = true;
    render();
    try {
      const result = await request(BASE, {}, "directory");
      if (!eligible() || epoch !== page) return;
      if (revision === directoryRevision) directory = result;
      if (editor === "bot" && directory?.can_manage_bots !== true) { editor = ""; formValues = {}; }
      directoryError = "";
    } catch (error) {
      if (!eligible() || epoch !== page) return;
      if (error.status === 401) { stop(); return; }
      if (error.status === 403) { generation++; abort("conversation"); reading = false; loading = false; directory = null; clearDetail(); drafts.clear(); }
      if (error.name !== "AbortError") { directoryError = `Could not load coworkers: ${errorText(error)}`; failures++; }
    } finally {
      if (eligible() && epoch === page) { directoryReading = false; render(); }
    }
  }
  async function readConversation(initial = false) {
    if (!eligible() || !selected || reading) return;
    const page = epoch, turn = generation, id = selected, memoryVersion = memoryRevision, jobsVersion = jobsRevision;
    reading = true;
    try {
      if (initial || !conversation) {
        const result = await request(path(id));
        if (!valid(page, turn, id)) return;
        if (!same(result.conversation?.id, id)) throw new Error("Conversation response did not match the request.");
        conversation = result.conversation;
        messages = result.messages.filter(item => same(item.conversation_id, id)).sort(messageOrder);
        jobs = result.jobs.filter(item => same(item.conversation_id, id));
        memories = result.memories.filter(item => same(item.conversation_id, id));
        cursor = messages.at(-1)?.id || 0;
      } else {
        // Bounded catch-up; the cursor advances only through messages received.
        for (let count = 0; count < 4; count++) {
          const result = await request(`${path(id)}/messages?after=${enc(cursor)}&limit=100`);
          if (!valid(page, turn, id)) return;
          const rows = result.messages.filter(item => same(item.conversation_id, id));
          messages = merge(messages, rows).sort(messageOrder);
          if (jobsVersion === jobsRevision) jobs = result.jobs.filter(item => same(item.conversation_id, id));
          else clearTaskProgress();
          const next = rows.at(-1)?.id;
          if (!rows.length || same(next, cursor)) break;
          cursor = next;
          if (rows.length < 100) break;
        }
        const result = await request(`${path(id)}/memory`);
        if (!valid(page, turn, id)) return;
        if (memoryVersion === memoryRevision) memories = result.memories.filter(item => same(item.conversation_id, id));
      }
      const d = draft();
      if (d.attempt && !d.sending && messages.some(item => item.request_id === d.attempt.request_id)) {
        if (d.text === d.attempt.text) d.text = "";
        d.attempt = null; d.uncertain = false;
      }
      detailError = ""; failures = 0;
    } catch (error) {
      if (!valid(page, turn, id)) return;
      clearTaskProgress();
      if (!denied(error) && error.name !== "AbortError") { detailError = `${conversation ? "Refresh unavailable; showing the last received data." : "Could not open conversation."} ${errorText(error)}`; failures++; }
    } finally {
      if (valid(page, turn, id)) { reading = false; loading = false; render(); }
    }
  }
  async function refresh() {
    if (!eligible()) { if (live) stop(); return; }
    timers.clearTimeout(timer);
    await Promise.all([loadDirectory(), readConversation()]);
    schedule();
  }

  async function send() {
    if (!eligible() || !conversation) return;
    const d = draft();
    if (d.sending) return;
    const target = coworkerTarget(d.text, d.target, participants());
    if (!d.attempt && (!d.text.trim() || target.error)) return;
    const body = d.attempt || { text: d.text, request_id: globalThis.crypto.randomUUID(), ...(target.botId != null ? { bot_id: target.botId } : {}) };
    const flight = {};
    d.attempt = body; d.sending = flight;
    jobsRevision++;
    const page = epoch, turn = generation, id = selected;
    notice = ""; detailError = ""; render();
    try {
      const result = await request(`${path(id)}/messages`, { method: "POST", body });
      if (!valid(page, turn, id)) return;
      if (!same(result.message?.conversation_id, id)) throw new Error("Message response did not match this conversation.");
      messages = merge(messages, [result.message]).sort(messageOrder);
      if (result.job && same(result.job.conversation_id, id)) { mergeJob(result.job, true); jobsRevision++; }
      // Do not advance the read cursor: other humans may have posted before this acknowledgement.
      if (d.text === body.text) d.text = "";
      d.attempt = null; d.uncertain = false;
      notice = result.job ? "Message saved. Response job recorded." : "Message saved.";
    } catch (error) {
      if (!valid(page, turn, id)) return;
      if (!denied(error)) {
        d.uncertain = !error.status || error.status >= 500;
        if (!d.uncertain) d.attempt = null;
        detailError = d.uncertain ? `Send could not be confirmed. Retry send reuses the same request ID. ${errorText(error)}` : `Message was not sent. ${errorText(error)}`;
      }
    } finally {
      if (d.sending === flight) d.sending = false;
      if (valid(page, turn, id)) { render(); schedule(); }
    }
  }
  async function changeMemory(id = null) {
    if (!eligible() || !conversation) return;
    const d = draft();
    if (d.savingMemory || (!id && !d.memory.trim())) return;
    const text = d.memory, page = epoch, turn = generation, conversationId = selected;
    const flight = {};
    d.savingMemory = flight; memoryRevision++; detailError = "";
    if (id != null) { jobsRevision++; clearTaskProgress(); }
    render();
    try {
      const result = await request(`${path(conversationId)}/memory${id != null ? `/${enc(id)}` : ""}`, id != null ? { method: "DELETE", body: {} } : { method: "POST", body: { text } });
      if (!valid(page, turn, conversationId)) return;
      memoryRevision++;
      if (id != null) { memories = memories.filter(item => !same(item.id, id)); jobsRevision++; clearTaskProgress(); }
      else if (same(result.memory?.conversation_id, conversationId)) { memories = merge(memories, [result.memory]); if (d.memory === text) d.memory = ""; }
      else throw new Error("Memory response did not match this conversation.");
      notice = id != null ? "Memory hidden from this conversation. Original messages remain." : "Memory saved for this conversation.";
    } catch (error) {
      if (valid(page, turn, conversationId) && !denied(error)) detailError = `Could not confirm memory ${id != null ? "hide" : "save"}. Refresh memory before trying again. ${errorText(error)}`;
    } finally { if (d.savingMemory === flight) d.savingMemory = false; if (valid(page, turn, conversationId)) render(); }
  }
  async function cancelJob(id) {
    if (!eligible() || !jobs.some(job => same(job.id, id) && activeJob(job)) || cancelling.has(String(id))) return;
    const page = epoch, turn = generation, conversationId = selected;
    cancelling.add(String(id)); jobsRevision++; render();
    try {
      const result = await request(`${BASE}/jobs/${enc(id)}/cancel`, { method: "POST", body: {} });
      if (!valid(page, turn, conversationId)) return;
      if (!same(result.job?.conversation_id, conversationId) || !same(result.job?.id, id)) throw new Error("Cancellation response did not match this conversation.");
      mergeJob(result.job, true); jobsRevision++; notice = `Job state: ${labels[result.job.state] || result.job.state}.`;
    } catch (error) {
      if (valid(page, turn, conversationId) && !denied(error)) detailError = `Cancellation could not be confirmed. Refresh to check the job. ${errorText(error)}`;
    } finally { cancelling.delete(String(id)); if (valid(page, turn, conversationId)) render(); }
  }
  async function saveForm(form) {
    if (!eligible() || formBusy) return;
    captureForm(form);
    const bot = editor === "bot", page = epoch;
    if (bot && directory?.can_manage_bots !== true) return;
    let body;
    if (bot) body = { name: formValues.name.trim(), instructions: formValues.instructions || "", ...(formValues.connection_id?.trim() ? { connection_id: formValues.connection_id.trim() } : {}) };
    else {
      const members = [...new Set((formValues.member_ids || "").split(/[,\n]/).map(id => id.trim()).filter(Boolean))];
      if (formValues.kind === "dm" && (formValues.bot_ids.length !== 1 || members.length)) { formError = "A direct message includes you and exactly one bot. Use a group to invite other people."; render(); return; }
      body = { kind: formValues.kind, title: formValues.title || "", member_ids: members, bot_ids: formValues.bot_ids, ...(formValues.board?.trim() ? { board: formValues.board.trim() } : {}) };
    }
    formBusy = true; formError = ""; render();
    try {
      const result = await request(`${BASE}/${bot ? "bots" : "conversations"}`, { method: "POST", body }, "editor");
      if (!eligible() || epoch !== page) return;
      directoryRevision++;
      if (bot) directory.bots = merge(directory.bots, [result.bot]);
      else directory.conversations = merge(directory.conversations, [result.conversation]);
      editor = ""; formValues = {};
      if (!bot) navigate(`#/coworkers/${enc(result.conversation.id)}`);
    } catch (error) {
      if (eligible() && epoch === page) {
        if (error.status === 401) { stop(); return; }
        formError = `${errorText(error)}${!error.status || error.status >= 500 ? " Creation could not be confirmed. Refresh before creating again." : ""}`;
      }
    } finally { if (eligible() && epoch === page) { formBusy = false; render(); } }
  }
  function input(event) {
    const form = event.target.closest?.(".cw-editor form");
    if (form) { captureForm(form); return; }
    const field = event.target.dataset?.cwField;
    if (!field || !conversation) return;
    draft()[field] = event.target.value;
    composeState();
  }
  function click(event) {
    const message = event.target.closest?.("[data-cw-message]");
    if (message && eligible()) {
      // Keep same-conversation links in the app, including keyboard activation.
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      focusedMessage = message.dataset.cwMessage;
      render(); return;
    }
    const item = event.target.closest?.("[data-cw-action]");
    if (!item || !eligible()) return;
    const action = item.dataset.cwAction, id = item.dataset.id;
    if (action === "refresh") void refresh();
    else if (action === "toggle-directory") { directoryExpanded = !directoryExpanded; render(); }
    else if (action === "cancel-job") void cancelJob(id);
    else if (action === "hide-memory") void changeMemory(id);
    else if (action === "close-editor") { if (!formBusy) { editor = ""; formValues = {}; render(); } }
    else if (action === "new-bot" || action === "new-conversation" || action === "start-dm") {
      if (formBusy || !directory || (action === "new-bot" && directory.can_manage_bots !== true)) return;
      editor = action === "new-bot" ? "bot" : "conversation"; formValues = { kind: "dm", bot_ids: id ? [id] : [] }; formError = ""; render();
      host.querySelector(".cw-editor input, .cw-editor select")?.focus();
    }
  }
  function submit(event) {
    const form = event.target.closest?.("[data-cw-form]");
    if (!form) return;
    event.preventDefault(); event.stopPropagation();
    if (form.dataset.cwForm === "message") void send();
    else if (form.dataset.cwForm === "memory") void changeMemory();
    else void saveForm(form);
  }
  function keydown(event) {
    if (event.target.dataset?.cwField === "text" && event.key === "Enter" && (event.ctrlKey || event.metaKey) && !event.isComposing) { event.preventDefault(); void send(); }
  }
  function visibility() {
    timers.clearTimeout(timer);
    if (!doc?.hidden) void refresh();
  }
  function stop() {
    live = false; epoch++; generation++;
    timers.clearTimeout(timer); abort();
    host?.removeEventListener("click", click); host?.removeEventListener("submit", submit);
    host?.removeEventListener("input", input); host?.removeEventListener("change", input); host?.removeEventListener("keydown", keydown);
    doc?.removeEventListener("visibilitychange", visibility); doc?.removeEventListener("session-expired", stop); doc?.removeEventListener("signout", stop);
    events?.removeEventListener("online", visibility); events?.removeEventListener("pagehide", stop);
    if (host) host.innerHTML = "";
    host = null; workspace = null; identity = null; directory = null; selected = null;
    clearDetail(); drafts.clear(); cancelling.clear();
    reading = false; directoryReading = false; formBusy = false; loading = false;
    editor = ""; formValues = {}; formError = ""; directoryError = ""; detailError = ""; notice = ""; failures = 0;
    directoryExpanded = false; renderedHTML = ""; focusedMessage = null;
  }
  function show(id = null, messageId = null) {
    const node = mount();
    if (!eligible() || host !== node) {
      stop();
      if (!current() || !node) return;
      host = node; live = true; workspace = current().workspace; identity = current().actor;
      host.addEventListener("click", click); host.addEventListener("submit", submit);
      host.addEventListener("input", input); host.addEventListener("change", input); host.addEventListener("keydown", keydown);
      doc?.addEventListener("visibilitychange", visibility); doc?.addEventListener("session-expired", stop); doc?.addEventListener("signout", stop);
      events?.addEventListener("online", visibility); events?.addEventListener("pagehide", stop);
    }
    if (!same(id, selected) || !conversation) {
      if (selected != null) {
        const previous = draft(); previous.sending = false; previous.savingMemory = false;
        if (previous.attempt) previous.uncertain = true;
      }
      epoch++; generation++; abort(); reading = false; directoryReading = false; formBusy = false;
      selected = id; clearDetail(); cancelling.clear(); detailError = ""; notice = "";
      directoryExpanded = false;
      editor = ""; formValues = {}; loading = Boolean(id);
      void readConversation(true);
    }
    focusedMessage = messageId;
    render();
    void loadDirectory().then(() => { if (eligible()) render(); });
    schedule();
  }
  return { show, stop, refresh };
}
