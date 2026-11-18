import { forgetProjectPanels, panelHTML } from "./projectpanel.js";

// Project settings > Project panel and Coordinator (G24). Both reuse the existing writes: the panel
// route (PUT /api/projects/<slug>/panel) and the board settings route (PATCH /api/boards/<slug>, the
// board_set rules). The page only offers the controls; the server decides who may use them, and a
// refused or failed write leaves the old state in place and says so.

export const PRESENT_COPY = Object.freeze({
  panelLoadError: "Could not load the project panel. Nothing was changed. Try again.",
  panelReason: "Only the workspace owner, or someone who may change this project's board, can edit the panel.",
  panelNone: "This project has no panel.",
  panelHelp: "The panel sits at the top of the project's board for everyone who opens it: a title, up to 6 highlight lines (200 characters each) and up to 6 links. Saving replaces the current panel.",
  panelSaved: "Panel saved. This is what people see on the board.",
  panelCleared: "Panel cleared. The board shows no panel now.",
  panelEmpty: "Add at least one highlight or one link, or use Clear panel.",
  panelSaveError: "Could not save the panel. Nothing was changed.",
  panelDenied: "You may not change this project's panel. Nothing was changed.",
  panelClearConfirm: "Clear the panel? Everyone loses these highlights and links on the board. This can't be undone here.",
  coordLoadError: "Could not load the coordinator. Nothing was changed.",
  coordReason: "Only the workspace owner can appoint, replace or clear the coordinator.",
  coordDistinct: "This only chooses which connected agent coordinates the project. It does not change any agent's model (see Agent models and escalation below) and does not create, rotate or revoke a token (see Connect agent under Coworker).",
  coordNone: "No coordinator is appointed. Questions from agents on this project come to the workspace owner.",
  coordNoCandidates: "No connected agent can coordinate this project yet. An agent qualifies when it can read and write this project's memory. Connect one in Settings, then come back.",
  coordDenied: "Only the workspace owner can change the coordinator. Nothing was changed.",
  coordGone: "That agent can no longer coordinate this project. Nothing was changed. Pick another.",
  coordError: "Could not change the coordinator. Nothing was changed.",
});

const styles = `.project-presentation .pp-form { display: flex; flex-direction: column; gap: 10px; }
.project-presentation label.pp-field { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.project-presentation textarea, .project-presentation input[type=text] { font: inherit; width: 100%; box-sizing: border-box; }
.project-presentation .pp-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.project-presentation .pp-preview { border: 1px solid var(--line-strong); border-radius: var(--r); padding: 10px 12px; background: var(--well, var(--surface)); }
.project-presentation .pp-preview ul { margin: 6px 0 10px; padding-left: 18px; }
.project-presentation .pp-readonly { margin: 0; font-size: 13px; }
.project-presentation .project-panel { margin: 0; }`;
if (globalThis.document?.adoptedStyleSheets) {
  const sheet = new CSSStyleSheet();
  sheet.replaceSync(styles);
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
}

const PANEL_FORMAT = "commonplace.panel/1";
const connection = (ref) => String(ref || "").replace(/^conn:/, "");
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const failureText = (error, fallback, denied) => {
  if (error?.status === 403 && denied) return denied;
  const text = error?.payload?.error;
  return error?.status && error.status < 500 && error.status !== 403 && error.status !== 401 && typeof text === "string" && text ? `${fallback} ${text}.` : fallback;
};

/** The panel as the three form fields: a title, one highlight per line, one "Label address" link per line. */
export function panelToForm(panel) {
  const doc = panel && typeof panel === "object" ? panel : {};
  return {
    title: typeof doc.title === "string" ? doc.title : "",
    highlights: (Array.isArray(doc.highlights) ? doc.highlights : []).join("\n"),
    links: (Array.isArray(doc.links) ? doc.links : []).map((link) => `${link.label} ${link.url}`).join("\n"),
  };
}

/** The form fields as a panel document, or { error } in plain words. The server checks every limit. */
export function formToPanel({ title = "", highlights = "", links = "" }) {
  const lines = (text) => String(text).split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const doc = { format: PANEL_FORMAT, highlights: lines(highlights), links: [] };
  if (title.trim()) doc.title = title.trim();
  for (const [index, line] of lines(links).entries()) {
    const match = /^(.*\S)\s+(\S+)$/.exec(line);
    if (!match) return { error: `Link ${index + 1} needs a label and an address, for example: Runbook https://example.com/runbook` };
    doc.links.push({ label: match[1], url: match[2] });
  }
  if (!doc.highlights.length && !doc.links.length) return { error: PRESENT_COPY.panelEmpty };
  return { panel: doc };
}

function panelEditorHTML(panel, e, { editable }) {
  const shown = panelHTML(panel);
  const result = `<div data-panel-result>${shown || `<p class="set-note" data-panel-none>${e(PRESENT_COPY.panelNone)}</p>`}</div>`;
  if (!editable) return `${result}<p class="pp-readonly set-note" data-panel-reason>${e(PRESENT_COPY.panelReason)}</p>`;
  const form = panelToForm(panel);
  return `<p class="lead">${e(PRESENT_COPY.panelHelp)}</p><form class="pp-form" data-panel-form novalidate>`
    + `<label class="pp-field">Title (optional)<input type="text" name="title" maxlength="60" value="${e(form.title)}"></label>`
    + `<label class="pp-field">Highlights, one per line<textarea name="highlights" rows="4">${e(form.highlights)}</textarea></label>`
    + `<label class="pp-field">Links, one per line: a label, a space, then the address (https://…)<textarea name="links" rows="3">${e(form.links)}</textarea></label>`
    + `<div class="pp-actions"><button type="submit" class="btn primary" data-panel-save>Save panel</button>`
    + `<button type="button" class="btn" data-panel-clear${panel ? "" : " disabled"}>Clear panel</button></div>`
    + `<div data-panel-confirm hidden><p>${e(PRESENT_COPY.panelClearConfirm)}</p><div class="pp-actions"><button type="button" class="btn danger" data-panel-clear-ok>Clear panel</button><button type="button" class="btn" data-panel-clear-cancel>Keep it</button></div></div>`
    + `<p role="status" data-panel-status></p></form>${result}`;
}

export async function mountPanelEditor(host, { slug, project, api, e, current = () => true, onSaved = () => {} }) {
  if (!host) return;
  const path = `/api/projects/${encodeURIComponent(slug)}/panel`;
  const active = () => current() && host.isConnected;
  const editable = Boolean(project.can_edit_panel);
  host.classList.add("project-presentation");
  host.innerHTML = '<p role="status">Loading the project panel…</p>';
  async function load() {
    let panel;
    try { panel = (await api(path))?.panel || null; }
    catch {
      if (!active()) return;
      host.innerHTML = `<p role="alert" data-panel-error>${e(PRESENT_COPY.panelLoadError)}</p><button type="button" class="btn" data-panel-retry>Try again</button>`;
      host.querySelector("[data-panel-retry]").onclick = () => { host.innerHTML = '<p role="status">Loading the project panel…</p>'; load(); };
      return;
    }
    if (active()) draw(panel);
  }
  function draw(panel, message = "") {
    host.innerHTML = panelEditorHTML(panel, e, { editable });
    const status = host.querySelector("[data-panel-status]");
    if (status && message) status.textContent = message;
    const form = host.querySelector("[data-panel-form]");
    if (!form) return;
    const save = form.querySelector("[data-panel-save]"), clear = form.querySelector("[data-panel-clear]"), confirm = form.querySelector("[data-panel-confirm]");
    const field = (name) => form.querySelector(`[name="${name}"]`).value;
    // A failed write keeps the typed text and the saved panel; the next try starts from there.
    async function write(doc, done) {
      save.disabled = true; clear.disabled = true;
      try {
        const stored = await api(path, { method: "PUT", body: { panel: doc } });
        if (!active()) return;
        forgetProjectPanels();
        onSaved();
        draw(stored?.panel || null, done);
      } catch (error) {
        if (!active()) return;
        status.textContent = failureText(error, PRESENT_COPY.panelSaveError, PRESENT_COPY.panelDenied);
        save.disabled = false; clear.disabled = !panel;
      }
    }
    form.onsubmit = (event) => {
      event.preventDefault();
      if (save.disabled) return;
      confirm.hidden = true;
      const parsed = formToPanel({ title: field("title"), highlights: field("highlights"), links: field("links") });
      if (parsed.error) { status.textContent = parsed.error; return; }
      status.textContent = "";
      write(parsed.panel, PRESENT_COPY.panelSaved);
    };
    clear.onclick = () => { confirm.hidden = false; confirm.querySelector("[data-panel-clear-cancel]").focus(); };
    form.querySelector("[data-panel-clear-cancel]").onclick = () => { confirm.hidden = true; clear.focus(); };
    form.querySelector("[data-panel-clear-ok]").onclick = () => { if (!save.disabled) { confirm.hidden = true; write(null, PRESENT_COPY.panelCleared); } };
  }
  await load();
}

/** The plain-language effect of a coordinator change; a list of sentences, none empty. */
export function coordinatorEffect({ current, next, candidates = [], effect = {}, name = "this project" }) {
  const label = (actor) => candidates.find((c) => c.actor === actor)?.name || connection(actor) || "An agent";
  const waiting = Number(effect.waiting_asks) || 0, open = Number(effect.open_cards) || 0;
  const lines = [];
  if (current) {
    lines.push(`${label(current)} stops coordinating ${name} and loses the stored approval it had to answer asks for you.`);
    lines.push(waiting ? `${plural(waiting, "open ask", "open asks")} addressed to ${label(current)} will come to you instead.` : `No open asks are waiting on ${label(current)}.`);
  }
  if (next) {
    lines.push(`${label(next)} becomes the coordinator. New questions from agents on ${name} go to it while it is connected; otherwise they come to you.`);
    lines.push(`${label(next)} starts without any stored approval to answer asks for you.`);
  } else lines.push(`With no coordinator, new questions from agents on ${name} come to you.`);
  lines.push(`Cards, chat and project memory stay as they are: ${plural(open, "open card", "open cards")} keep their status.`);
  lines.push("No agent model changes, and no token is created, rotated or revoked.");
  return lines;
}

function coordinatorHTML(state, project, e, { selected, preview = "", busy = false }) {
  const { coordinator, candidates } = state;
  const who = coordinator ? candidates.find((c) => c.actor === coordinator)?.name || connection(coordinator) : "";
  const current = coordinator ? `<p data-coordinator-current>Current coordinator: <b>${e(who)}</b></p>` : `<p data-coordinator-current>${e(PRESENT_COPY.coordNone)}</p>`;
  const options = [{ actor: "", name: "No coordinator", note: "Questions from agents come to you." }, ...candidates.map((c) => ({ actor: c.actor, name: c.name, note: c.current ? "Current coordinator" : "" }))];
  const empty = !candidates.length ? `<p class="set-note" data-coordinator-empty>${e(PRESENT_COPY.coordNoCandidates)}</p>` : "";
  const radios = `<fieldset class="radios" data-coordinator-options${busy ? " disabled" : ""}><legend class="set-note">Coordinator</legend>${options.map((o) => `<label class="radio${o.actor === selected ? " on" : ""}"><input type="radio" name="coordinator" value="${e(o.actor)}"${o.actor === selected ? " checked" : ""}><span><b>${e(o.name)}</b>${o.note ? `<small>${e(o.note)}</small>` : ""}</span></label>`).join("")}</fieldset>`;
  const changed = selected !== (coordinator || "");
  return `${current}${empty}${radios}<div class="pp-actions"><button type="button" class="btn primary" data-coordinator-review${changed && !busy ? "" : " disabled"}>Review change</button></div>${preview}<p role="status" data-coordinator-status></p><p class="set-note" data-coordinator-distinct>${e(PRESENT_COPY.coordDistinct)}</p>`;
}

export async function mountCoordinator(host, { slug, project, api, e, current = () => true, onChanged = () => {} }) {
  if (!host) return;
  const path = `/api/boards/${encodeURIComponent(slug)}`;
  const active = () => current() && host.isConnected;
  host.classList.add("project-presentation");
  if (!project.can_configure) {
    const main = project.main || {};
    const line = main.source === "coordinator" && main.name ? `Coordinator: ${main.name}.` : PRESENT_COPY.coordNone;
    host.innerHTML = `<p class="pp-readonly" data-coordinator-current>${e(line)}</p><p class="pp-readonly set-note" data-coordinator-reason>${e(PRESENT_COPY.coordReason)}</p><p class="pp-readonly set-note" data-coordinator-distinct>${e(PRESENT_COPY.coordDistinct)}</p>`;
    return;
  }
  host.innerHTML = '<p role="status">Loading the coordinator…</p>';
  let state = null, selected = "";
  const read = async () => {
    const result = await api(`${path}/coordinator`);
    if (!result || !Array.isArray(result.candidates)) throw new Error("unreadable coordinator");
    return result;
  };
  async function load() {
    try { state = await read(); }
    catch {
      if (!active()) return;
      host.innerHTML = `<p role="alert" data-coordinator-error>${e(PRESENT_COPY.coordLoadError)}</p><button type="button" class="btn" data-coordinator-retry>Try again</button>`;
      host.querySelector("[data-coordinator-retry]").onclick = () => { host.innerHTML = '<p role="status">Loading the coordinator…</p>'; load(); };
      return;
    }
    selected = state.coordinator || "";
    if (active()) draw();
  }
  function draw({ preview = "", message = "", busy = false } = {}) {
    host.innerHTML = coordinatorHTML(state, project, e, { selected, preview, busy });
    if (message) host.querySelector("[data-coordinator-status]").textContent = message;
    host.querySelectorAll('input[name="coordinator"]').forEach((input) => {
      input.onchange = () => { selected = input.value; draw(); host.querySelector(`input[value="${CSS.escape(selected)}"]`)?.focus(); };
    });
    host.querySelector("[data-coordinator-review]").onclick = review;
  }
  // The preview is read fresh: the eligible list and the counts are the server's at this moment.
  async function review() {
    let latest;
    try { latest = await read(); }
    catch { if (active()) draw({ message: PRESENT_COPY.coordLoadError }); return; }
    if (!active()) return;
    state = latest;
    if (selected && !state.candidates.some((c) => c.actor === selected)) { selected = state.coordinator || ""; draw({ message: PRESENT_COPY.coordGone }); return; }
    if (selected === (state.coordinator || "")) { draw({ message: "That is already the coordinator. Nothing to change." }); return; }
    const lines = coordinatorEffect({ current: state.coordinator, next: selected || null, candidates: state.candidates, effect: state.effect, name: project.name || "this project" });
    const preview = `<div class="pp-preview" data-coordinator-preview role="group" aria-label="What this change does"><b>What this change does</b><ul>${lines.map((line) => `<li>${e(line)}</li>`).join("")}</ul><div class="pp-actions"><button type="button" class="btn primary" data-coordinator-confirm>Confirm change</button><button type="button" class="btn" data-coordinator-cancel>Cancel</button></div></div>`;
    draw({ preview });
    host.querySelector("[data-coordinator-cancel]").onclick = () => { selected = state.coordinator || ""; draw(); host.querySelector("[data-coordinator-review]")?.focus(); };
    host.querySelector("[data-coordinator-confirm]").onclick = confirmChange;
  }
  async function confirmChange() {
    const next = selected || null, previous = state.coordinator || "";
    const names = Object.fromEntries(state.candidates.map((c) => [c.actor, c.name]));
    const button = host.querySelector("[data-coordinator-confirm]");
    button.disabled = true;
    try {
      await api(path, { method: "PATCH", body: { coordinator: next } });
    } catch (error) {
      if (!active()) return;
      // The write did not go through, so the old coordinator stays shown; re-read to be sure.
      const message = error?.status === 403 && !error?.payload?.error?.includes?.("active agent") ? PRESENT_COPY.coordDenied
        : error?.status === 403 ? PRESENT_COPY.coordGone : PRESENT_COPY.coordError;
      selected = previous;
      try { state = await read(); selected = state.coordinator || ""; } catch { /* keep the last confirmed state */ }
      if (active()) draw({ message: `${message}${state.coordinator ? ` ${names[state.coordinator] || connection(state.coordinator)} is still the coordinator.` : ""}` });
      return;
    }
    if (!active()) return;
    try { state = await read(); } catch { state = { ...state, coordinator: next, effect: state.effect }; }
    selected = state.coordinator || "";
    const label = next ? names[next] || connection(next) : "";
    draw({ message: next ? `Coordinator changed to ${label}.` : "Coordinator cleared. Questions from agents come to you." });
    onChanged();
  }
  await load();
}
