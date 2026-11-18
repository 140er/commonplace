// History and handoff: open a saved thread and its chain, and ask for an authorized handoff.
// It reads the existing thread routes and asks /api/handoff/start for the one action it adds.
// Saved words are shown exactly as stored (escaped, never summarised); the server decides who may act.
import { api, actor, escape as e } from "./api.js";

const enc = encodeURIComponent;
const link = (id) => `/app/history.html?thread=${enc(id)}`;
const words = (value) => `<pre class="history-words">${e(value)}</pre>`;
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const STATES = { done: "Handed off", launched: "Handed off", stored: "Saved, not started", launch_failed: "Saved, new thread did not start", failed: "Not saved", pending: "Saving" };
export const THREAD_PATH = (id) => `/api/threads/${enc(id)}`;

export function when(value) {
  const at = new Date(value);
  return value && !Number.isNaN(at.getTime()) ? `<time datetime="${e(value)}">${e(at.toISOString().slice(0, 16).replace("T", " "))} UTC</time>` : "<span>Time not recorded</span>";
}

const section = (title, body, attrs = "") => `<section class="history-section"${attrs}><h2>${e(title)}</h2>${body}</section>`;
const notice = (kind, title, text, extra = "") => `<div class="history-notice" data-kind="${e(kind)}" role="${kind === "error" ? "alert" : "status"}"><strong>${e(title)}</strong> ${e(text)}${extra}</div>`;

// Only a 401 is signed out. A timeout or any other failure is a failed read, and says so.
export function failureHTML(error, what = "this page") {
  if (error?.status === 401) return notice("signed-out", "Sign in to see this.", "Your session has ended, or you are not signed in.", ' <a href="/app/">Sign in</a>, then open History again.');
  if (error?.status === 404) return notice("missing", "This thread is not available to you.", "It may not exist, or your access may not include it. Check the id, or open one from your saved threads.", ' <a href="/app/history.html">Saved threads</a>');
  const why = error?.status === 403 ? "Your access does not include this." : `${what[0].toUpperCase()}${what.slice(1)} could not be read just now. This does not mean you are signed out.`;
  return notice("error", "Could not load.", why, ' <button type="button" class="btn" data-history-retry>Try again</button>');
}

// ---- The list of saved threads -------------------------------------------------------------
export function listHTML(handoffs) {
  if (!Array.isArray(handoffs)) return section("Saved threads", notice("unknown", "This page did not understand the answer.", "It shows nothing rather than guess. Reload to try again."));
  const rows = handoffs.filter((row) => row && typeof row.stored_thread_id === "string" && row.stored_thread_id);
  const body = rows.length
    ? `<ul class="history-list">${rows.map((row) => `<li><a class="history-row" href="${e(link(row.stored_thread_id))}"><span class="history-id">${e(row.stored_thread_id)}</span><span class="history-meta">${e(STATES[row.state] || `State: ${row.state}`)}${row.agent ? ` · ${e(row.agent)}` : ""}${row.task_ref ? ` · card ${e(row.task_ref)}` : ""} · ${when(row.created_at)}</span></a></li>`).join("")}</ul>`
    : `<p class="history-empty">No thread has been saved yet. A thread is saved when an agent hands off. Handoff is off until a workspace owner turns it on in <a href="/app/settings/memory">Memory settings</a>.</p>`;
  return section("Saved threads", body + openForm());
}

function openForm() {
  return `<form class="history-open" data-history-open><label for="history-thread">Open a saved thread by id</label><div class="history-inline"><input id="history-thread" name="thread" autocomplete="off" spellcheck="false" placeholder="thr_…" required><button class="btn" type="submit">Open</button></div></form>`;
}

// ---- One thread and its chain ------------------------------------------------------------------
export function chainHTML(chain, thread) {
  if (!Array.isArray(chain) || !chain.length) return section("History this would use", notice("unknown", "The chain could not be read.", "This thread is shown on its own. Reload to try again."));
  const messages = chain.reduce((sum, row) => sum + (Number.isSafeInteger(row.message_count) ? row.message_count : 0), 0);
  const capped = chain.length >= 50;
  const rows = chain.map((row, i) => `<li><a href="${e(link(row.id))}"${row.id === thread.id ? ' aria-current="true"' : ""}>${e(row.id)}</a><span class="history-meta">${i === 0 ? "newest" : "earlier"} · ${e(plural(row.message_count ?? 0, "message"))} · saved ${when(row.created_at)}</span> <a class="history-read" href="/app/threads/${enc(row.id)}">Read the messages</a></li>`).join("");
  const edge = capped
    ? "The newest 50 saved threads are used; older ones are not part of this view."
    : "The chain ends here: no earlier saved thread exists, or none you may read.";
  return section("History this would use", `<p>A new agent that recalls this thread reads these ${e(plural(chain.length, "saved thread"))} (${e(plural(messages, "message"))}), newest first, in the exact words they were saved. Nothing is summarised.</p><ol class="history-chain">${rows}</ol><p class="history-meta">${e(edge)}</p>`, ' data-history-chain');
}

export function threadHTML(thread) {
  return `<header class="history-head"><h1>Saved thread</h1><p class="history-id">${e(thread.id)}</p><p class="history-meta">${e(plural(thread.message_count ?? 0, "message"))} · saved ${when(thread.created_at)}${thread.created_by ? ` by ${e(actor(thread.created_by))}` : ""}</p></header>`;
}

// ---- The handoff action -----------------------------------------------------------------------
export function offerHTML(offer, state = {}) {
  if (state.result) return resultHTML(state.result);
  if (state.preview) return confirmHTML(state.preview, state);
  if (offer?.error) return section("Hand off", failureHTML(offer.error, "the handoff options"), " data-history-offer");
  if (!offer || typeof offer !== "object" || !["ready", "unavailable"].includes(offer.state)) return section("Hand off", notice("unknown", "This page did not understand the answer.", "It offers no action rather than guess. Reload to try again."), " data-history-offer");
  if (offer.state === "unavailable") {
    const extra = [offer.later_thread ? ` <a href="${e(link(offer.later_thread))}">Open the later thread</a>` : "", offer.settings_link ? ` <a href="${e(offer.settings_link)}">Memory settings</a>` : "", offer.retry ? ` <button type="button" class="btn" data-history-relaunch="${e(offer.handoff_id)}">Retry the launch</button>` : ""].join("");
    return section("Hand off", notice("unavailable", `${offer.reason || "A handoff cannot be requested from here."} ${offer.next_step || ""}`.trim(), "", extra) + (state.error ? failureText(state.error) : ""), " data-history-offer");
  }
  const c = offer.continues || {};
  return section("Hand off", `<p>Continue this work in a new agent. The thread that carries it on is <code>${e(c.thread_id)}</code>${c.agent ? ` (agent ${e(c.agent)})` : ""}${c.task ? `, card ${e(c.task)}` : ""}.</p>${state.error ? failureText(state.error) : ""}${form(state)}`, " data-history-offer");
}

function form(state) {
  const f = state.form || {};
  return `<form class="history-handoff" data-history-handoff><label>Start the new agent on<input name="to" value="${e(f.to ?? "")}" placeholder="provider/model, e.g. codex/gpt-6.1-sol" autocomplete="off" spellcheck="false" required></label><label>Effort (optional)<input name="effort" value="${e(f.effort ?? "")}" autocomplete="off" spellcheck="false"></label><label>Your own words for the new agent (optional, kept exactly)<textarea name="note" rows="3">${e(f.note ?? "")}</textarea></label><button class="btn primary" type="submit"${state.busy ? " disabled" : ""}>${state.busy ? "Checking…" : "Review the handoff"}</button></form>`;
}

function failureText(error) {
  return `<div class="history-notice" data-kind="error" role="alert"><strong>${e(error.message || "The handoff did not start.")}</strong>${error.next ? ` <span>${e(error.next)}</span>` : ""}</div>`;
}

export function confirmHTML(preview, state = {}) {
  const d = preview.destination || {}, c = preview.carried || {}, live = preview.continues?.thread_id;
  const carried = [
    `The whole saved transcript of <code>${e(live)}</code>, word for word, kept in Memory as a new saved thread.`,
    c.earlier_threads ? `The ${e(plural(c.earlier_threads, "saved thread"))} already in this chain (${e(plural(c.earlier_messages ?? 0, "message"))}) stay linked; the new agent can recall them in their exact words.` : "No earlier saved thread exists, so there is no earlier history to link.",
    preview.note ? "Your note, exactly as typed:" : "No note.",
  ];
  return section("Confirm the handoff", `<dl class="history-facts"><dt>Destination</dt><dd>${e(d.provider)} / ${e(d.model)}${d.effort ? `, effort ${e(d.effort)}` : ""}</dd><dt>Continues thread</dt><dd><code>${e(live)}</code></dd><dt>Carried over</dt><dd><ul>${carried.map((line) => `<li>${line}</li>`).join("")}</ul>${preview.note ? words(preview.note) : ""}</dd><dt>Then</dt><dd>The old thread is stopped once the new one has started. If starting fails, the old one keeps running.</dd></dl>${state.error ? failureText(state.error) : ""}<div class="history-actions"><button type="button" class="btn primary" data-history-confirm${state.busy ? " disabled" : ""}>${state.busy ? "Starting…" : "Start the handoff"}</button><button type="button" class="btn" data-history-cancel>Change it</button></div>`, " data-history-offer");
}

export function resultHTML(result) {
  const next = result.stored_thread_id ? ` <a href="${e(link(result.stored_thread_id))}">Open the saved thread</a>` : "";
  const ref = result.successor?.agent_ref ? ` The new thread is ${result.successor.agent_ref}.` : "";
  if (result.source_settled === false) return section("Hand off", notice("warning", "The new thread started, but the old one could not be stopped.", `${ref.trim()} Run the action again: it only stops the old thread.`, next), " data-history-offer");
  return section("Hand off", notice("done", "Handoff started.", `The old thread is stopped.${ref}`, next), " data-history-offer");
}

export function relaunchResult(row) {
  return { state: row?.state, stored_thread_id: row?.stored_thread_id, successor: row?.successor, source_settled: true };
}

// Plain failure: what happened and a working next step. Network or timeout is never "signed out".
export function refusal(error) {
  if (error?.status === 401) return { message: "You are signed out.", next: "Sign in at /app/, then try again." };
  if (error?.status === 403) return { message: "Your access does not include starting a handoff.", next: "Ask a workspace owner to grant it, or hand off from the agent that wrote the thread." };
  const payload = error?.payload;
  if (payload?.error) return { message: payload.error, next: payload.next_step || "Try again in a moment." };
  return { message: "The request did not finish, so nothing was changed.", next: "Check your connection and try again. This does not mean you are signed out." };
}

// ---- The page ----------------------------------------------------------------------------------
export function mountHistory(host, { read = (path, options) => api(path, options), location = globalThis.location, timeoutMs = 15000, navigate, fields = (el) => Object.fromEntries(new FormData(el)) } = {}) {
  let request = 0, thread = null, offer = null, chain = null, ui = {};
  const id = () => new URLSearchParams(location?.search || "").get("thread") || "";
  const go = navigate || ((url) => { location.assign(url); });
  const paint = (html) => { host.innerHTML = html; host.setAttribute?.("aria-busy", "false"); };
  const draw = () => {
    paint(`${threadHTML(thread)}${chainHTML(chain, thread)}${offerHTML(offer, ui)}`);
  };

  async function load() {
    const mine = ++request, wanted = id();
    host.setAttribute?.("aria-busy", "true");
    host.innerHTML = `<p role="status" class="history-state">Loading…</p>`;
    ui = {};
    try {
      if (!wanted) { const rows = await read("/api/handoffs", { timeoutMs }); if (mine === request) paint(`<header class="history-head"><h1>History and handoff</h1><p class="history-meta">Open a saved thread to see its chain and continue it in a new agent.</p></header>${listHTML(rows)}`); return; }
      thread = await read(THREAD_PATH(wanted), { timeoutMs });
      if (mine !== request) return;
      [chain, offer] = await Promise.all([
        read(`${THREAD_PATH(wanted)}/chain`, { timeoutMs }).catch(() => null),
        read(`/api/handoff/start?thread=${enc(wanted)}`, { timeoutMs }).catch((error) => ({ state: "unknown", error })),
      ]);
      if (mine === request) draw();
    } catch (error) { if (mine === request) paint(failureHTML(error, "this thread")); }
  }

  async function send(path, body, options = {}) {
    return read(path, { method: "POST", body, timeoutMs: 60000, ...options });
  }

  host.addEventListener?.("submit", (event) => {
    event.preventDefault?.();
    const target = event.target;
    if (target?.closest?.("[data-history-open]")) {
      const value = String(fields(target.closest("[data-history-open]")).thread || "").trim();
      if (value) go(link(value));
      return;
    }
    if (!target?.closest?.("[data-history-handoff]")) return;
    ui = { ...ui, form: fields(target.closest("[data-history-handoff]")), error: null, busy: true }; draw();
    const mine = request, typed = ui.form;
    send("/api/handoff/start", { thread: thread.id, to: String(typed.to || "").trim(), effort: String(typed.effort || "").trim(), note: String(typed.note || "") }).then((preview) => {
      if (mine === request) { ui = { ...ui, busy: false, preview }; draw(); }
    }, (error) => { if (mine === request) { ui = { ...ui, busy: false, error: refusal(error) }; draw(); } });
  });

  host.addEventListener?.("click", (event) => {
    const hit = (selector) => event.target?.closest?.(selector);
    if (hit("[data-history-retry]")) { void load(); return; }
    if (hit("[data-history-cancel]")) { ui = { form: ui.form }; draw(); return; }
    const again = hit("[data-history-relaunch]");
    if (again) {
      const handoff = again.getAttribute?.("data-history-relaunch") || again.dataset?.historyRelaunch;
      const mine = request;
      send(`/api/handoffs/${enc(handoff)}/relaunch`, {}).then((row) => { if (mine === request) { ui = { result: relaunchResult(row) }; draw(); } }, (error) => { if (mine === request) { ui = { error: refusal(error) }; draw(); } });
      return;
    }
    if (hit("[data-history-confirm]")) {
      ui = { ...ui, busy: true, error: null }; draw();
      const mine = request, p = ui.preview;
      send("/api/handoff/start", { thread: thread.id, to: `${p.destination.provider}/${p.destination.model}`, effort: p.destination.effort || "", note: p.note || "", confirm: true }).then((result) => {
        if (mine === request) { ui = { result }; draw(); }
      }, (error) => { if (mine === request) { ui = { ...ui, busy: false, error: refusal(error) }; draw(); } });
    }
  });
  return load();
}
