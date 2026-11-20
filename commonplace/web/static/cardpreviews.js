import { actor, api as request, escape as e, elapsed } from "./api.js";

// "Previews" in the card panel: what a Coworker published for this card, from
// GET /api/previews?card=<n>. A preview stays after the work session ends; Open shows it in a new
// tab, for signed-in people who can open the card. Stop keeps the files and Open again serves
// them again. The owner also sees the stop rule here and can change it; the server checks that
// again. A card the person cannot see answers 404, and then the section stays hidden.
//
// Focus and announcements: the list is drawn into its own body element, and results are said in
// one status line that stays in place, so a screen reader hears them. After an action, focus
// moves to the control that replaced the one used (or to the heading); a redraw keeps focus on
// the same control.

export const STATE_WORDS = {
  ready: "Ready",
  building: "Being published",
  stopped: "Stopped",
  failed: "Could not be published",
};

const URL_SHAPE = /^\/p\/pv_[0-9a-f]{16}\/$/;
const MIB = 1024 * 1024;
const POLL_MS = 3000;
const KIND_WORDS = {
  private_key: "a private key", github_token: "a GitHub token", gitlab_token: "a GitLab token",
  aws_key: "an AWS key", anthropic_key: "an Anthropic key", openai_key: "an OpenAI key",
  slack_token: "a Slack token", stripe_key: "a Stripe key", google_api_key: "a Google API key",
  npm_token: "an npm token", url_credentials: "a password in an address",
  cmmn_token: "a Commonplace token", cmmn_oauth: "a Commonplace token", session_cookie: "a session cookie",
};
// Controls that keep their identity across a redraw, so focus can return to the same one.
export const FOCUS_KEYS = ["data-preview-open", "data-preview-stop", "data-preview-reopen", "data-preview-retry", "data-preview-field", "data-preview-rule-summary", "data-preview-heading"];

const sentence = (value) => value ? `${value[0].toUpperCase()}${value.slice(1)}${/[.!?]$/.test(value) ? "" : "."}` : "";
const networkError = (error) => !error?.status;

export const refusalWords = (error) => {
  const payload = error?.payload || {};
  const text = String(payload.error || "").replace(/^no: /, "");
  if (text) return [sentence(text), sentence(String(payload.next || ""))].filter(Boolean).join(" ");
  if (error?.status === 404) return "This preview no longer exists, or you cannot open its card.";
  if (error?.status === 403) return "You cannot do that to this preview.";
  return networkError(error) ? "That did not reach the server. Check the connection and try again." : "That did not work on the server. Try again.";
};

const ago = (at) => {
  const value = elapsed(at);
  return value === "now" ? "just now" : value === "—" ? "" : `${value} ago`;
};
const when = (at) => at ? `<time datetime="${e(at)}" title="${e(at)}">${e(ago(at) || at)}</time>` : "";
const day = (at) => {
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? at : date.toLocaleDateString([], { year: "numeric", month: "short", day: "numeric" });
};
const mib = (bytes) => {
  const value = Number(bytes) || 0;
  // Small amounts in KiB, so a few files never read as "0 MiB".
  return value < MIB ? `${Math.max(value ? 1 : 0, Math.round(value / 1024))} KiB` : `${Math.round(value / MIB * 10) / 10} MiB`;
};
const byWords = (preview) => preview.by === "person" ? "a person" : actor(`conn:${preview.published_by || ""}`);

function previewHTML(preview) {
  const state = STATE_WORDS[preview.state] ? preview.state : "failed";
  const url = URL_SHAPE.test(preview.url || "") ? preview.url : null;
  const revision = preview.revision ? ` · revision ${e(preview.revision)}` : "";
  // Only a published revision has a publish time; an attempt says when it started.
  const time = preview.published_at
    ? ` · published ${when(preview.published_at)} by ${e(byWords(preview))}`
    : ` · started ${when(preview.started_at || preview.last_changed_at)} by ${e(byWords(preview))}`;
  const stops = state === "ready" && preview.stops_at
    ? `<p class="run-reason" data-preview-stops>Stops being served on <time datetime="${e(preview.stops_at)}">${e(day(preview.stops_at))}</time> under the workspace rule. The files are kept.</p>`
    : "";
  const failure = state === "failed" && preview.failure?.file
    ? `<p class="run-reason" data-preview-failure>File: <code>${e(preview.failure.file)}</code>${preview.failure.kind ? `, which looked like ${e(KIND_WORDS[preview.failure.kind] || "a credential")}` : ""}.</p>`
    : "";
  const name = preview.name;
  const acts = [
    state === "ready" && url ? `<a class="btn sm primary" href="${e(url)}" target="_blank" rel="noopener noreferrer" data-preview-open="${e(preview.id)}" aria-label="Open preview ${e(name)} in a new tab">Open</a>` : "",
    state === "ready" ? `<button class="btn sm" type="button" data-preview-stop="${e(preview.id)}" aria-label="Stop preview ${e(name)}">Stop</button>` : "",
    state === "stopped" && url ? `<button class="btn sm" type="button" data-preview-reopen="${e(preview.id)}" aria-label="Open again: serve preview ${e(name)}">Open again</button>` : "",
  ].join("");
  return `<li data-preview="${e(preview.id)}" data-state="${e(state)}"><p class="run-line"><span class="tag run-state">${e(STATE_WORDS[state])}</span><b class="preview-name">${e(name)}</b>${revision}${time}</p><p class="run-reason" data-preview-reason>${e(preview.reason_text || "")}</p>${failure}${preview.next ? `<p class="run-reason" data-preview-next>${e(preview.next)}</p>` : ""}${stops}${acts ? `<div class="preview-acts">${acts}</div>` : ""}</li>`;
}

function usageHTML(found) {
  const usage = found?.usage, limits = found?.limits;
  if (!usage || !limits) return "";
  return `<p class="run-reason" data-preview-usage>${e(usage.ready)} of ${e(limits.max_ready)} previews served in this workspace · ${e(mib(usage.bytes))} of ${e(mib(limits.max_workspace_bytes))} used (stopped previews still count).</p>`;
}

function ruleHTML(found) {
  const rule = found?.rule;
  if (!rule) return "";
  const text = `<p class="run-reason" data-preview-rule-text>${e(rule.text || "")}</p>`;
  if (!found.can_set_rule) return text;
  const field = (name, words, max, value) => `<label class="run-start-field">${words}<input type="number" inputmode="numeric" name="${name}" data-preview-field="${name}" min="1" max="${max}" step="1" value="${value == null ? "" : e(value)}" placeholder="Never"></label>`;
  return `${text}<details class="preview-rule" data-preview-rule><summary data-preview-rule-summary="rule">Change the stop rule</summary><form class="run-start" data-preview-rule-form><p>Leave a field empty to keep previews served until someone stops them. Stopping never deletes the files.</p><div class="run-start-row">${field("stop_days_after_pr", "Days after the card records the pull request as merged or closed", 365, rule.stop_days_after_pr)}${field("max_age_days", "Days after it was last made live", 3650, rule.max_age_days)}</div><button class="btn sm" type="submit">Save rule</button></form></details>`;
}

// The list part of the section for a loaded answer (the status line is separate).
export function cardPreviewsHTML(found) {
  const previews = Array.isArray(found?.previews) ? found.previews : [];
  const list = previews.length
    ? `<ul class="run-list">${previews.map(previewHTML).join("")}</ul>`
    : `<p class="run-empty" data-preview-empty>No preview yet. When a Coworker publishes one for this card, it appears here and stays after the work session ends.</p>`;
  return `${list}${usageHTML(found)}${ruleHTML(found)}`;
}

// What the section says when there is no list to show; the retry button is real.
export const offlineHTML = (error) => `<p class="run-empty" data-preview-offline>${networkError(error) ? "Could not load the previews. Check the connection and try again." : "Could not load the previews. Try again."}</p><button class="btn sm" type="button" data-preview-retry="load">Try again</button>`;

const HEAD = `<div class="sec-h"><h3 tabindex="-1" data-preview-heading="previews">Previews</h3></div>`;
const keyOf = (card) => `${card.board}/${card.number}`;

// The one insertion renderDetails makes: an empty, hidden section that attachCardPreviews fills.
// There is no module-level cache: a list is only ever repainted from the panel that showed it a
// moment ago (see `previous`), so nothing can carry over to another session or workspace.
export const cardPreviewsSlot = (card) => `<section class="card-previews" data-card-previews data-card-key="${e(keyOf(card))}" aria-label="Previews" hidden>${HEAD}<div data-preview-body></div><p class="run-note" role="status" aria-live="polite" data-preview-status></p></section>`;

const hasList = (html) => /data-preview="|data-preview-empty/.test(html || "");

// The selector of the focused control inside `scope`, by its stable data-preview-* key.
export function focusedKey(scope, doc = globalThis.document) {
  const active = doc?.activeElement;
  if (!active || !scope?.contains?.(active)) return null;
  for (const name of FOCUS_KEYS) {
    if (active.hasAttribute?.(name)) return `[${name}="${String(active.getAttribute(name)).replace(/["\\]/g, "\\$&")}"]`;
  }
  return null;
}

// What the panel showed before a redraw, so the same card's list does not blink away and back.
// Only the list is kept: an old result in the status line is not repeated.
export function previousPreviews(mount) {
  const slot = mount?.querySelector?.("[data-card-previews]");
  const body = slot?.querySelector?.("[data-preview-body]");
  return slot && !slot.hidden && body?.innerHTML ? { key: slot.dataset?.cardKey, html: body.innerHTML, focus: focusedKey(slot) } : null;
}

// Load the card's previews into the slot inside `mount` and wire Stop, Open again and the rule.
export async function attachCardPreviews(mount, card, { api = request, previous = null, schedule = (fn, ms) => setTimeout(fn, ms) } = {}) {
  const slot = mount.querySelector?.("[data-card-previews]");
  if (!slot || card?.number == null) return;
  const body = slot.querySelector?.("[data-preview-body]");
  const status = slot.querySelector?.("[data-preview-status]");
  if (!body || !status) return;
  const key = keyOf(card);
  let revision = 0, last = null, wanted = null, polling = false;
  const live = (token) => token === revision && slot.isConnected;
  const say = (note) => { status.textContent = note || ""; };
  const focus = (selector) => {
    const target = selector && slot.querySelector?.(selector);
    if (target?.focus) { target.focus({ preventScroll: true }); return true; }
    return false;
  };
  const paint = (html) => {
    // Keep focus on the same control through the redraw, or move it where an action asked.
    const had = focusedKey(slot);
    body.innerHTML = html;
    slot.hidden = false;
    const target = wanted || had;
    wanted = null;
    if (target && !focus(target)) focus('[data-preview-heading="previews"]');
  };
  const show = (found, note) => {
    last = found;
    paint(cardPreviewsHTML(found));
    if (note !== undefined) say(note);
    // While something is being published the card follows it by itself.
    if (!polling && (found?.previews || []).some((preview) => preview.state === "building")) {
      polling = true;
      schedule(() => { polling = false; if (slot.isConnected) void load(); }, POLL_MS);
    }
  };
  // A redraw of the same card shows what the panel showed a moment ago until the load answers.
  const painted = previous?.key === key && hasList(previous.html);
  if (painted) {
    body.innerHTML = previous.html;
    slot.hidden = false;
    focus(previous.focus);
  }
  async function load(note) {
    const token = ++revision;
    try {
      const found = await api(`/api/previews?card=${encodeURIComponent(card.number)}`);
      if (live(token)) show(found && typeof found === "object" ? found : {}, note);
    } catch (error) {
      if (!live(token)) return;
      // A card this person cannot see (or no longer exists): show nothing, as for a missing card.
      if ([403, 404].includes(error?.status)) { last = null; slot.hidden = true; body.innerHTML = ""; say(""); return; }
      if (last) { show(last, note ?? "Could not refresh the previews. Showing the last list."); return; }
      if (hasList(body.innerHTML)) { say(note ?? "Could not refresh the previews. Showing the last list."); return; }
      paint(offlineHTML(error));
      say(note ?? "");
    }
  }
  async function act(control, path, method, payload, done, next) {
    control.closest?.("li, form")?.querySelectorAll?.("button, input").forEach((item) => { item.disabled = true; });
    let note;
    try {
      const result = await api(path, { method, body: payload });
      note = typeof done === "function" ? done(result) : done;
      wanted = next;
    } catch (error) {
      note = refusalWords(error);
      wanted = '[data-preview-heading="previews"]';
    }
    // A refusal usually means the preview moved on: reload so the list shows what is true now.
    await load(note);
  }
  slot.addEventListener("click", (event) => {
    const stop = event.target.closest?.("[data-preview-stop]");
    if (stop) {
      const id = stop.dataset.previewStop;
      void act(stop, `/api/previews/${encodeURIComponent(id)}/stop`, "POST", {},
        (result) => result?.state === "stopped" ? "Stopped. The files are kept; use Open again to serve it." : `Not stopped: ${result?.reason_text || "it was not being served."}`,
        `[data-preview-reopen="${id}"]`);
      return;
    }
    const reopen = event.target.closest?.("[data-preview-reopen]");
    if (reopen) {
      const id = reopen.dataset.previewReopen;
      void act(reopen, `/api/previews/${encodeURIComponent(id)}/open-again`, "POST", {},
        (result) => result?.state === "ready" ? "Served again. Press Open to view it." : `Not served: ${result?.reason_text || "it is not stopped."}`,
        `[data-preview-open="${id}"]`);
      return;
    }
    if (event.target.closest?.("[data-preview-retry]")) { wanted = '[data-preview-heading="previews"]'; void load(); }
  });
  slot.addEventListener("submit", (event) => {
    const form = event.target.closest?.("[data-preview-rule-form]");
    if (!form) return;
    event.preventDefault();
    const days = (value) => { const text = String(value ?? "").trim(); return text === "" ? null : Number(text); };
    const payload = { stop_days_after_pr: days(form.elements.stop_days_after_pr.value), max_age_days: days(form.elements.max_age_days.value) };
    const bad = Object.values(payload).some((value) => value !== null && !(Number.isInteger(value) && value >= 1));
    if (bad) { say("Use whole days of 1 or more, or leave the field empty."); return; }
    void act(form, "/api/previews/rules", "PUT", payload, "Saved the stop rule.", '[data-preview-rule-summary="rule"]');
  });
  await load();
}
