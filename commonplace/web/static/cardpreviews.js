import { api as request, escape as e, elapsed } from "./api.js";

// "Previews" in the card panel: what a Coworker published for this card, from
// GET /api/previews?card=<n>. A preview stays after the work session ends; Open shows it in a
// new tab (/p/<id>/, only for signed-in people who can open the card). Stop keeps the files and
// Open again serves them again. The owner also sees the stop rule here and can change it, and
// can delete a preview's files after confirming; the server checks both again. A card the
// person cannot see answers 404, and then the section stays hidden.

export const STATE_WORDS = {
  ready: "Ready",
  building: "Being published",
  stopped: "Stopped",
  failed: "Could not be published",
};

const URL_SHAPE = /^\/p\/pv_[0-9a-f]{16}\/$/;
const MIB = 1024 * 1024;

export const refusalWords = (error) => {
  const payload = error?.payload || {};
  const text = String(payload.error || "").replace(/^no: /, "");
  const sentence = (value) => value ? `${value[0].toUpperCase()}${value.slice(1)}${/[.!?]$/.test(value) ? "" : "."}` : "";
  if (text) return [sentence(text), sentence(String(payload.next || ""))].filter(Boolean).join(" ");
  if (error?.status === 404) return "This preview no longer exists, or you cannot open its card.";
  if (error?.status === 403) return "You cannot do that to this preview.";
  return "That did not work. Check the connection and try again.";
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

function previewHTML(preview, { canDelete = false, confirming = "" } = {}) {
  const state = STATE_WORDS[preview.state] ? preview.state : "failed";
  const url = URL_SHAPE.test(preview.url || "") ? preview.url : null;
  const by = preview.by === "person" ? "a person" : `Coworker ${preview.published_by || ""}`.trim();
  const revision = preview.revision ? ` · revision ${e(preview.revision)}` : "";
  const published = preview.published_at ? ` · published ${when(preview.published_at)} by ${e(by)}` : "";
  const stops = state === "ready" && preview.stops_at
    ? `<p class="run-reason" data-preview-stops>Stops being served on <time datetime="${e(preview.stops_at)}">${e(day(preview.stops_at))}</time> under the workspace rule. The files are kept.</p>`
    : "";
  const label = `${preview.name}`;
  // Files to delete: a served or stopped preview, or a deleted one whose removal left files behind.
  const retry = preview.reason === "files_deleted" && preview.files_remaining === true;
  const deletable = (url && state !== "building") || retry;
  const acts = [
    state === "ready" && url ? `<a class="btn sm primary" href="${e(url)}" target="_blank" rel="noopener noreferrer" data-preview-open aria-label="Open preview ${e(label)} in a new tab">Open</a>` : "",
    state === "ready" ? `<button class="btn sm" type="button" data-preview-stop="${e(preview.id)}" aria-label="Stop preview ${e(label)}">Stop</button>` : "",
    state === "stopped" && url ? `<button class="btn sm" type="button" data-preview-reopen="${e(preview.id)}" aria-label="Open preview ${e(label)} again">Open again</button>` : "",
    canDelete && deletable && confirming !== preview.id ? `<button class="btn sm danger" type="button" data-preview-delete="${e(preview.id)}" aria-label="${retry ? "Finish deleting the files of" : "Delete the files of"} preview ${e(label)}">${retry ? "Finish deleting" : "Delete files"}</button>` : "",
  ].join("");
  const confirm = canDelete && deletable && confirming === preview.id
    ? `<div class="preview-confirm" role="group" aria-label="Delete the files of preview ${e(label)}"><p class="run-reason">${retry ? `Remove the files of <b>${e(preview.name)}</b> that were left behind last time?` : `Delete the files of <b>${e(preview.name)}</b> (${e(mib(preview.size_bytes))}, this card)? People can no longer open it, and this cannot be undone.`}</p><div class="preview-acts"><button class="btn sm danger" type="button" data-preview-delete-confirm="${e(preview.id)}">Delete files</button><button class="btn sm" type="button" data-preview-delete-cancel>Cancel</button></div></div>`
    : "";
  return `<li data-preview="${e(preview.id)}" data-state="${e(state)}"><p class="run-line"><span class="tag run-state">${e(STATE_WORDS[state])}</span><b>${e(preview.name)}</b>${revision}${published}</p><p class="run-reason" data-preview-reason>${e(preview.reason_text || "")}</p>${preview.next ? `<p class="run-reason" data-preview-next>${e(preview.next)}</p>` : ""}${stops}${acts ? `<div class="preview-acts">${acts}</div>` : ""}${confirm}</li>`;
}

function usageHTML(found) {
  const usage = found?.usage, limits = found?.limits;
  if (!usage || !limits) return "";
  return `<p class="run-reason" data-preview-usage>${e(usage.ready)} of ${e(limits.max_ready)} open previews in this workspace · ${e(mib(usage.bytes))} of ${e(mib(limits.max_workspace_bytes))} used.</p>`;
}

function ruleHTML(found) {
  const rule = found?.rule;
  if (!rule) return "";
  const text = `<p class="run-reason" data-preview-rule-text>${e(rule.text || "")}</p>`;
  if (!found.can_set_rule) return text;
  const field = (name, words, max, value) => `<label class="run-start-field">${words}<input type="number" inputmode="numeric" name="${name}" min="1" max="${max}" step="1" value="${value == null ? "" : e(value)}" placeholder="Never"></label>`;
  return `${text}<details class="preview-rule" data-preview-rule><summary>Change the stop rule</summary><form class="run-start" data-preview-rule-form><p>Leave a field empty to keep previews served until someone stops them. Stopping never deletes the files.</p><div class="run-start-row">${field("stop_days_after_pr", "Days after the pull request is merged or closed", 365, rule.stop_days_after_pr)}${field("max_age_days", "Days after it was published", 3650, rule.max_age_days)}</div><button class="btn sm" type="submit">Save rule</button></form></details>`;
}

// The inner HTML of the section for a loaded list; `note` is the last result or refusal.
export function cardPreviewsHTML(found, note = "", confirming = "") {
  const previews = Array.isArray(found?.previews) ? found.previews : [];
  const options = { canDelete: Boolean(found?.can_delete), confirming };
  const list = previews.length
    ? `<ul class="run-list">${previews.map((preview) => previewHTML(preview, options)).join("")}</ul>`
    : `<p class="run-empty" data-preview-empty>No preview yet. When a Coworker publishes one for this card, it appears here and stays after the work session ends.</p>`;
  return `<div class="sec-h"><h3>Previews</h3></div>${list}${usageHTML(found)}${ruleHTML(found)}<p class="run-note" role="status" aria-live="polite">${e(note)}</p>`;
}

export const offlineHTML = (note = "") => `<div class="sec-h"><h3>Previews</h3></div><p class="run-empty" data-preview-offline>Could not load the previews. Check the connection and try again.</p><button class="btn sm" type="button" data-preview-retry>Try again</button><p class="run-note" role="status" aria-live="polite">${e(note)}</p>`;

const keyOf = (card) => `${card.board}/${card.number}`;

// The one insertion renderDetails makes: an empty, hidden section that attachCardPreviews fills.
// There is no module-level cache: a list is only ever repainted from the panel that showed it a
// moment ago (see `previous`), so nothing can carry over to another session or workspace.
export const cardPreviewsSlot = (card) => `<section class="card-previews" data-card-previews data-card-key="${e(keyOf(card))}" aria-label="Previews" hidden></section>`;

// What the panel showed before a redraw, so the same card's list does not blink away and back.
export function previousPreviews(mount) {
  const slot = mount?.querySelector?.("[data-card-previews]");
  return slot && !slot.hidden && slot.innerHTML ? { key: slot.dataset?.cardKey, html: slot.innerHTML } : null;
}

const days = (value) => {
  const text = String(value ?? "").trim();
  return text === "" ? null : Number(text);
};

// Load the card's previews into the slot inside `mount` and wire Stop, Open again and the rule.
export async function attachCardPreviews(mount, card, { api = request, previous = null } = {}) {
  const slot = mount.querySelector?.("[data-card-previews]");
  if (!slot || card?.number == null) return;
  const key = keyOf(card);
  let revision = 0, last = null, confirming = "";
  const live = (token) => token === revision && slot.isConnected;
  const show = (found, note = "") => {
    last = found;
    slot.innerHTML = cardPreviewsHTML(found, note, confirming);
    slot.hidden = false;
  };
  // A redraw of the same card shows what the panel showed a moment ago until the load answers.
  const painted = previous?.key === key && Boolean(previous.html);
  if (painted) { slot.innerHTML = previous.html; slot.hidden = false; }
  async function load(note = "") {
    const token = ++revision;
    try {
      const found = await api(`/api/previews?card=${encodeURIComponent(card.number)}`);
      if (live(token)) show(found && typeof found === "object" ? found : {}, note);
    } catch (error) {
      if (!live(token)) return;
      // A card this person cannot see (or no longer exists): show nothing, as for a missing card.
      if ([403, 404].includes(error?.status)) { last = null; slot.hidden = true; slot.innerHTML = ""; return; }
      if (last) show(last, "Could not refresh the previews. Showing the last list.");
      else if (painted && slot.innerHTML === previous.html) {
        const status = slot.querySelector?.(".run-note");
        if (status) status.textContent = "Could not refresh the previews. Showing the last list.";
      } else { slot.innerHTML = offlineHTML(note); slot.hidden = false; }
    }
  }
  async function act(control, path, method, body, done) {
    control.closest?.("li, form")?.querySelectorAll?.("button, input").forEach((item) => { item.disabled = true; });
    let note;
    try {
      const result = await api(path, { method, body });
      note = typeof done === "function" ? done(result) : done;
    } catch (error) {
      note = refusalWords(error);
    }
    // A refusal usually means the preview moved on: reload so the list shows what is true now.
    await load(note);
  }
  slot.addEventListener("click", (event) => {
    const stop = event.target.closest?.("[data-preview-stop]");
    if (stop) { void act(stop, `/api/previews/${encodeURIComponent(stop.dataset.previewStop)}/stop`, "POST", {}, "Stopped. The files are kept; Open again serves them again."); return; }
    const reopen = event.target.closest?.("[data-preview-reopen]");
    if (reopen) { void act(reopen, `/api/previews/${encodeURIComponent(reopen.dataset.previewReopen)}/open-again`, "POST", {}, "Served again."); return; }
    const remove = event.target.closest?.("[data-preview-delete]");
    if (remove) { confirming = remove.dataset.previewDelete; if (last) show(last); return; }
    if (event.target.closest?.("[data-preview-delete-cancel]")) { confirming = ""; if (last) show(last); return; }
    const sure = event.target.closest?.("[data-preview-delete-confirm]");
    if (sure) {
      const id = sure.dataset.previewDeleteConfirm;
      confirming = "";
      void act(sure, `/api/previews/${encodeURIComponent(id)}`, "DELETE", { confirm: id }, (result) => result?.files_remaining
        ? "Some files could not be removed. Delete files again to finish."
        : "Deleted the files. People can no longer open this preview.");
      return;
    }
    if (event.target.closest?.("[data-preview-retry]")) void load();
  });
  slot.addEventListener("submit", (event) => {
    const form = event.target.closest?.("[data-preview-rule-form]");
    if (!form) return;
    event.preventDefault();
    const body = { stop_days_after_pr: days(form.elements.stop_days_after_pr.value), max_age_days: days(form.elements.max_age_days.value) };
    const bad = Object.values(body).some((value) => value !== null && !(Number.isInteger(value) && value >= 1));
    const note = slot.querySelector?.(".run-note");
    if (bad) { if (note) note.textContent = "Use whole days of 1 or more, or leave the field empty."; return; }
    void act(form, "/api/previews/rules", "PUT", body, "Saved the stop rule.");
  });
  await load();
}
