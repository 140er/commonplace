// Bots screen: a roster of named bots and one streamed chat thread per bot.
// Pure markup functions (…HTML) take the escape helper `e`; createBots owns state, polling and events.
// Controls carry data-bots-action / data-bots-form, never data-action, so the shell's own click
// handler leaves them alone. All server text goes through `e` before it reaches innerHTML.
import { elapsed, safeLink } from "./api.js";
import { sendMessage } from "./botstream.js";
import { permissionText, sharingHTML, threadsHTML, threadPreviewHTML } from "./bot-sharing.js";

export const BOTS_COPY = Object.freeze({
  title: "Bots",
  newBot: "New bot",
  empty: "No bots yet. Create one to get started.",
  pick: "Choose a bot, or create a new one.",
  hint: (name) => `Tell ${name} what you need. Ask a question, or ask for work and ${name} files it for you.`,
  loading: "Loading…",
  earlier: "Earlier messages",
  waiting: (n) => `Waiting on you (${n})`,
  gone: "That work is no longer available to you.",
  offline: "Could not reach the server. Retrying…",
  unavailable: "This bot is no longer available.",
  stopped: "Stopped.",
  orchestrator: "Coworker",
  coworker: "Coworker",
  learnedGone: "That item is gone.",
});

const STATUS = Object.freeze({ queued: "Queued", working: "Working", needs_you: "Needs you", ready: "Ready", blocked: "Blocked", done: "Done" });
const DOT = Object.freeze({ needs_you: "Needs you", working: "Working" });
const FAILS = Object.freeze({
  interrupted: "This reply was interrupted. Ask again if you still need it.",
  needs_configuration: "No chat model is set up.",
  limit: "The daily limit is reached.",
  rate: "Too many messages for now. Try again in a minute.",
  model_error: "The model could not answer.",
  timeout: "The bot took too long to answer.",
});
const SETUP = Object.freeze({
  needs_configuration: ["Bots need a chat model before they can answer.", "Choose one in Workspace settings.", "Ask the workspace owner to choose a chat model."],
  needs_turn_on: ["This project's chat model is not turned on yet.", "Turn it on in the project's settings, then send again.", "Ask the workspace owner to turn it on for this project."],
  key_missing: ["The chat model has no key yet.", "Add it in Workspace settings.", "Ask the workspace owner to add it."],
});
// Server-supplied keys only ever match the table's own entries ("constructor" is not a status).
const own = (table, key, fallback) => typeof key === "string" && Object.hasOwn(table, key) ? table[key] : fallback;
const MAX_TEXT = 8000;
const PAGE = 50;
const enc = encodeURIComponent;

export const markRedactions = (escaped) => escaped.replace(/\[(?:redacted|omitted):[a-z_]+\]/g, (marker) => `<mark class="redacted">${marker}</mark>`);
const marked = (value, e) => markRedactions(e(value));
const oneLine = (value) => String(value ?? "").replace(/\s+/g, " ").trim().slice(0, 160);
const card = (value) => Number.isSafeInteger(Number(value)) && Number(value) > 0 ? Number(value) : 0;
const initial = (name) => (Array.from(String(name ?? "").trim())[0] || "?").toUpperCase();
const link = (url) => {
  const href = safeLink(url);
  if (href) return href;
  return typeof url === "string" && /^#\/[\w\-./?=&%]*$/.test(url) ? url : null;
};
const chevron = '<svg class="i" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m4.5 6 3.5 3.5L11.5 6"/></svg>';
const back = '<svg class="i" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 3 5 8l5 5"/></svg>';

export function setupText(code, canConfigure) {
  const [what, owner, member] = own(SETUP, code, SETUP.needs_configuration);
  return `${what} ${canConfigure ? owner : member}`;
}

// ---------- markup ----------

export function rosterHTML({ bots, current = null, loaded = true }, e) {
  if (!bots.length) return `<li class="bots-note">${e(loaded ? BOTS_COPY.empty : BOTS_COPY.loading)}</li>`;
  return bots.map((bot) => {
    const line = bot.last?.text ? `${bot.last.role === "user" ? "You: " : ""}${oneLine(bot.last.text)}` : oneLine(bot.job);
    const unread = Number.isSafeInteger(bot.unread) && bot.unread > 0 ? `<span class="bot-unread">${bot.unread}<span class="sr"> unread</span></span>` : "";
    const dot = own(DOT, bot.state) ? `<span class="bot-dot ${e(bot.state)}" role="img" aria-label="${DOT[bot.state]}"></span>` : "";
    return `<li><a class="bot-row" href="#/bots/${enc(bot.id)}" data-bot="${e(bot.id)}"${bot.id === current ? ' aria-current="page"' : ""}>
      <span class="bot-av" aria-hidden="true">${e(initial(bot.name))}</span>
      <span class="bot-main"><span class="bot-top"><b class="bot-name">${e(bot.name)}</b><span class="deck-bot-job" hidden>${e(oneLine(bot.job))}</span>${bot.last?.at ? `<time class="bot-at" datetime="${e(bot.last.at)}">${e(elapsed(bot.last.at))}</time>` : ""}</span><span class="bot-line">${marked(line, e)}</span></span>
      <span class="bot-side">${unread}${dot}<span class="deck-bot-state" data-state="${e(bot.state)}" hidden>${bot.frozen ? bot.frozen_reason === "owner_left" ? "Needs a new owner" : "Disabled" : own({ needs_you: "Waiting on you", working: "Working" }, bot.state, "Idle")}</span></span></a></li>`;
  }).join("");
}

export function modelNoteHTML({ model, canConfigure, error }, e) {
  if (error) return `<p class="bots-notice" role="alert">${e(error)} <button type="button" data-bots-action="retry">Retry</button></p>`;
  if (!model || model.ready !== false) return "";
  const text = setupText(model.code, canConfigure);
  return `<p class="bots-notice" role="status">${e(text)}${canConfigure ? ' <a href="#/settings">Open settings</a>' : ""}</p>`;
}

const gone = (e) => `<p class="bb-gone">${e(BOTS_COPY.gone)}</p>`;

export function workHTML(b, ctx, e) {
  const n = card(b.card);
  if (b.gone) return gone(e);
  const key = `work:${n}:${ctx.mid ?? ""}`;
  const open = ctx.open?.has(key) === true;
  const links = (Array.isArray(b.links) ? b.links : []).map((l) => ({ label: l?.label, href: link(l?.url) })).filter((l) => l.href);
  return `<div class="bb work" data-status="${e(b.status)}"><button type="button" class="bb-head" data-bots-action="toggle" data-key="${e(key)}" aria-expanded="${open}">${own(STATUS, b.status) ? `<span class="chip ${e(b.status)}">${own(STATUS, b.status)}</span>` : ""}<span class="bb-title">${marked(b.title ?? "", e)}</span>${chevron}</button>${open ? `<div class="bb-body">${b.note ? `<p class="bb-note">${marked(b.note, e)}</p>` : ""}${links.length ? `<ul class="bb-links">${links.map((l) => `<li><a href="${e(l.href)}" target="_blank" rel="noopener noreferrer">${e(l.label || l.href)}</a></li>`).join("")}</ul>` : ""}${n ? `<a class="bb-open" href="#/card/${n}">Open</a>` : ""}</div>` : ""}</div>`;
}

export function decisionHTML(b, ctx, e) {
  const n = card(b.card);
  if (b.gone) return gone(e);
  const head = `<p class="bb-q">${marked(b.question ?? "", e)}</p>${b.context ? `<p class="bb-ctx">${marked(b.context, e)}</p>` : ""}`;
  if (!b.open) return `<div class="bb decision" data-state="answered">${head}<p class="bb-answer">${b.answer ? `Answered: ${marked(b.answer, e)}` : "Closed"}</p></div>`;
  const off = ctx.busy?.has(n) ? " disabled" : "";
  const options = (Array.isArray(b.options) ? b.options : []).map((o) => `<button type="button" class="bb-opt" data-bots-action="decide" data-work="${n}" data-version="${e(b.version)}" data-choice="${e(o)}"${off}>${e(o)}</button>`).join("");
  return `<div class="bb decision" data-state="open">${head}${options ? `<div class="bb-btns">${options}</div>` : ""}<form class="bb-reply" data-bots-form="reply" data-work="${n}" data-version="${e(b.version)}"><input name="reply" aria-label="Your own answer" placeholder="Or answer in your own words" autocomplete="off" required${off}><button type="submit" class="bb-opt"${off}>Reply</button></form></div>`;
}

export function confirmHTML(b, ctx, e) {
  const label = marked(b.label ?? "", e);
  if (b.state === "open") {
    const off = ctx.busy?.has(`p:${b.pending}`) ? " disabled" : "";
    return `<div class="bb confirm" data-state="open"><p class="bb-label">${label}</p><div class="bb-btns"><button type="button" class="bb-opt go" data-bots-action="confirm" data-pending="${e(b.pending)}" data-accept="1"${off}>Confirm</button><button type="button" class="bb-opt" data-bots-action="confirm" data-pending="${e(b.pending)}" data-accept="0"${off}>Cancel</button></div></div>`;
  }
  const word = own({ done: "Done", cancelled: "Cancelled", expired: "Expired" }, b.state);
  return `<p class="bb-settled">${label}${word ? ` · ${word}` : ""}</p>`;
}

const learnedButton = (e, what, id, label, off) => `<button type="button" class="bb-opt${what === "accept" ? " go" : ""}" data-bots-action="learned" data-do="${what}" data-id="${e(id)}"${off}>${label}</button>`;

function learnedEditHTML(id, text, e) {
  return `<form class="bb-reply" data-bots-form="learned-edit" data-id="${e(id)}"><input name="text" aria-label="Your words" maxlength="300" value="${e(text)}" autocomplete="off" required><button type="submit" class="bb-opt go">Save</button><button type="button" class="bb-opt" data-bots-action="learned" data-do="cancel" data-id="${e(id)}">Cancel</button></form>`;
}

/** A reply used learned items, or a turn just learned some: each with a Forget tap. */
export function learnedHTML(b, ctx, e) {
  if (b.gone || !Array.isArray(b.items) || !b.items.length) return `<p class="bb-gone">${e(BOTS_COPY.learnedGone)}</p>`;
  const lead = b.how === "learned" ? "Learned" : "Used";
  const rows = b.items.map((item) => {
    const off = ctx.busy?.has(`l:${item?.id}`) ? " disabled" : "";
    return `<li data-learned="${e(item?.id)}"><span>${marked(item?.text ?? "", e)}</span>${learnedButton(e, "forget", item?.id, "Forget", off)}</li>`;
  }).join("");
  return `<div class="bb learned" data-how="${e(b.how === "learned" ? "learned" : "used")}"><p class="bb-label">${lead}: ${b.items.length} ${b.items.length === 1 ? "item" : "items"}</p><ul class="memory">${rows}</ul></div>`;
}

/** Ask mode: one proposed item with Accept, Reject and Edit; once answered it reads as kept. */
export function learnProposalHTML(b, ctx, e) {
  const item = b.item;
  if (b.gone || !item || typeof item !== "object") return `<p class="bb-gone">${e(BOTS_COPY.learnedGone)}</p>`;
  const off = ctx.busy?.has(`l:${item.id}`) ? " disabled" : "";
  const text = `<p class="bb-label">${marked(item.text ?? "", e)}</p>`;
  if (ctx.editing?.has(item.id)) return `<div class="bb learn-proposal" data-state="editing">${learnedEditHTML(item.id, item.text ?? "", e)}</div>`;
  if (b.state === "accepted") return `<div class="bb learn-proposal" data-state="accepted">${text}<p class="bb-settled">Kept${item.edited ? " · edited" : ""}</p><div class="bb-btns">${learnedButton(e, "edit", item.id, "Edit", off)}${learnedButton(e, "forget", item.id, "Forget", off)}</div></div>`;
  return `<div class="bb learn-proposal" data-state="open">${text}<div class="bb-btns">${learnedButton(e, "accept", item.id, "Accept", off)}${learnedButton(e, "reject", item.id, "Reject", off)}${learnedButton(e, "edit", item.id, "Edit", off)}</div></div>`;
}

const LEARNED_GROUPS = [
  ["proposed", "Waiting for your answer"],
  ["person", "What I know about you"],
  ["shared", "Shared with a project"],
  ["earlier", "Earlier versions"],
  ["old", "Not used lately"],
];
const LEARNED_ACTIONS = {
  proposed: ["accept", "reject", "edit"],
  person: ["edit", "pin", "forget"],
  shared: ["forget"],
  earlier: ["forget"],
  old: ["edit", "pin", "forget"],
};
const LEARNED_LABEL = { accept: "Accept", reject: "Reject", edit: "Edit", pin: "Pin", unpin: "Unpin", forget: "Forget" };

function learnedRowHTML(item, group, sheet, e) {
  const off = sheet.busy ? " disabled" : "";
  const src = item.source || {};
  const wording = item.kind === "note" ? ["forget"] : LEARNED_ACTIONS[group];
  const buttons = (sheet.deck && sheet.canShare && group === "person" && item.kind !== "note" ? [...wording, "share"] : wording).map((what) => {
    const real = what === "pin" && item.pinned ? "unpin" : what;
    return learnedButton(e, real, item.id, (real === "share" ? "Share with a project" : LEARNED_LABEL[real]), off);
  }).join("");
  const line = [item.kind, item.scope, src.bot_name, String(src.at || "").slice(0, 10), item.text_by === "person_edit" ? "edited" : "", item.pinned ? "pinned" : ""].filter(Boolean).map(e).join(" · ");
  const body = sheet.editing === item.id ? learnedEditHTML(item.id, item.text ?? "", e) : `<p class="learned-text">${marked(item.text ?? "", e)}</p>`;
  const note = item.note ? `<p class="learned-note">Bot’s note: ${marked(item.note, e)}</p>` : "";
  const from = src.bot_id ? `<a class="learned-from" href="#/bots/${enc(src.bot_id)}">Open conversation</a>` : "";
  return `<li data-learned="${e(item.id)}">${body}<p class="learned-src">${line}</p>${note}<div class="bb-btns">${buttons}${from}</div></li>`;
}

/** The review sheet: everything the person's bots learned, with its source, grouped. */
export function learnedSheetHTML(sheet, bot, e) {
  const items = Array.isArray(sheet.items) ? sheet.items : [];
  const sections = LEARNED_GROUPS.map(([key, title]) => {
    const rows = items.filter((item) => item.group === key);
    return rows.length ? `<section class="learned-group" data-group="${key}"><h3>${title}</h3><ul class="memory">${rows.map((item) => learnedRowHTML(item, key, sheet, e)).join("")}</ul></section>` : "";
  }).join("");
  const off = sheet.busy ? " disabled" : "";
  const paused = sheet.learning?.paused === true;
  const control = (what, label) => `<button type="button" class="bb-opt" data-bots-action="learned-bot" data-do="${what}"${off}>${label}</button>`;
  const controls = `${paused ? control("resume", "Resume learning") : control("pause", "Pause learning for 7 days")}${control("forget-all", sheet.confirmAll ? "Forget everything for good?" : `Forget everything ${e(bot.name ?? "")} learned`)}`;
  const sharing = sheet.shareItem ? `<section class="bot-access-card" data-learning-share><h3>Share these words</h3><p>${marked(items.find(item => item.id === sheet.shareItem)?.text || "", e)}</p>${sheet.sharePending ? `<div class="bot-access-preview"><h4>Who will see this</h4><p>${e(sheet.sharePending.label)}</p></div><div class="sheet-btns"><button class="bb-opt go" data-bots-action="learning-share-confirm"${off}>Share these words</button><button class="bb-opt" data-bots-action="learning-share-cancel"${off}>Cancel</button></div>` : `<form data-bots-form="learning-share"><label>Project audience<select name="project" required><option value="">Choose a project</option>${(sheet.projects || []).filter(project => !bot.board || project.slug === bot.board).map(project => `<option value="${e(project.slug)}">${e(project.name)}</option>`).join("")}</select></label><p>Everyone who can read this project will see these exact words and your name. Review the audience and words before sharing.</p><button class="bb-opt" type="submit"${off}>Preview sharing</button><button type="button" class="bb-opt" data-bots-action="learning-share-cancel">Cancel</button></form>`}</section>` : "";
  return `<div class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-h"><div class="sheet-body${sheet.deck ? " bot-access" : ""}"><h2 id="sheet-h">What ${e(bot.name ?? "")} learned</h2>${sharing}
    ${sheet.loading ? `<p class="bots-note">${e(BOTS_COPY.loading)}</p>` : sheet.shareItem ? "" : sections || '<p class="bots-note">Nothing learned yet.</p>'}
    ${sheet.error ? `<p class="bots-notice" role="alert">${e(sheet.error)}</p>` : ""}
    <div class="sheet-btns">${sheet.loading || sheet.shareItem ? "" : controls}<button type="button" class="bb-opt" data-bots-action="close" data-focus>Close</button></div>
  </div></div>`;
}

export function setupHTML(b, e) {
  const canConfigure = b.can_configure === true;
  return `<div class="bb setup"><p>${e(setupText(b.code, canConfigure))}</p>${canConfigure ? '<a class="bb-open" href="#/settings">Open settings</a>' : ""}</div>`;
}

function machinesHTML(b, e) {
  const list = Array.isArray(b.machines) ? b.machines : [];
  if (!list.length) return `<p class="bb-settled">No machines are available to you.</p>`;
  return `<ul class="bb machines">${list.map((m) => `<li><b>${e(m?.label)}</b><span>${m?.capabilities?.start_agent ? "can start work" : "cannot start work"}${m?.last_seen ? ` · seen ${e(elapsed(m.last_seen))} ago` : ""}</span></li>`).join("")}</ul>`;
}

/** Actions are small and share one line; every other block is a card of its own. */
export function actionsHTML(actions, e) {
  if (!actions.length) return "";
  return `<p class="acts">${actions.map((a) => `<span class="act${a.ok === false || a.state === "refused" ? " no" : ""}${a.state === "running" ? " run" : ""}">${e(a.label)}</span>`).join('<span class="sep" aria-hidden="true"> · </span>')}</p>`;
}

export function blocksHTML(blocks, ctx, e) {
  const out = [];
  let run = [];
  const flush = () => { if (run.length) out.push(actionsHTML(run, e)); run = []; };
  for (const b of Array.isArray(blocks) ? blocks : []) {
    if (!b || typeof b !== "object") continue;
    if (b.type === "action") { run.push(b); continue; }
    flush();
    if (b.type === "work") out.push(workHTML(b, ctx, e));
    else if (b.type === "decision") out.push(decisionHTML(b, ctx, e));
    else if (b.type === "confirm") out.push(confirmHTML(b, ctx, e));
    else if (b.type === "learned") out.push(learnedHTML(b, ctx, e));
    else if (b.type === "learn_proposal") out.push(learnProposalHTML(b, ctx, e));
    else if (b.type === "setup") out.push(setupHTML(b, e));
    else if (b.type === "machines") out.push(machinesHTML(b, e));
  }
  flush();
  return out.join("");
}

function noteHTML(m, e) {
  if (m.state === "stopped") return `<p class="bm-note">${e(BOTS_COPY.stopped)}</p>`;
  const code = /^failed:(.+)$/.exec(m.state || "")?.[1];
  if (!code || (code === "needs_configuration" && (m.blocks || []).some((b) => b?.type === "setup"))) return "";
  return `<p class="bm-note">${e(own(FAILS, code, "The reply failed."))}</p>`;
}

export function messageHTML(m, ctx, e) {
  const kind = m.role === "user" ? "me" : m.role === "event" ? "event" : "bot";
  const bubble = m.text ? `<div class="bm-bubble"><div class="bm-text">${marked(m.text, e)}</div></div>` : "";
  // An event carrying an orchestrator block is the orchestrator's own reply, forwarded word for word.
  const from = kind === "event" && Array.isArray(m.blocks) && m.blocks.some((b) => b?.type === "orchestrator") ? `<p class="bm-from">${ctx.deck ? BOTS_COPY.coworker : BOTS_COPY.orchestrator}</p>` : "";
  return `<li class="bm ${kind}" data-id="${e(m.id)}"${m.at ? ` title="${e(m.at)}"` : ""}>${from}${bubble}${blocksHTML(m.blocks, { ...ctx, mid: m.id }, e)}${kind === "bot" ? noteHTML(m, e) : ""}</li>`;
}

export function liveHTML(live, ctx, e) {
  const actions = (live?.actions || []).filter(Boolean);
  return `<li class="bm bot live"><div class="bm-bubble"><div class="bm-text" data-stream>${marked(live?.text ?? "", e)}</div></div>${actionsHTML(actions, e)}${blocksHTML(live?.blocks, { ...ctx, mid: "live" }, e)}</li>`;
}

export function waitingHTML(waiting, open, ctx, e) {
  if (!waiting.length) return "";
  return `<div class="wait"><button type="button" class="wait-toggle" data-bots-action="waiting" aria-expanded="${open}">${e(BOTS_COPY.waiting(waiting.length))}${chevron}</button>${open ? `<div class="wait-list">${waiting.map((b) => decisionHTML(b, ctx, e)).join("")}</div>` : ""}</div>`;
}

export function headHTML(bot, e) {
  return `<a class="bots-back" href="#/bots">${back}<span>${e(BOTS_COPY.title)}</span></a><button type="button" class="bots-name" data-bots-action="sheet" aria-haspopup="dialog"><span class="bot-av" aria-hidden="true">${e(initial(bot.name))}</span><span class="bots-title"><b>${e(bot.name)}</b>${bot.job ? `<small>${e(oneLine(bot.job))}</small>` : ""}</span></button>`;
}

export function coworkersNavHTML(bot = '-') {
  return `<a class="deck-coworkers-link" href="#/bots/${enc(bot)}/coworkers">Coworkers</a>`;
}

export function stripHTML(bots, bot, e) {
  // Keep the selected bot available even if the roster request has not finished yet.
  const list = bots.some((b) => b.id === bot.id) ? bots : [bot, ...bots];
  return `<nav class="deck-bots-strip" aria-label="Bots"><a class="bots-back" href="#/bots" aria-label="Back to bots">${back}</a><div class="deck-bot-chips">${list.map((b) => `<a class="deck-bot-chip" href="#/bots/${enc(b.id)}"${b.id === bot.id ? ' aria-current="page"' : ""}><span class="bot-av" aria-hidden="true">${e(initial(b.name))}</span><span>${e(b.name)}</span>${b.state === "needs_you" ? '<span class="deck-bot-wait" role="img" aria-label="Waiting on you"></span>' : ""}</a>`).join("")}</div><button type="button" class="deck-bot-settings" data-bots-action="sheet" aria-label="${e(bot.name)} settings and memory" title="Bot settings and memory"><span class="deck-bot-settings-name">${e(bot.name)}</span>${chevron}</button>${bot.board ? `<a class="deck-project-link" href="#/bots/${enc(bot.id)}" data-bots-action="project"><span class="deck-project-long">What ${e(bot.name)} is doing</span><span class="deck-project-short">Project</span>${chevron}</a>` : ""}</nav>`;
}

/** Mirror bots.blocks.work_status over the person's existing project read projection. */
export function projectWork(item) {
  let status, note = "";
  if (item.status === "done") status = "done";
  else if (item.status === "review") {
    if (item.review_kind !== "decision") status = "ready";
    else if (item.need === "you") status = "needs_you";
    else { status = "blocked"; note = "Waiting for an answer"; }
  } else if (item.waiting) {
    status = "blocked";
    note = item.waiting.text || "";
    if (item.waiting.code === "claim_expired") note = note.replace("hand the card off", "hand the task off");
  }
  else status = item.status === "doing" ? "working" : "queued";
  return { ...item, status, note: oneLine(note || item.status_line) };
}

export function projectHTML(sheet, bot, e, now = new Date()) {
  const week = new Date(now);
  week.setHours(0, 0, 0, 0);
  week.setDate(week.getDate() - (week.getDay() + 6) % 7);
  const groups = new Map([["Needs you", []], ["Working", []], ["Blocked", []], ["Queued", []], ["Done this week", []]]);
  for (const raw of sheet.items || []) {
    const item = projectWork(raw);
    if (item.status === "done") {
      const at = Date.parse(item.done_at);
      if (at >= week.getTime() && at <= now.getTime()) groups.get("Done this week").push(item);
    } else groups.get(own({ needs_you: "Needs you", ready: "Needs you", working: "Working", blocked: "Blocked" }, item.status, "Queued")).push(item);
  }
  const rows = [...groups].filter(([, items]) => items.length).map(([label, items]) => `<section class="deck-project-group"><h3>${label}</h3><ul>${items.map((item) => `<li><span class="chip ${item.status}">${STATUS[item.status]}</span><div><b>${marked(item.title ?? "", e)}</b>${item.note ? `<p>${marked(item.note, e)}</p>` : ""}</div></li>`).join("")}</ul></section>`).join("");
  return `<div class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-h"><div class="sheet-body deck-project"><header><div><h2 id="sheet-h">What ${e(bot.name)} is doing</h2>${bot.job ? `<p>${e(bot.job)}</p>` : ""}</div><button type="button" class="bb-opt" data-bots-action="close" data-focus>Close</button></header>${sheet.loading ? `<p class="bots-note">${e(BOTS_COPY.loading)}</p>` : sheet.error ? '<p class="bots-note" role="alert">This project’s work is unavailable. Close and try again.</p>' : rows || '<p class="bots-note">No open work or work finished this week.</p>'}</div></div>`;
}

export function composerHTML(bot, e) {
  return `<div class="bots-box"><textarea name="text" rows="1" maxlength="${MAX_TEXT}" aria-label="Message to ${e(bot.name)}" placeholder="Message ${e(bot.name)}" enterkeyhint="send"></textarea><span data-region="send"></span></div>`;
}

export function sendButtonHTML({ busy, canStop }) {
  return busy
    ? `<button type="button" class="bots-send stop" data-bots-action="stop"${canStop ? "" : " disabled"}>Stop</button>`
    : '<button type="submit" class="bots-send">Send</button>';
}

export function sheetHTML(sheet, bot, e) {
  if (sheet.kind === "sharing") return sharingHTML(sheet, bot, e);
  if (sheet.kind === "shared-thread") return threadPreviewHTML(sheet, bot, e);
  if (sheet.kind === "project") return projectHTML(sheet, bot, e);
  if (sheet.kind === "learned") return learnedSheetHTML(sheet, bot, e);
  if (sheet.kind === "new") {
    return `<div class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-h"><form class="sheet-body" data-bots-form="create"><h2 id="sheet-h">New bot</h2>
      <label>Name<input name="name" maxlength="60" required autocomplete="off" data-focus></label>
      <label>Job<input name="job" maxlength="200" autocomplete="off" placeholder="What it helps with"></label>
      <label>How to work<textarea name="instructions" maxlength="4000" rows="4" placeholder="Optional: tone, habits, things to avoid"></textarea></label>
      ${sheet.error ? `<p class="bots-notice" role="alert">${e(sheet.error)}</p>` : ""}
      <div class="sheet-btns"><button type="submit" class="bb-opt go"${sheet.busy ? " disabled" : ""}>Create</button><button type="button" class="bb-opt" data-bots-action="close">Cancel</button></div></form></div>`;
  }
  const memories = sheet.memories;
  return `<div class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-h"><div class="sheet-body"><h2 id="sheet-h">${e(bot.name)}</h2>
    <form data-bots-form="save">
      <label>Name<input name="name" maxlength="60" required autocomplete="off" value="${e(bot.name)}" data-focus></label>
      <label>Job<input name="job" maxlength="200" autocomplete="off" value="${e(bot.job ?? "")}"></label>
      <label>How to work<textarea name="instructions" maxlength="4000" rows="3">${e(bot.instructions ?? "")}</textarea></label>
      <div class="sheet-btns"><button type="submit" class="bb-opt go"${sheet.busy ? " disabled" : ""}>Save</button></div>
    </form>
    <h3>Learned</h3>
    <div class="sheet-btns"><button type="button" class="bb-opt" data-bots-action="learned-sheet">What ${e(bot.name)} learned</button></div>
    <h3>Memory</h3>
    ${memories === null ? `<p class="bots-note">${e(BOTS_COPY.loading)}</p>` : memories.length ? `<ul class="memory">${memories.map((m) => `<li><span>${marked(m.text, e)}</span><button type="button" class="bb-opt" data-bots-action="forget" data-id="${e(m.id)}" aria-label="Remove this note">Remove</button></li>`).join("")}</ul>` : `<p class="bots-note">Nothing remembered yet. Say “remember that…” in the chat, or add a note.</p>`}
    <form class="memory-add" data-bots-form="memory"><input name="text" maxlength="2000" aria-label="New note" placeholder="Add a note" autocomplete="off" required><button type="submit" class="bb-opt">Add</button></form>
    ${sheet.error ? `<p class="bots-notice" role="alert">${e(sheet.error)}</p>` : ""}
    <div class="sheet-btns"><button type="button" class="bb-opt" data-bots-action="archive">${sheet.confirmArchive ? "Archive for good?" : "Archive bot"}</button><button type="button" class="bb-opt" data-bots-action="close">Close</button></div>
  </div></div>`;
}

const shellHTML = `<div class="bots" data-pane="roster">
  <section class="bots-roster" aria-labelledby="bots-h">
    <header class="bots-bar"><h1 id="bots-h">${BOTS_COPY.title}</h1><p class="deck-bots-intro" hidden>One thread per bot. Tap one to talk.</p><button type="button" class="bots-new" data-bots-action="new">${BOTS_COPY.newBot}</button></header>
    <div data-region="model"></div>
    <ul class="bots-list" data-region="roster"></ul>
  </section>
  <section class="bots-thread" aria-label="Conversation">
    <header class="bots-head" data-region="head"></header>
    <ol class="bots-stream" data-region="messages" tabindex="-1"></ol>
    <div data-region="waiting"></div>
    <div data-region="notice"></div>
    <form class="bots-compose" data-region="composer" data-bots-form="send"></form>
  </section>
  <div data-region="sheet"></div>
</div>`;

const newRequestId = () => {
  const id = globalThis.crypto?.randomUUID?.().replaceAll("-", "");
  return id || `r${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
};
/** Questions waiting on the person across all their bots. The General bot's count already covers every
 * readable project, so project bots are not added on top of it (each question would count twice). */
export function waitingTotal(bots) {
  const n = (bot) => Number.isSafeInteger(bot?.waiting) && bot.waiting > 0 ? bot.waiting : 0;
  const list = Array.isArray(bots) ? bots : [];
  const general = list.find((bot) => bot?.kind === "general");
  return general ? n(general) : list.reduce((sum, bot) => sum + n(bot), 0);
}
const lastId = (messages) => messages.reduce((top, m) => Math.max(top, Number(m.id) || 0), 0);
const merge = (rows, added) => [...new Map([...rows, ...added].map((m) => [m.id, m])).values()].sort((a, b) => a.id - b.id);
const waitingIds = (list) => list.map((b) => b.card).sort().join(",");

// ---------- controller ----------

export function createBots({
  mount, api, e,
  setTimer = setTimeout, clearTimer = clearTimeout,
  hidden = () => globalThis.document?.hidden === true,
  phone = () => globalThis.matchMedia?.("(max-width:699px)")?.matches === true,
  go = (hash) => { globalThis.location.hash = hash; },
  send = sendMessage, fetchImpl,
  viewport = globalThis.visualViewport,
  makeId = newRequestId,
  onRoster = () => {},
}) {
  const s = {
    bots: [], model: null, canConfigure: false, loaded: false, error: "",
    botId: null, bot: null, messages: [], waiting: [], running: false, more: false, ready: false,
    open: new Set(), busy: new Set(), editing: new Set(), waitingOpen: false, notice: null,
    turn: null, unsent: null, sheet: null, threads: [], threadId: null,
    drafts: new Map(), readSent: new Map(),
  };
  let active = false, generation = 0, ticks = 0, loadingEarlier = false;
  const timers = { thread: null, roster: null };
  let rootEl = null, visibilityBound = false;

  const alive = (token) => active && token === generation;
  const host = () => mount();
  const region = (name) => host()?.querySelector(`[data-region="${name}"]`) ?? null;
  const atBottom = (node) => !node || node.scrollHeight - node.scrollTop - node.clientHeight <= 40;
  const busy = () => Boolean(s.turn) || s.running;
  const ctx = () => ({ open: s.open, busy: s.busy, editing: s.editing, deck: deck() });
  const runningRequestId = () => s.turn?.requestId || (s.running ? [...s.messages].reverse().find((m) => m.role === "user")?.request_id : "") || "";
  const deck = () => globalThis.document?.documentElement?.dataset?.ui === "deck";
  const draftKey = () => deck() && s.threadId ? `${s.botId}:${s.threadId}` : s.botId;
  const threadQuery = () => deck() && s.threadId ? `&thread=${enc(s.threadId)}` : "";
  const threadPath = (tail) => `/api/bots/${enc(s.botId)}/${tail}${threadQuery() ? `?thread=${enc(s.threadId)}` : ""}`;
  const canTalk = () => !deck() || s.bot && !s.bot.frozen && !s.bot.lifecycle && (s.bot.mine !== false || s.bot.perms?.talk);

  // --- painting ---
  function paintRoster() {
    const node = region("roster");
    if (node) node.innerHTML = rosterHTML({ bots: s.bots, current: s.botId, loaded: s.loaded }, e);
    const model = region("model");
    if (model) model.innerHTML = modelNoteHTML({ model: s.model, canConfigure: s.canConfigure, error: s.error }, e);
    if (deck()) paintHead();
  }
  function paintMessages({ force = false } = {}) {
    const node = region("messages");
    if (!node) return;
    const stick = force || atBottom(node), top = node.scrollTop;
    const parts = [];
    if (!s.botId) parts.push(`<li class="bots-empty">${e(BOTS_COPY.pick)}</li>`);
    else if (!s.ready) parts.push(`<li class="bots-empty">${e(s.notice?.gone ? BOTS_COPY.unavailable : BOTS_COPY.loading)}</li>`);
    else {
      if (s.more) parts.push(`<li class="bots-more"><button type="button" data-bots-action="earlier">${e(BOTS_COPY.earlier)}</button></li>`);
      for (const m of s.messages) parts.push(messageHTML(m, ctx(), e));
      if (s.turn && !s.turn.acked) parts.push(`<li class="bm me echo"><div class="bm-bubble"><div class="bm-text">${marked(s.turn.text, e)}</div></div></li>`);
      if (s.turn || s.running) parts.push(liveHTML(s.turn?.live, ctx(), e));
      if (!s.messages.length && !s.turn && !s.running) parts.push(`<li class="bots-empty">${e(BOTS_COPY.hint(s.bot?.name || "the bot"))}</li>`);
    }
    node.innerHTML = parts.join("");
    node.scrollTop = stick ? node.scrollHeight : top;
  }
  function paintWaiting() {
    const node = region("waiting");
    if (node) node.innerHTML = s.botId ? waitingHTML(s.waiting, s.waitingOpen, ctx(), e) : "";
  }
  function paintNotice() {
    const node = region("notice");
    if (!node) return;
    node.innerHTML = s.notice && !s.notice.gone ? `<p class="bots-notice" role="${s.notice.tone === "info" ? "status" : "alert"}">${e(s.notice.text)}</p>` : "";
  }
  function paintSend() {
    const node = region("send");
    if (node) node.innerHTML = sendButtonHTML({ busy: busy(), canStop: Boolean(runningRequestId()) });
  }
  function paintSheet() {
    const node = region("sheet");
    if (!node) return;
    node.innerHTML = s.sheet ? sheetHTML(s.sheet, s.bot || {}, e) : "";
    if (s.sheet && !s.sheet.painted) { s.sheet.painted = true; node.querySelector?.("[data-focus]")?.focus?.(); }
  }
  function paintThread() {
    host()?.querySelector(".bots")?.setAttribute?.("data-pane", s.botId ? "thread" : "roster");
    const composer = region("composer");
    paintHead();
    if (composer) {
      composer.innerHTML = s.bot && canTalk() ? composerHTML(s.bot, e) : "";
      const box = composer.querySelector?.('textarea[name="text"]');
      if (box && s.drafts.get(draftKey())) { box.value = s.drafts.get(draftKey()); fit(box); }
      takeDraft();
    }
    paintMessages({ force: true });
    paintWaiting();
    paintNotice();
    paintSend();
  }
  function paintHead() {
    const head = region("head");
    if (head) head.innerHTML = s.bot ? deck() ? stripHTML(s.bots, s.bot, e) + coworkersNavHTML(s.bot.id) + threadsHTML(s.bot, s.threads, s.threadId, e) : headHTML(s.bot, e) : "";
  }
  function takeDraft() {
    const box = composerBox();
    if (!s.bot || !s.ready || !box || s.notice?.gone || deck() && s.threadId) return;
    try {
      const storage = globalThis.sessionStorage, raw = storage?.getItem("cmmn.deck.draft");
      if (raw === null || raw === undefined) return;
      let draft;
      try { draft = JSON.parse(raw); } catch { storage.removeItem("cmmn.deck.draft"); return; }
      if (!draft || typeof draft.bot !== "string" || typeof draft.text !== "string") { storage.removeItem("cmmn.deck.draft"); return; }
      if (draft.bot !== s.botId) return;
      setComposer(draft.text);
      box.focus?.();
      storage.removeItem("cmmn.deck.draft");
    } catch { /* Storage can be disabled; the composer still works. */ }
  }
  function fit(box) {
    if (!box?.style) return;
    box.style.height = "auto";
    box.style.height = `${Math.min(box.scrollHeight || 0, 160)}px`;
  }
  const composerBox = () => region("composer")?.querySelector?.('textarea[name="text"]') ?? null;
  function setComposer(text) {
    const box = composerBox();
    if (box) { box.value = text; fit(box); }
    if (text) s.drafts.set(draftKey(), text); else s.drafts.delete(draftKey());
  }
  function updateStream() {
    const node = region("messages");
    const live = node?.querySelector?.("[data-stream]");
    if (!live) { paintMessages(); return; }
    const stick = atBottom(node);
    live.innerHTML = marked(s.turn?.live?.text ?? "", e);
    if (stick) node.scrollTop = node.scrollHeight;
  }
  function repaintAll() { paintMessages(); paintWaiting(); paintSend(); }

  // --- reading ---
  function takePage(page, { replace = false } = {}) {
    if (page.bot) s.bot = { ...s.bot, ...page.bot };
    if (Array.isArray(page.messages)) s.messages = replace ? merge([], page.messages) : merge(s.messages, page.messages);
    if (Array.isArray(page.waiting)) s.waiting = page.waiting;
    s.running = page.running === true;
  }
  function markRead() {
    const top = lastId(s.messages);
    const key = `${s.botId}:${s.threadId || ""}`;
    if (!s.botId || !s.ready || hidden() || !top || (s.readSent.get(key) || 0) >= top) return;
    const id = s.botId;
    s.readSent.set(key, top);
    const row = s.bots.find((b) => b.id === id);
    if (row && row.unread) { row.unread = 0; paintRoster(); }
    api(threadPath("read"), { method: "POST", body: { upto: top } }).catch(() => { if ((s.readSent.get(key) || 0) === top) s.readSent.delete(key); });
  }
  async function loadRoster(token) {
    try {
      const result = await api("/api/bots");
      if (!alive(token)) return;
      s.bots = result.bots || []; s.model = result.model || null; s.canConfigure = result.can_configure === true;
      if (deck() && s.canConfigure) {
        try {
          const all = await api("/api/bots/shared");
          if (!alive(token)) return;
          for (const bot of all.bots || []) if (!s.bots.some(row => row.id === bot.id)) s.bots.push({ ...bot, mine: false, lifecycle: true, shared: true, kind: "custom" });
        } catch { /* The personal roster remains available if workspace management is unavailable. */ }
      }
      s.loaded = true; s.error = "";
      if (s.botId) s.bot = s.bots.find((b) => b.id === s.botId) || null;
      onRoster(s.bots);
    } catch (error) {
      if (!alive(token)) return;
      s.loaded = true; s.error = error.message || BOTS_COPY.offline;
    }
    paintRoster();
  }
  async function loadThread(token, id) {
    const selected = s.threadId;
    try {
      if (deck() && (s.bot?.frozen || s.bot?.lifecycle)) {
        s.ready = false; paintThread();
        await openSharing();
        return;
      }
      if (deck() && s.bot?.shared) {
        const listed = await api(`/api/bots/${enc(id)}/threads`);
        if (!alive(token) || s.botId !== id || s.threadId !== selected) return;
        s.threads = listed.threads || [];
        if (!s.bot.mine && !s.bot.perms?.talk && !s.threadId) s.threadId = s.threads.find(t => t.visibility === "shared" && !t.archived)?.id || null;
        if (!s.bot.mine && !s.bot.perms?.talk && !s.threadId) { s.ready = true; paintThread(); return; }
      }
      const thread = s.threadId;
      const page = await api(`/api/bots/${enc(id)}/messages?limit=${PAGE}${threadQuery()}`);
      if (!alive(token) || s.botId !== id) return;
      if (deck() && s.threadId !== thread) return;
      takePage(page, { replace: true });
      s.more = (page.messages || []).length >= PAGE;
      s.ready = true; s.notice = null;
    } catch (error) {
      if (!alive(token) || s.botId !== id) return;
      if (error.status === 404) { s.notice = { gone: true, text: BOTS_COPY.unavailable }; s.bot = null; }
      else s.notice = { text: error.message || BOTS_COPY.offline, tone: "error" };
    }
    paintThread();
    markRead();
    scheduleThread(token);
  }
  async function reloadTail(token = generation) {
    if (!s.botId) return;
    const id = s.botId;
    try {
      const selected = s.threadId;
      const page = await api(`/api/bots/${enc(id)}/messages?limit=${PAGE}${threadQuery()}`);
      if (!alive(token) || s.botId !== id || s.threadId !== selected) return;
      takePage(page);
      repaintAll();
      markRead();
    } catch { /* the next poll tries again */ }
  }
  async function pollThread(token) {
    const id = s.botId;
    if (!id || !s.ready || hidden() || deck() && (s.bot?.frozen || s.bot?.lifecycle || !s.bot?.mine && !s.bot?.perms?.talk && !s.threadId && s.bot?.shared)) return;
    ticks++;
    try {
      // The newest messages by id; every sixth round re-reads the tail so older blocks show their current state.
      const refresh = ticks % 6 === 0;
      const selected = s.threadId;
      const page = await api(`/api/bots/${enc(id)}/messages?${refresh ? "" : `after=${lastId(s.messages)}&`}limit=${PAGE}${threadQuery()}`);
      if (!alive(token) || s.botId !== id || s.threadId !== selected) return;
      const before = waitingIds(s.waiting), wasRunning = s.running;
      takePage(page);
      if (s.turn?.state === "polling" && !s.running) { s.turn = null; s.notice = null; }
      if (before !== waitingIds(s.waiting) && !refresh) { await reloadTail(token); }
      else repaintAll();
      if (wasRunning && !s.running) void loadRoster(token);
      if (s.notice?.offline) { s.notice = null; paintNotice(); }
      markRead();
    } catch (error) {
      if (!alive(token) || s.botId !== id) return;
      if (error.status === 404) { s.notice = { gone: true, text: BOTS_COPY.unavailable }; s.ready = false; paintThread(); return; }
      s.notice = { offline: true, text: BOTS_COPY.offline, tone: "info" };
      paintNotice();
    }
  }
  function scheduleThread(token) {
    clearTimer(timers.thread);
    timers.thread = null;
    if (!alive(token) || !s.botId) return;
    timers.thread = setTimer(async () => {
      timers.thread = null;
      if (!alive(token)) return;
      await pollThread(token);
      scheduleThread(token);
    }, busy() ? 1500 : 5000);
  }
  function scheduleRoster(token) {
    clearTimer(timers.roster);
    timers.roster = null;
    if (!alive(token)) return;
    timers.roster = setTimer(async () => {
      timers.roster = null;
      if (!alive(token)) return;
      if (!hidden() && (!s.botId || !phone())) await loadRoster(token);
      scheduleRoster(token);
    }, 15000);
  }
  async function earlier() {
    const first = s.messages[0]?.id, id = s.botId;
    if (!first || loadingEarlier) return;
    loadingEarlier = true;
    try {
      const selected = s.threadId;
      const page = await api(`/api/bots/${enc(id)}/messages?before=${first}&limit=${PAGE}${threadQuery()}`);
      if (s.botId !== id || s.threadId !== selected) return;
      s.messages = merge(s.messages, page.messages || []);
      s.more = (page.messages || []).length >= PAGE;
      const node = region("messages"), height = node?.scrollHeight || 0, top = node?.scrollTop || 0;
      paintMessages();
      if (node) node.scrollTop = top + (node.scrollHeight - height);
    } catch (error) { s.notice = { text: error.message, tone: "error" }; paintNotice(); }
    finally { loadingEarlier = false; }
  }

  // --- sending ---
  function onStream(turn, name, data) {
    if (s.turn !== turn) return;
    if (name === "ack") {
      turn.acked = data?.message?.id ?? true; turn.state = "streaming";
      if (data?.message) s.messages = merge(s.messages, [data.message]);
      paintMessages({ force: true });
    } else if (name === "delta") {
      turn.live.text += String(data?.text ?? "");
      updateStream();
    } else if (name === "action") {
      turn.live.actions[Number.isFinite(data?.step) ? data.step : turn.live.actions.length] = { label: data?.label, state: data?.state, ok: data?.state === "refused" ? false : true };
      paintMessages();
    } else if (name === "block") {
      if (data && data.type !== "action") { turn.live.blocks.push(data); paintMessages(); }
    } else if (name === "done") {
      if (data?.message) s.messages = merge(s.messages, [data.message]);
      turn.finished = true;
    } else if (name === "error") {
      if (data?.message) s.messages = merge(s.messages, [data.message]);
      turn.finished = true;
      if (!data?.message && data?.text) s.notice = { text: data.text, tone: "error" };
    } else if (name === "running") turn.state = "polling";
  }
  async function sendText(raw) {
    const text = String(raw ?? "").trim();
    if (!text || text.length > MAX_TEXT || busy() || !s.botId || !s.ready || !canTalk()) return;
    const botId = s.botId;
    const retry = s.unsent?.botId === botId && s.unsent.text === text;
    const requestId = retry ? s.unsent.requestId : makeId();
    s.unsent = null; s.notice = null;
    const turn = s.turn = { botId, requestId, text, acked: null, live: { text: "", actions: [], blocks: [] }, state: "sending", finished: false, controller: new AbortController() };
    setComposer("");
    paintNotice(); paintMessages({ force: true }); paintSend();
    scheduleThread(generation);
    try {
      const result = await send({ botId, ...(deck() && s.threadId ? { threadId: s.threadId } : {}), text, requestId, signal: turn.controller.signal, fetchImpl, onEvent: (name, data) => onStream(turn, name, data) });
      if (s.turn !== turn) return;
      if (result.state === "dropped" || result.state === "running") {
        // The turn goes on without us: poll until the server says it is no longer running.
        turn.state = "polling";
        s.running = true;
        paintSend();
        void pollThread(generation);
        return;
      }
      s.turn = null;
      repaintAll();
      scheduleThread(generation);
      void pollThread(generation);
      void loadRoster(generation);
    } catch (error) {
      if (s.turn !== turn) return;
      s.turn = null;
      s.unsent = { botId, requestId, text };
      if (error.status === 404) { s.notice = { gone: true, text: BOTS_COPY.unavailable }; s.unsent = null; void loadRoster(generation); }
      else { setComposer(text); s.notice = { text: `${error.message} Send again to retry.`, tone: "error" }; }
      paintThread();
      if (error.status === 404) paintMessages();
      scheduleThread(generation);
    }
  }
  async function stop() {
    const requestId = runningRequestId();
    if (!requestId || !s.botId) return;
    try { await api(threadPath("stop"), { method: "POST", body: { request_id: requestId } }); }
    catch (error) { s.notice = { text: error.message, tone: "error" }; paintNotice(); }
  }

  // --- answering inside the thread ---
  async function decide(n, version, payload) {
    if (!n || s.busy.has(n)) return;
    s.busy.add(n); repaintAll();
    try {
      await api(`/api/cards/${n}/decide`, { method: "POST", body: { version: Number(version), ...payload } });
      s.notice = null; paintNotice();
    } catch (error) {
      s.notice = error.status === 409 ? { text: "That question changed. Showing the latest.", tone: "info" } : { text: error.message, tone: "error" };
      paintNotice();
    } finally { s.busy.delete(n); }
    await reloadTail();
    void loadRoster(generation);
  }
  async function confirm(pending, accept) {
    const key = `p:${pending}`;
    if (!pending || s.busy.has(key) || !s.botId) return;
    const id = s.botId, thread = s.threadId;
    s.busy.add(key); repaintAll();
    try {
      const result = await api(threadPath("confirm"), { method: "POST", body: { pending, accept } });
      if (s.botId !== id || s.threadId !== thread) return;
      if (result?.message) s.messages = merge(s.messages, [result.message]);
      s.notice = null; paintNotice();
    } catch (error) { s.notice = { text: error.message, tone: "error" }; paintNotice(); }
    finally { s.busy.delete(key); }
    await reloadTail();
    void loadRoster(generation);
  }

  // --- sheets ---
  async function loadSharing(sheet) {
    const id = s.botId, bot = s.bot;
    sheet.loading = true; sheet.error = ""; paintSheet();
    try {
      const [me, settings] = await Promise.all([api("/api/me"), api("/api/bots/settings")]);
      const [shares, access, log] = bot.frozen || bot.lifecycle
        ? [{ shares: bot.members || [] }, { entries: bot.access || [] }, { log: [] }]
        : await Promise.all([api(`/api/bots/${enc(id)}/shares`), api(`/api/bots/${enc(id)}/access`), api(`/api/bots/${enc(id)}/access/log?limit=50`)]);
      if (s.sheet !== sheet || s.botId !== id) return;
      Object.assign(sheet, { me, settings, shares: shares.shares, access, log: log.log, moreLog: log.log.length === 50 });
    } catch (error) { if (s.sheet === sheet) sheet.error = error.message || "Bot settings are unavailable. Try again."; }
    if (s.sheet === sheet) { sheet.loading = false; paintSheet(); }
  }
  async function openSharing() {
    if (!deck() || !s.bot) return;
    const sheet = s.sheet = { kind: "sharing", tab: "people", shares: [], log: [], busy: false, loading: true, error: "", painted: false };
    await loadSharing(sheet);
  }
  async function sharingWrite(action) {
    const sheet = s.sheet, bot = s.bot;
    if (!deck() || sheet?.kind !== "sharing" || sheet.busy || sheet.loading || !bot) return;
    const memberActions = ["grant", "revoke"];
    if (memberActions.includes(action) && (!bot.mine || bot.frozen || !sheet.selected || !sheet.settings?.sharing)) return;
    if (["disable", "enable", "adopt", "sharing-enable"].includes(action) && !sheet.me?.can_admin) return;
    if (action === "revoke" && !sheet.confirmRevoke) { sheet.confirmRevoke = true; paintSheet(); return; }
    if (action === "disable" && !sheet.confirmDisable) { sheet.confirmDisable = true; paintSheet(); return; }
    const id = s.botId;
    let path = `/api/bots/${enc(id)}/${action}`, method = "POST", body = {};
    if (action === "sharing-enable") { path = "/api/bots/settings"; method = "PATCH"; body = { sharing: true }; }
    if (memberActions.includes(action)) {
      path = `/api/bots/${enc(id)}/shares`;
      const exists = sheet.shares.some(row => row.user.id === sheet.selected);
      if (action === "revoke" || exists) path += `/${enc(sheet.selected)}`;
      method = action === "revoke" ? "DELETE" : exists ? "PATCH" : "POST";
      body = action === "revoke" ? undefined : { ...(!exists ? { user: sheet.selected } : {}), talk: sheet.draft.talk === true, see_shared: sheet.draft.see_shared === true };
      if (action === "grant" && !body.talk && !body.see_shared) return;
    }
    const result = await sheetCall(sheet, () => api(path, { method, ...(body ? { body } : {}) }));
    if (!result || s.sheet !== sheet || s.botId !== id) return;
    sheet.selected = null; sheet.picker = false; sheet.confirmRevoke = false; sheet.confirmDisable = false; sheet.saved = true;
    await loadRoster(generation);
    if (s.sheet !== sheet || s.botId !== id) return;
    await loadSharing(sheet);
    if (["disable", "enable", "adopt"].includes(action)) {
      s.turn?.controller.abort(); s.turn = null; s.unsent = null; s.messages = []; s.waiting = []; s.running = false; s.ready = false;
      if (action !== "disable") await loadThread(generation, id);
    }
    paintThread();
  }
  async function moreSharingLog() {
    const sheet = s.sheet;
    if (sheet?.kind !== "sharing" || sheet.busy || !sheet.moreLog) return;
    const result = await sheetCall(sheet, () => api(`/api/bots/${enc(s.botId)}/access/log?after=${sheet.log.at(-1).id}&limit=50`));
    if (result && s.sheet === sheet) { sheet.log.push(...result.log); sheet.moreLog = result.log.length === 50; paintSheet(); }
  }
  function selectShare(user) {
    const sheet = s.sheet;
    if (!deck() || sheet?.kind !== "sharing" || !s.bot?.mine || s.bot.frozen || !sheet.me?.people?.[`user:${user}`]) return;
    const row = sheet.shares.find(row => row.user.id === user);
    sheet.selected = user; sheet.picker = false; sheet.confirmRevoke = false;
    sheet.draft = { talk: row?.talk === true, see_shared: row?.see_shared === true };
    paintSheet();
    region("sheet")?.querySelector?.("[data-permission-editor]")?.scrollIntoView?.({ block: "nearest" });
  }
  async function openSharedThread() {
    if (!deck() || !s.bot?.shared || !canTalk()) return;
    const id = s.botId, sheet = s.sheet = { kind: "shared-thread", loading: true, shares: [], error: "" };
    paintSheet();
    try {
      const result = await api(`/api/bots/${enc(id)}/shares`);
      if (s.sheet === sheet && s.botId === id) sheet.shares = result.shares;
    } catch (error) { if (s.sheet === sheet) sheet.error = error.message; }
    if (s.sheet === sheet) { sheet.loading = false; paintSheet(); }
  }
  async function switchThread(id) {
    if (!deck() || !s.bot?.shared || id && !s.threads.some(t => t.id === id)) return;
    s.turn?.controller.abort(); s.turn = null; s.unsent = null;
    s.threadId = id || null; s.messages = []; s.waiting = []; s.running = false; s.ready = false; s.more = false; s.notice = null;
    const token = ++generation;
    scheduleRoster(token);
    paintThread();
    await loadThread(token, s.botId);
  }
  async function createSharedThread(form) {
    const sheet = s.sheet;
    if (sheet?.kind !== "shared-thread" || sheet.loading || sheet.error || sheet.busy || !canTalk()) return;
    const result = await sheetCall(sheet, () => api(`/api/bots/${enc(s.botId)}/threads`, { method: "POST", body: { visibility: "shared", title: form.elements.title.value.trim() } }));
    if (!result?.thread || s.sheet !== sheet) return;
    s.threads.push(result.thread); closeSheet();
    await switchThread(result.thread.id);
  }
  async function openSheet(kind) {
    if (kind === "bot" && deck()) return openSharing();
    if (kind === "project") {
      if (!deck() || !s.bot?.board) return;
      const sheet = s.sheet = { kind, loading: true, items: [], error: "", painted: false };
      const token = generation, id = s.botId;
      paintSheet();
      try {
        const result = await api(`/api/boards/${enc(s.bot.board)}/cards`);
        if (alive(token) && s.botId === id && s.sheet === sheet) sheet.items = [...(result.cards || []), ...(result.children || [])];
      } catch { if (alive(token) && s.sheet === sheet) sheet.error = "unavailable"; }
      if (alive(token) && s.sheet === sheet) { sheet.loading = false; sheet.painted = false; paintSheet(); }
      return;
    }
    s.sheet = { kind, busy: false, error: "", confirmArchive: false, memories: ["bot", "details"].includes(kind) ? null : undefined, painted: false };
    paintSheet();
    if (!["bot", "details"].includes(kind)) return;
    const sheet = s.sheet, id = s.botId;
    try {
      const result = await api(`/api/bots/${enc(id)}/memory`);
      if (s.sheet === sheet) { sheet.memories = result.memories || []; paintSheet(); }
    } catch (error) { if (s.sheet === sheet) { sheet.memories = []; sheet.error = error.message; paintSheet(); } }
  }
  // --- what the bots learned: the review sheet and the thread blocks share these calls ---
  async function loadLearned(sheet) {
    const id = s.botId;
    try {
      const [list, learning] = await Promise.all([api(deck() ? `/api/learned?bot=${enc(id)}&limit=100` : "/api/learned?limit=100"), api(`/api/bots/${enc(id)}/learning`)]);
      if (s.sheet !== sheet) return;
      sheet.items = list.items || [];
      sheet.learning = learning.learning || null;
      if (sheet.deck && sheet.canShare) { const projects = await api("/api/boards"); if (s.sheet === sheet) sheet.projects = projects.boards || []; }
    } catch (error) { if (s.sheet === sheet) sheet.error = error.message; }
    if (s.sheet === sheet) { sheet.loading = false; paintSheet(); }
  }
  async function openLearned() {
    if (!s.botId) return;
    if (deck() && (!s.bot?.mine || s.bot.frozen)) return;
    const sheet = s.sheet = { kind: "learned", deck: deck(), canShare: !s.bot?.shared, loading: true, items: [], learning: null, busy: false, error: "", confirmAll: false, editing: null, painted: false };
    paintSheet();
    await loadLearned(sheet);
  }
  const LEARNED_CALLS = {
    accept: (id) => [`/api/learned/${enc(id)}/accept`, "POST", {}],
    reject: (id) => [`/api/learned/${enc(id)}/reject`, "POST", {}],
    forget: (id) => [`/api/learned/${enc(id)}`, "DELETE"],
    pin: (id) => [`/api/learned/${enc(id)}`, "PATCH", { pinned: true }],
    unpin: (id) => [`/api/learned/${enc(id)}`, "PATCH", { pinned: false }],
  };
  /** One tap on an item, from the sheet or from the thread; the thread and the sheet both refresh. */
  async function learnedAct(what, id, extra) {
    if (!id) return;
    const sheet = s.sheet?.kind === "learned" ? s.sheet : null;
    if (what === "share") {
      if (sheet?.deck && sheet.canShare && sheet.items.some(item => item.id === id && item.group === "person" && item.kind !== "note")) { sheet.shareItem = id; sheet.sharePending = null; paintSheet(); region("sheet")?.querySelector?.("[data-learning-share]")?.scrollIntoView?.({ block: "nearest" }); }
      return;
    }
    if (what === "edit" || what === "cancel") {
      if (sheet) { sheet.editing = what === "edit" ? id : null; paintSheet(); }
      else { if (what === "edit") s.editing.add(id); else s.editing.delete(id); paintMessages(); }
      return;
    }
    const key = `l:${id}`;
    const call = extra ? [`/api/learned/${enc(id)}`, "PATCH", extra] : LEARNED_CALLS[what]?.(id);
    if (!call || s.busy.has(key)) return;
    s.busy.add(key);
    if (sheet) { sheet.busy = true; sheet.error = ""; paintSheet(); } else repaintAll();
    try {
      await api(call[0], { method: call[1], ...(call[2] ? { body: call[2] } : {}) });
      s.editing.delete(id);
      if (sheet) sheet.editing = null;
      s.notice = null; paintNotice();
    } catch (error) {
      if (sheet) sheet.error = error.message; else { s.notice = { text: error.message, tone: "error" }; paintNotice(); }
    } finally {
      s.busy.delete(key);
      if (sheet) sheet.busy = false;
    }
    if (sheet && s.sheet === sheet) await loadLearned(sheet);
    await reloadTail();
  }
  async function learnedBot(what) {
    const sheet = s.sheet, id = s.botId;
    if (sheet?.kind !== "learned" || sheet.busy || !id) return;
    if (what === "forget-all" && !sheet.confirmAll) { sheet.confirmAll = true; paintSheet(); return; }
    const call = what === "forget-all"
      ? [`/api/bots/${enc(id)}/learned/forget-all`, "POST", {}]
      : [`/api/bots/${enc(id)}/learning`, "PATCH", { pause: what === "pause" ? 7 : null }];
    sheet.confirmAll = false;
    await sheetCall(sheet, () => api(call[0], { method: call[1], body: call[2] }));
    if (s.sheet === sheet) await loadLearned(sheet);
    await reloadTail();
  }
  function closeSheet() { const project = s.sheet?.kind === "project"; s.sheet = null; paintSheet(); region("head")?.querySelector?.(deck() ? project ? ".deck-project-link" : ".deck-bot-settings" : ".bots-name")?.focus?.(); }
  async function previewLearningShare(form) {
    const sheet = s.sheet;
    if (sheet?.kind !== "learned" || !sheet.deck || !sheet.canShare || sheet.busy || !sheet.shareItem) return;
    const result = await sheetCall(sheet, () => api(`/api/learned/${enc(sheet.shareItem)}/share`, { method: "POST", body: { project: form.elements.project.value } }));
    if (result && s.sheet === sheet) { sheet.sharePending = result; paintSheet(); }
  }
  async function confirmLearningShare(accept) {
    const sheet = s.sheet, pending = sheet?.sharePending;
    if (sheet?.kind !== "learned" || sheet.busy) return;
    if (pending) {
      const result = await sheetCall(sheet, () => api(`/api/bots/${enc(pending.bot)}/confirm`, { method: "POST", body: { pending: pending.pending, accept } }));
      if (!result || s.sheet !== sheet) return;
    }
    sheet.shareItem = null; sheet.sharePending = null;
    await loadLearned(sheet);
  }
  async function sheetCall(sheet, work) {
    sheet.busy = true; sheet.error = ""; paintSheet();
    try { return await work(); }
    catch (error) { sheet.error = error.message; return undefined; }
    finally { sheet.busy = false; if (s.sheet === sheet) paintSheet(); }
  }
  async function create(form) {
    const sheet = s.sheet;
    if (!sheet || sheet.busy) return;
    const body = { name: form.elements.name.value.trim() };
    for (const key of ["job", "instructions"]) { const value = form.elements[key].value.trim(); if (value) body[key] = value; }
    if (!body.name) return;
    const result = await sheetCall(sheet, () => api("/api/bots", { method: "POST", body }));
    if (!result?.bot || s.sheet !== sheet) return;
    s.sheet = null; paintSheet();
    await loadRoster(generation);
    go(`#/bots/${enc(result.bot.id)}`);
  }
  async function save(form) {
    const sheet = s.sheet, id = s.botId;
    if (!sheet || sheet.busy || !s.bot || deck() && (!s.bot.mine || s.bot.frozen)) return;
    const body = {};
    for (const key of ["name", "job", "instructions"]) {
      const value = form.elements[key].value.trim();
      if (value !== String(s.bot[key] ?? "")) body[key] = value;
    }
    if (!Object.keys(body).length) { closeSheet(); return; }
    if (body.name === "") return;
    const result = await sheetCall(sheet, () => api(`/api/bots/${enc(id)}`, { method: "PATCH", body }));
    if (!result?.bot || s.botId !== id) return;
    s.bot = { ...s.bot, ...result.bot };
    s.bots = s.bots.map((b) => b.id === id ? { ...b, ...result.bot } : b);
    s.sheet = null; paintSheet(); paintRoster(); paintThread();
  }
  async function addMemory(form) {
    const sheet = s.sheet, id = s.botId, text = form.elements.text.value.trim();
    if (!sheet || sheet.busy || !text) return;
    const result = await sheetCall(sheet, () => api(`/api/bots/${enc(id)}/memory`, { method: "POST", body: { text } }));
    if (result?.memory && s.sheet === sheet) { sheet.memories = [...(sheet.memories || []), result.memory]; paintSheet(); }
  }
  async function forget(memoryId) {
    const sheet = s.sheet, id = s.botId;
    if (!sheet || sheet.busy) return;
    const result = await sheetCall(sheet, () => api(`/api/bots/${enc(id)}/memory/${enc(memoryId)}`, { method: "DELETE" }));
    if (result && s.sheet === sheet) { sheet.memories = (sheet.memories || []).filter((m) => String(m.id) !== String(memoryId)); paintSheet(); }
  }
  async function archive() {
    const sheet = s.sheet, id = s.botId;
    if (!sheet || sheet.busy || deck() && (!s.bot?.mine || s.bot.frozen)) return;
    if (!sheet.confirmArchive) { sheet.confirmArchive = true; paintSheet(); return; }
    const result = await sheetCall(sheet, () => api(`/api/bots/${enc(id)}`, { method: "PATCH", body: { archived: true } }));
    if (!result?.bot) return;
    s.bots = s.bots.filter((b) => b.id !== id);
    s.sheet = null;
    go("#/bots");
  }

  // --- events ---
  function click(el) {
    const d = el?.dataset || {};
    switch (d.botsAction) {
      case "new": void openSheet("new"); break;
      case "sheet": void openSheet("bot"); break;
      case "bot-details": if (deck() && s.bot?.mine && !s.bot.frozen) void openSheet("details"); break;
      case "sharing-tab": if (s.sheet?.kind === "sharing") { s.sheet.tab = d.tab === "log" ? "log" : "people"; paintSheet(); } break;
      case "sharing-picker": if (s.sheet?.kind === "sharing" && s.bot?.mine && !s.bot.frozen) { s.sheet.picker = true; s.sheet.selected = null; s.sheet.painted = false; paintSheet(); region("sheet")?.querySelector?.("[data-people-picker]")?.scrollIntoView?.({ block: "nearest" }); } break;
      case "sharing-select": case "sharing-edit": selectShare(d.user); break;
      case "sharing-cancel": if (s.sheet?.kind === "sharing") { s.sheet.selected = null; paintSheet(); } break;
      case "sharing-revoke": void sharingWrite("revoke"); break;
      case "sharing-enable": case "disable": case "enable": case "adopt": void sharingWrite(d.botsAction); break;
      case "sharing-retry": if (s.sheet?.kind === "sharing") void loadSharing(s.sheet); break;
      case "sharing-more": void moreSharingLog(); break;
      case "shared-thread": void openSharedThread(); break;
      case "project": void openSheet("project"); break;
      case "close": closeSheet(); break;
      case "retry": { const token = generation; s.error = ""; void loadRoster(token); if (s.botId) void loadThread(token, s.botId); break; }
      case "toggle": if (s.open.has(d.key)) s.open.delete(d.key); else s.open.add(d.key); paintMessages(); break;
      case "waiting": s.waitingOpen = !s.waitingOpen; paintWaiting(); break;
      case "earlier": void earlier(); break;
      case "decide": void decide(Number(d.work), d.version, { choice: d.choice }); break;
      case "confirm": void confirm(d.pending, d.accept === "1"); break;
      case "stop": void stop(); break;
      case "forget": void forget(d.id); break;
      case "learned-sheet": void openLearned(); break;
      case "learned": void learnedAct(d.do, d.id); break;
      case "learned-bot": void learnedBot(d.do); break;
      case "learning-share-confirm": void confirmLearningShare(true); break;
      case "learning-share-cancel": void confirmLearningShare(false); break;
      case "archive": void archive(); break;
      default: break;
    }
  }
  function submit(form) {
    const d = form?.dataset || {};
    switch (d.botsForm) {
      case "send": return sendText(form.elements.text.value);
      case "reply": { const reply = form.elements.reply.value.trim(); return reply ? decide(Number(d.work), d.version, { reply }) : undefined; }
      case "learned-edit": { const text = form.elements.text.value.trim(); return text ? learnedAct("save", d.id, { text }) : undefined; }
      case "create": return create(form);
      case "save": return save(form);
      case "share": return sharingWrite("grant");
      case "shared-thread": return createSharedThread(form);
      case "learning-share": return previewLearningShare(form);
      case "memory": return addMemory(form).then(() => { if (form.elements.text) form.elements.text.value = ""; });
      default: return undefined;
    }
  }
  function onClick(event) {
    const el = event.target?.closest?.("[data-bots-action]");
    if (!el) return;
    event.preventDefault?.();
    click(el);
  }
  function onSubmit(event) {
    const form = event.target?.closest?.("[data-bots-form]");
    if (!form) return;
    event.preventDefault?.();
    void submit(form);
  }
  function onInput(event) {
    const box = event.target;
    if (deck() && s.sheet?.kind === "sharing") {
      if (box?.name === "person-search") {
        s.sheet.search = box.value;
        let visible = 0;
        for (const row of region("sheet")?.querySelectorAll?.("[data-person-name]") || []) { row.hidden = !row.dataset.personName.includes(box.value.toLocaleLowerCase()); if (!row.hidden) visible++; }
        const empty = region("sheet")?.querySelector?.("[data-picker-empty]"); if (empty) empty.hidden = visible > 0;
        return;
      }
      if (["talk", "see_shared"].includes(box?.name) && s.sheet.selected && s.bot?.mine) {
        s.sheet.draft[box.name] = box.checked;
        const preview = region("sheet")?.querySelector?.(".bot-access-preview p");
        if (preview) preview.textContent = permissionText(s.sheet.draft, s.sheet.me.people[`user:${s.sheet.selected}`]);
        const save = region("sheet")?.querySelector?.('[data-bots-form="share"] [type="submit"]');
        if (save) save.disabled = !s.sheet.draft.talk && !s.sheet.draft.see_shared;
        return;
      }
    }
    if (box?.name !== "text" || !box.closest?.('[data-region="composer"]')) return;
    fit(box);
    if (box.value) s.drafts.set(draftKey(), box.value); else s.drafts.delete(draftKey());
  }
  function onKeydown(event) {
    if (event.key === "Escape" && s.sheet) { event.preventDefault?.(); closeSheet(); return; }
    const box = event.target;
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && box?.name === "text" && box.closest?.('[data-region="composer"]')) {
      event.preventDefault?.();
      void sendText(box.value);
    }
  }
  function onVisible() {
    if (!active || hidden()) return;
    const token = generation;
    if (s.botId) { void pollThread(token); scheduleThread(token); }
    if (!s.botId || !phone()) void loadRoster(token);
  }
  function onChange(event) { if (event.target?.name === "bot-thread") void switchThread(event.target.value); }
  // The on-screen keyboard shrinks the visual viewport; the screen follows it so the composer stays in view.
  function followViewport() {
    if (!viewport) return;
    const node = region("messages"), stick = atBottom(node);
    globalThis.document?.documentElement?.style?.setProperty("--bots-vh", `${Math.round(viewport.height)}px`);
    if (stick && node) node.scrollTop = node.scrollHeight;
  }

  function unbind() {
    clearTimer(timers.thread); clearTimer(timers.roster);
    timers.thread = timers.roster = null;
    if (visibilityBound) { globalThis.document?.removeEventListener?.("visibilitychange", onVisible); visibilityBound = false; }
    if (viewport) {
      viewport.removeEventListener?.("resize", followViewport);
      viewport.removeEventListener?.("scroll", followViewport);
      globalThis.document?.documentElement?.style?.removeProperty("--bots-vh");
    }
  }

  return {
    newBot() { if (active) return openSheet("new"); },
    show(id = null) {
      const wasActive = active, previous = s.botId;
      const el = host();
      if (!el) return undefined;
      // The shell asks again after unrelated route changes (a work item opened beside the thread): keep what is on screen.
      if (wasActive && id === previous && el.querySelector(".bots")?.isConnected === true) { takeDraft(); return undefined; }
      const token = ++generation;
      active = true;
      if (id !== previous) {
        // Another thread: a local stream for the old one stops; the server finishes the turn and stores it.
        s.turn?.controller.abort();
        s.turn = null; s.unsent = null; s.sheet = null; s.notice = null;
        s.botId = id; s.bot = id ? s.bots.find((b) => b.id === id) || null : null; s.threadId = null; s.threads = [];
        s.messages = []; s.waiting = []; s.running = false; s.more = false; s.ready = false; s.waitingOpen = false;
        ticks = 0;
      }
      el.innerHTML = deck() ? shellHTML.replace('<div data-region="model">', `${coworkersNavHTML()}<div data-region="model">`) : shellHTML;
      rootEl = el.querySelector(".bots");
      for (const [type, handler] of [["click", onClick], ["submit", onSubmit], ["input", onInput], ["change", onChange], ["keydown", onKeydown]]) rootEl?.addEventListener?.(type, handler);
      if (!visibilityBound) { globalThis.document?.addEventListener?.("visibilitychange", onVisible); visibilityBound = true; }
      if (!wasActive && viewport) {
        viewport.addEventListener?.("resize", followViewport);
        viewport.addEventListener?.("scroll", followViewport);
        followViewport();
      }
      paintRoster();
      paintThread();
      paintSheet();
      scheduleRoster(token);
      if (deck()) return loadRoster(token).then(() => id && alive(token) ? loadThread(token, id) : undefined);
      const loads = [loadRoster(token)];
      if (id) loads.push(loadThread(token, id));
      return Promise.all(loads).then(() => undefined);
    },
    stop() {
      active = false;
      generation++;
      s.turn?.controller.abort();
      s.turn = null;
      unbind();
    },
    // Test and shell hooks: the same entry points the DOM listeners use.
    click, submit, keydown: onKeydown, state: s,
  };
}
