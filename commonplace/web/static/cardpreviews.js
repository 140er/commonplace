import { actor, api as request, escape as e, elapsed } from "./api.js";

// "Previews" in the card panel: what a Coworker published for this card, from
// GET /api/previews?card=<n>. A preview stays after the work session ends; Open shows it in a new
// tab, for signed-in people who can open the card. Stop keeps the files and Open again serves
// them again. The owner also sees the stop rule here and can change it, and can delete a
// preview's files after confirming; the server checks both again. A card the person cannot see
// answers 404, and then the section stays hidden.
//
// Focus and announcements: the list is drawn into its own body element, and results are said in
// one status line that stays in place, so a screen reader hears them. After an action, focus
// moves to the control that replaced the one used (or to the heading); a redraw keeps focus on
// the same control. The owner's stop-rule form lives in its own element outside the redrawn list
// (like the status line): it is built once and only its sentence and untouched fields follow the
// server, so a list redraw never collapses it, never clears what was typed and never takes focus.

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
  assignment: "a credential in a setting", json_secret: "a credential in a setting", jwt: "a signed token", bearer: "a bearer token",
};
// Controls that keep their identity across a redraw, so focus can return to the same one.
export const FOCUS_KEYS = ["data-preview-open", "data-preview-stop", "data-preview-reopen", "data-preview-retry", "data-preview-delete", "data-preview-delete-confirm", "data-preview-delete-cancel", "data-preview-field", "data-preview-rule-summary", "data-preview-rule-save", "data-preview-heading"];

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
// The publisher as the card names it; a connection with no display name reads as words ("Release coworker"), not as an id.
const plainName = (name) => { const words = String(name).replace(/[-_]+/g, " ").trim(); return words ? `${words[0].toUpperCase()}${words.slice(1)}` : "a Coworker"; };
const byWords = (preview) => {
  if (preview.by === "person") return "a person";
  const ref = `conn:${preview.published_by || ""}`, named = actor(ref);
  return named === String(preview.published_by || "") ? plainName(named) : named;
};

const deleteQuestion = (preview) => preview.files_remaining
  ? `Remove the files of ${preview.name} that were left behind last time?`
  : `Delete the files of ${preview.name} (${mib(preview.size_bytes)})? People can no longer open it, and this cannot be undone.`;

function previewHTML(preview, { canDelete = false, confirming = "" } = {}) {
  const state = STATE_WORDS[preview.state] ? preview.state : "failed";
  const url = URL_SHAPE.test(preview.url || "") ? preview.url : null;
  const revision = preview.revision ? ` · version ${e(preview.revision)} · ${e(mib(preview.size_bytes))}` : "";
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
  // Files to delete: a served or stopped preview, or a deleted one whose removal left files behind.
  const retry = preview.reason === "files_deleted" && preview.files_remaining === true;
  const deletable = (url && state !== "building") || retry;
  const id = e(preview.id);
  const acts = [
    state === "ready" && url ? `<a class="btn sm primary" href="${e(url)}" target="_blank" rel="noopener noreferrer" data-preview-open="${e(preview.id)}" aria-label="Open in new tab: preview ${e(name)}">Open in new tab</a>` : "",
    state === "ready" ? `<button class="btn sm" type="button" data-preview-stop="${e(preview.id)}" aria-label="Stop preview ${e(name)}">Stop</button>` : "",
    state === "stopped" && url ? `<button class="btn sm" type="button" data-preview-reopen="${e(preview.id)}" aria-label="Open again: serve preview ${e(name)}">Open again</button>` : "",
    canDelete && deletable && confirming !== preview.id ? `<button class="btn sm danger" type="button" data-preview-delete="${id}" aria-label="${retry ? "Finish deleting files for" : "Delete files for"} preview ${e(name)}">${retry ? "Finish deleting" : "Delete files"}</button>` : "",
  ].join("");
  const confirm = canDelete && deletable && confirming === preview.id
    ? `<div class="preview-confirm" role="group" aria-label="Delete the files of preview ${e(name)}"><p class="run-reason" id="preview-delete-question-${id}">${e(deleteQuestion(preview))}</p><div class="preview-acts"><button class="btn sm danger" type="button" data-preview-delete-confirm="${id}" aria-describedby="preview-delete-question-${id}">${retry ? "Finish deleting" : "Delete files"}</button><button class="btn sm" type="button" data-preview-delete-cancel="${id}">Cancel</button></div></div>`
    : "";
  return `<li data-preview="${e(preview.id)}" data-state="${e(state)}"><p class="run-line"><span class="tag run-state">${e(STATE_WORDS[state])}</span><b class="preview-name">${e(name)}</b>${revision}${time}</p><p class="run-reason" data-preview-reason>${e(preview.reason_text || "")}</p>${failure}${preview.next ? `<p class="run-reason" data-preview-next>${e(preview.next)}</p>` : ""}${stops}${acts ? `<div class="preview-acts">${acts}</div>` : ""}${confirm}</li>`;
}

function usageHTML(found) {
  const usage = found?.usage, limits = found?.limits;
  if (!usage || !limits) return "";
  return `<p class="run-reason" data-preview-usage>${e(usage.ready)} of ${e(limits.max_ready)} previews served in this workspace · ${e(mib(usage.bytes))} of ${e(mib(limits.max_workspace_bytes))} used (stopped previews still count).</p>`;
}

// What a person needs to know about the address and the kind of page, once, under the list.
const NOTE = "Open gives this address a key for 30 minutes: anyone with the exact address can open the preview until then, so share the preview, not the address. If parts stop loading, reload it from the card. This is a saved page with no server code, inside a viewer. It cannot load other websites, cannot move the viewer or open popups. Open only previews from people you trust.";
const noteHTML = (found) => Array.isArray(found?.previews) && found.previews.some((preview) => preview.state === "ready" && URL_SHAPE.test(preview.url || "")) ? `<p class="run-reason" data-preview-note>${e(NOTE)} Stopping never deletes the files.</p>` : "";

const RULE_HINT = "preview-rule-hint";
const ruleField = (name, words, max, value) => `<label class="run-start-field">${words}<input type="number" inputmode="numeric" name="${name}" data-preview-field="${name}" min="1" max="${max}" step="1" value="${value == null ? "" : e(value)}" placeholder="Never" aria-describedby="${RULE_HINT}"><span id="preview-rule-error-${name}" data-preview-field-error="${name}" hidden></span></label>`;

// The owner's rule: its sentence for everyone, and for the owner the form. It is drawn into its
// own element (`paintRule` below), never into the redrawn list.
export function ruleHTML(found) {
  const rule = found?.rule;
  if (!rule) return "";
  const text = `<p class="run-reason" data-preview-rule-text>${e(rule.text || "")}</p>`;
  if (!found.can_set_rule) return text;
  return `${text}<details class="preview-rule" data-preview-rule><summary data-preview-rule-summary="rule">Change when previews stop</summary><form class="run-start" data-preview-rule-form novalidate><p id="${RULE_HINT}">Leave a field empty to keep previews served until someone stops them. Stopping never deletes the files.</p><div class="run-start-row">${ruleField("stop_days_after_pr", "Days after the card records the pull request as merged or closed", 365, rule.stop_days_after_pr)}${ruleField("max_age_days", "Days after it was last made live", 3650, rule.max_age_days)}</div><button class="btn sm" type="submit" data-preview-rule-save="rule">Save rule</button></form></details>`;
}

// The list part of the section for a loaded answer (the status line and the rule are separate);
// `confirming` is the id of the preview whose delete the owner is being asked to confirm.
export function cardPreviewsHTML(found, confirming = "") {
  const previews = Array.isArray(found?.previews) ? found.previews : [];
  const options = { canDelete: Boolean(found?.can_delete), confirming };
  const list = previews.length
    ? `<ul class="run-list">${previews.map((preview) => previewHTML(preview, options)).join("")}</ul>`
    : `<p class="run-empty" data-preview-empty>No preview yet. When a Coworker publishes one for this card, it appears here and stays after it finishes working.</p>`;
  return `${list}${noteHTML(found)}${usageHTML(found)}`;
}

// What the section says when there is no list to show; the retry button is real.
export const offlineWords = (error) => networkError(error) ? "Could not load the previews. Check the connection and try again." : "Could not load the previews. Try again.";
export const offlineHTML = () => `<button class="btn sm" type="button" data-preview-offline data-preview-retry="load">Try again</button>`;

const HEAD = `<div class="sec-h"><h3 tabindex="-1" data-preview-heading="previews">Previews</h3></div>`;
const keyOf = (card) => `${card.board}/${card.number}`;

// The one insertion renderDetails makes: an empty, hidden section that attachCardPreviews fills.
// There is no module-level cache: a list is only ever repainted from the panel that showed it a
// moment ago (see `previous`), so nothing can carry over to another session or workspace.
export const cardPreviewsSlot = (card) => `<section class="card-previews" data-card-previews data-card-key="${e(keyOf(card))}" aria-label="Previews" hidden>${HEAD}<div data-preview-body></div><div data-preview-rule-slot></div><p class="run-note" role="status" aria-live="polite" data-preview-status></p></section>`;

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
// Keep the actual section when possible: a request in flight still owns its status line.
export function previousPreviews(mount) {
  const slot = mount?.querySelector?.("[data-card-previews]");
  const body = slot?.querySelector?.("[data-preview-body]");
  if (!slot || slot.hidden || !body?.innerHTML) return null;
  // The rule form is kept as it stands: open or closed, with what was typed.
  const holder = slot.querySelector?.("[data-preview-rule-slot]");
  const fields = [...(holder?.querySelectorAll?.("[data-preview-field]") || [])];
  const rule = holder?.innerHTML
    ? { html: holder.innerHTML, open: Boolean(holder.querySelector?.("[data-preview-rule]")?.open), values: Object.fromEntries(fields.map((input) => [input.getAttribute("data-preview-field"), input.value])) }
    : null;
  return { key: slot.dataset?.cardKey, html: body.innerHTML, rule, focus: focusedKey(slot), element: slot, status: slot.querySelector?.("[data-preview-status]")?.textContent || "" };
}

// Load the card's previews into the slot inside `mount` and wire Stop, Open again, Delete files and the rule.
export async function attachCardPreviews(mount, card, { api = request, previous = null, schedule = (fn, ms) => setTimeout(fn, ms) } = {}) {
  const slot = mount.querySelector?.("[data-card-previews]");
  if (!slot || card?.number == null) return;
  if (previous?.key === keyOf(card) && previous.element?.previewController && slot.replaceWith) {
    slot.replaceWith(previous.element);
    previous.element.previewController.focus(previous.focus);
    await previous.element.previewController.refresh();
    return;
  }
  const body = slot.querySelector?.("[data-preview-body]");
  const holder = slot.querySelector?.("[data-preview-rule-slot]");
  const status = slot.querySelector?.("[data-preview-status]");
  if (!body || !status || !holder) return;
  const key = keyOf(card);
  let revision = 0, last = null, wanted = null, polling = false, confirming = "", acting = 0;
  const live = (token) => token === revision && slot.isConnected;
  const say = (note) => { status.textContent = note || ""; };
  // True only when the control really has focus: a control inside a closed <details> accepts the
  // call and keeps nothing, so the heading fallback must still run for it.
  const focus = (selector) => {
    const target = selector && slot.querySelector?.(selector);
    if (!target?.focus) return false;
    target.focus({ preventScroll: true });
    return globalThis.document?.activeElement === target;
  };
  // The rule is built once per kind of viewer. Afterwards only its sentence follows the server,
  // and a field follows it only while it still holds what the server last sent.
  const paintRule = (found) => {
    const wantsForm = Boolean(found?.rule && found.can_set_rule);
    const hasForm = Boolean(holder.querySelector?.("[data-preview-rule-form]"));
    if (!found?.rule || wantsForm !== hasForm || !holder.innerHTML) { holder.innerHTML = ruleHTML(found); return; }
    const text = holder.querySelector("[data-preview-rule-text]");
    if (text) text.textContent = found.rule.text || "";
    for (const name of ["stop_days_after_pr", "max_age_days"]) {
      const input = holder.querySelector(`[data-preview-field="${name}"]`);
      if (!input) continue;
      const fresh = found.rule[name] == null ? "" : String(found.rule[name]);
      if (input.value === input.defaultValue) input.value = fresh;
      input.defaultValue = fresh;
    }
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
    paintRule(found);
    paint(cardPreviewsHTML(found, confirming));
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
    if (previous.rule?.html) {
      holder.innerHTML = previous.rule.html;
      const details = holder.querySelector?.("[data-preview-rule]");
      if (details && previous.rule.open) details.open = true;
      for (const [name, value] of Object.entries(previous.rule.values || {})) {
        const input = holder.querySelector?.(`[data-preview-field="${name}"]`);
        if (input) input.value = value;
      }
    }
    slot.hidden = false;
    say(previous.status);
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
      if ([403, 404].includes(error?.status)) { last = null; slot.hidden = true; body.innerHTML = ""; holder.innerHTML = ""; say(""); return; }
      if (last) { show(last, note ?? "Could not refresh the previews. Showing the last list."); return; }
      if (hasList(body.innerHTML)) { say(note ?? "Could not refresh the previews. Showing the last list."); return; }
      paint(offlineHTML(error));
      say(note ?? offlineWords(error));
    }
  }
  async function act(trigger, path, method, payload, done, next, { progress = "", failure = '[data-preview-heading="previews"]' } = {}) {
    acting += 1;
    const items = [...(trigger.closest?.("li, form")?.querySelectorAll?.("button, input") || [])];
    items.forEach((item) => { item.disabled = true; });
    // Said before the request, so a screen reader hears that something is happening.
    if (progress) say(progress);
    let note;
    try {
      const result = await api(path, { method, body: payload });
      note = typeof done === "function" ? done(result) : done;
      wanted = next;
    } catch (error) {
      note = refusalWords(error);
      wanted = failure;
    }
    // The rule form is not redrawn with the list, so it is enabled again before focus returns to it.
    if (trigger.closest?.("[data-preview-rule-form]")) items.forEach((item) => { item.disabled = false; });
    // A refusal usually means the preview moved on: reload so the list shows what is true now.
    await load(note);
    acting -= 1;
  }
  slot.previewController = { focus, refresh: () => acting ? Promise.resolve() : load() };
  slot.addEventListener("click", (event) => {
    const stop = event.target.closest?.("[data-preview-stop]");
    if (stop) {
      const id = stop.dataset.previewStop;
      void act(stop, `/api/previews/${encodeURIComponent(id)}/stop`, "POST", {},
        (result) => result?.state === "stopped" ? "Stopped. The files are kept; use Open again to serve it." : `Not stopped: ${result?.reason_text || "it was not being served."}`,
        `[data-preview-reopen="${id}"]`, { progress: "Stopping…" });
      return;
    }
    const reopen = event.target.closest?.("[data-preview-reopen]");
    if (reopen) {
      const id = reopen.dataset.previewReopen;
      void act(reopen, `/api/previews/${encodeURIComponent(id)}/open-again`, "POST", {},
        (result) => result?.state === "ready" ? "Served again. Press Open to view it." : `Not served: ${result?.reason_text || "it is not stopped."}`,
        `[data-preview-open="${id}"]`, { progress: "Serving it again…" });
      return;
    }
    const remove = event.target.closest?.("[data-preview-delete]");
    if (remove) {
      confirming = remove.dataset.previewDelete;
      wanted = `[data-preview-delete-cancel="${confirming}"]`;
      if (last) show(last, deleteQuestion(last.previews.find((preview) => preview.id === confirming)));
      return;
    }
    const cancel = event.target.closest?.("[data-preview-delete-cancel]");
    if (cancel) {
      const id = cancel.dataset.previewDeleteCancel;
      confirming = "";
      wanted = `[data-preview-delete="${id}"]`;
      if (last) show(last, "Deletion cancelled.");
      return;
    }
    const sure = event.target.closest?.("[data-preview-delete-confirm]");
    if (sure) {
      const id = sure.dataset.previewDeleteConfirm;
      confirming = "";
      void act(sure, `/api/previews/${encodeURIComponent(id)}`, "DELETE", { confirm: id },
        (result) => result?.files_remaining
          ? "Some files could not be removed. Use Finish deleting to try again."
          : "Deleted the files. People can no longer open this preview.",
        `[data-preview-delete="${id}"]`, { progress: "Deleting the files…" });
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
    let firstBad = null;
    for (const [name, max] of [["stop_days_after_pr", 365], ["max_age_days", 3650]]) {
      const input = form.elements[name], value = payload[name];
      const bad = input.validity?.badInput || (value !== null && !(Number.isInteger(value) && value >= 1 && value <= max));
      input.setAttribute?.("aria-invalid", String(Boolean(bad)));
      input.setAttribute?.("aria-describedby", bad ? `${RULE_HINT} preview-rule-error-${name}` : RULE_HINT);
      const error = form.querySelector?.(`[data-preview-field-error="${name}"]`);
      if (error) { error.hidden = !bad; error.textContent = bad ? `Use whole days from 1 to ${max}, or leave this field empty.` : ""; }
      if (bad && !firstBad) firstBad = input;
    }
    if (firstBad) { firstBad.focus?.(); say("Check the day fields. Use whole days in the range shown, or leave a field empty."); return; }
    // A refused save keeps the form open with what was typed, and puts focus back on Save.
    void act(form, "/api/previews/rules", "PUT", payload, "Saved the stop rule.", '[data-preview-rule-summary="rule"]', { progress: "Saving the rule…", failure: '[data-preview-rule-save="rule"]' });
  });
  await load();
}
