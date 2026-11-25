// Organise memory: read graph facts with their history, stored sources and diary entries, and links,
// and make the few changes that keep history (end or replace a fact, add or remove a link).
// Saved words are shown exactly as stored (escaped, never summarised). The server decides who may do
// what; every change is previewed first and applied only after an explicit confirm. Nothing here deletes.
import { api, escape as e } from "./api.js";

const enc = encodeURIComponent;
const VIEWS = [["facts", "Facts"], ["sources", "Stored sources and diary"], ["links", "Links"]];
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const words = (value) => `<pre class="organise-words">${e(value)}</pre>`;
const section = (title, body, attrs = "") => `<section class="organise-section"${attrs}><h2>${e(title)}</h2>${body}</section>`;
const notice = (kind, title, text = "", extra = "") => `<div class="organise-notice" data-kind="${e(kind)}" role="${kind === "error" ? "alert" : "status"}"><strong>${e(title)}</strong> ${e(text)}${extra}</div>`;
const link = (view, params = {}) => `/app/organise.html?${new URLSearchParams({ view, ...params })}`;

export function when(value) {
  const at = new Date(value);
  return value && !Number.isNaN(at.getTime()) ? `<time datetime="${e(value)}">${e(at.toISOString().slice(0, String(value).length > 10 ? 16 : 10).replace("T", " "))}${String(value).length > 10 ? " UTC" : ""}</time>` : "";
}

// Only a 401 is signed out. A timeout or another failure is a failed read, and says so.
export function failureHTML(error, what = "this page") {
  if (error?.status === 401) return notice("signed-out", "Sign in to see this.", "Your session has ended, or you are not signed in.", ' <a href="/app/">Sign in</a>, then open Organise memory again.');
  if (error?.status === 403) return notice("denied", "Your access does not include this.", error?.payload?.error || "Ask a workspace owner if you need it. Nothing was changed.");
  const why = error?.payload?.error || `${what[0].toUpperCase()}${what.slice(1)} could not be read just now. This does not mean you are signed out.`;
  return notice("error", "Could not load.", why, ' <button type="button" class="btn" data-organise-retry>Try again</button>');
}

// What a change attempt that failed says: the old state stays, and the page says so.
export function refusal(error) {
  if (error?.status === 401) return { message: "You are signed out, so nothing was changed.", next: "Sign in at /app/, then try again." };
  const text = error?.payload?.error;
  if (text) return { message: text, next: error.status === 409 || error.status === 404 ? "Reload the page to see what is stored now." : "" };
  return { message: "The request did not finish, so the page cannot tell whether anything changed.", next: "Reload to see what is stored. This does not mean you are signed out." };
}

function nav(view) {
  return `<nav class="organise-tabs" aria-label="Memory views">${VIEWS.map(([id, label]) => `<a href="${e(link(id))}"${id === view ? ' aria-current="page"' : ""}>${e(label)}</a>`).join("")}</nav>`;
}
const head = (view) => `<header class="organise-head"><h1>Organise memory</h1><p class="organise-meta">See what is stored and how it is joined. Stored words are shown exactly as saved. Nothing on this page deletes or rewrites them.</p>${nav(view)}</header>`;

// ---- Facts -------------------------------------------------------------------------------------
const sourceLine = (s) => Object.entries(s || {}).filter(([k, v]) => v !== null && v !== "" && k !== "id").map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(" · ");

function validity(f) {
  const from = f.valid_from ? `From ${when(f.valid_from)}` : "Start date not recorded";
  return f.valid_to ? `${from} · Ended ${when(f.valid_to)}` : `${from} · Current`;
}

export function factsHTML(data, state = {}) {
  if (!data || !Array.isArray(data.facts)) return section("Facts", notice("unknown", "This page did not understand the answer.", "It shows nothing rather than guess. Reload to try again."));
  const search = `<form class="organise-search" data-organise-search><label for="organise-entity">Show facts about</label><div class="organise-inline"><input id="organise-entity" name="entity" value="${e(data.entity ?? "")}" autocomplete="off" spellcheck="false" placeholder="A name, exactly as stored"><button class="btn" type="submit">Show</button></div></form>`;
  if (!data.facts.length) {
    const why = data.entity
      ? `No fact mentions ${e(data.entity)} that your access includes. Names are matched exactly as stored: check the spelling and capitals, or clear the name to see everything.`
      : "No fact is stored yet, or none your access includes. Facts are saved by agents and from chat; this page shows them with their history and cannot make one up.";
    return section("Facts", `${search}<p class="organise-empty">${why}</p>`);
  }
  const rows = data.facts.map((f, i) => `<li class="organise-fact" data-current="${f.valid_to ? "false" : "true"}"><p class="organise-triple"><strong>${e(f.subject)}</strong> <span>${e(String(f.predicate).replace(/_/g, " "))}</span> <strong>${e(f.object)}</strong></p><p class="organise-meta">${validity(f)}${f.wing ? ` · Wing ${e(f.wing)}` : " · Wing not recorded"}${f.sensitivity ? ` · ${e(f.sensitivity)}` : ""}</p>${(f.sources || []).length ? `<ul class="organise-sources">${f.sources.map((s) => `<li>${e(sourceLine(s))}</li>`).join("")}</ul>` : '<p class="organise-meta">No source recorded for this fact.</p>'}${f.valid_to ? "" : `<div class="organise-actions"><button type="button" class="btn" data-organise-end="${i}">End this fact…</button><button type="button" class="btn" data-organise-replace="${i}">Replace with a new value…</button></div>`}</li>`).join("");
  const total = Number.isSafeInteger(data.total) ? data.total : data.facts.length;
  const more = data.offset + data.facts.length < total;
  const pager = `<p class="organise-meta">Showing ${data.offset + 1}–${data.offset + data.facts.length} of ${total}, oldest first.</p><div class="organise-actions">${data.offset > 0 ? `<button type="button" class="btn" data-organise-page="${Math.max(0, data.offset - data.limit)}">Earlier</button>` : ""}${more ? `<button type="button" class="btn" data-organise-page="${data.offset + data.limit}">Later</button>` : ""}</div>`;
  return section("Facts", `${search}<ul class="organise-list">${rows}</ul>${pager}`);
}

// ---- Sources and diary -----------------------------------------------------------------------
export function placesHTML(data, wing, room) {
  if (!data || !Array.isArray(data.wings)) return notice("unknown", "This page did not understand the answer.", "It lists no places rather than guess. Reload to try again.");
  if (!data.wings.length) return `<p class="organise-empty">Nothing is stored yet, or none of it is in a place your access includes. Stored sources and diary entries appear here once something is saved.</p>`;
  return `<ul class="organise-places">${data.wings.map((w) => `<li><strong>${e(w.wing)}</strong><ul>${w.rooms.map((r) => `<li><a href="${e(link("sources", { wing: w.wing, room: r.room }))}"${w.wing === wing && r.room === room ? ' aria-current="true"' : ""}>${e(r.room === "diary" ? "Diary entries" : r.room)}</a> <span class="organise-meta">${e(plural(r.count, "stored text"))}</span></li>`).join("")}</ul></li>`).join("")}</ul>`;
}

export function sourcesHTML(places, data, wing, room, open = {}) {
  const picker = section("Where", placesHTML(places, wing, room));
  if (!wing && !room) return `${picker}${section("Stored texts", '<p class="organise-empty">Pick a room above to read what is stored in it. Diary entries are the room named “Diary entries”. Everything is read-only here.</p>')}`;
  if (!data || !Array.isArray(data.drawers)) return `${picker}${section("Stored texts", notice("unknown", "This page did not understand the answer.", "It shows nothing rather than guess. Reload to try again."))}`;
  const where = `${wing || "any wing"} / ${room || "any room"}`;
  if (!data.drawers.length) return `${picker}${section("Stored texts", `<p class="organise-empty">Nothing is stored in ${e(where)} that your access includes. The room may be empty, or it may not exist.</p>`)}`;
  const rows = data.drawers.map((d) => {
    const full = open[d.id];
    const body = full?.text !== undefined ? words(full.text) : `${words(d.preview ?? "")}<button type="button" class="btn" data-organise-read="${e(d.id)}">Read the whole text</button>${full?.error ? ` <span class="organise-meta" role="alert">${e(full.error)}</span>` : ""}`;
    return `<li class="organise-source"><p class="organise-meta"><code>${e(d.id)}</code>${d.filed_at ? ` · saved ${when(d.filed_at)}` : ""}${d.added_by ? ` · by ${e(d.added_by)}` : ""}${d.source_file ? ` · from ${e(d.source_file)}` : ""}</p>${body}</li>`;
  }).join("");
  const total = Number.isSafeInteger(data.total) ? data.total : data.drawers.length;
  const pager = `<p class="organise-meta">Showing ${data.offset + 1}–${data.offset + data.drawers.length} of ${total}.</p><div class="organise-actions">${data.offset > 0 ? `<a class="btn" href="${e(link("sources", { wing, room, offset: Math.max(0, data.offset - data.limit) }))}">Earlier</a>` : ""}${data.offset + data.drawers.length < total ? `<a class="btn" href="${e(link("sources", { wing, room, offset: data.offset + data.limit }))}">Later</a>` : ""}</div>`;
  return `${picker}${section(`Stored texts in ${where}`, `<ul class="organise-list">${rows}</ul>${pager}`)}`;
}

// ---- Links -------------------------------------------------------------------------------------
const endpoint = (end) => (end && typeof end === "object" ? `${end.wing ?? "?"} / ${end.room ?? "?"}` : "unknown place");

export function linksHTML(data, state = {}) {
  if (!data || !Array.isArray(data.tunnels)) return section("Links", notice("unknown", "This page did not understand the answer.", "It shows nothing rather than guess. Reload to try again."));
  const rows = data.tunnels.map((t, i) => `<li class="organise-link"><p><strong>${e(endpoint(t.source))}</strong> ↔ <strong>${e(endpoint(t.target))}</strong></p><p class="organise-meta">${t.label ? `Label: ${e(t.label)} · ` : "No label · "}Used ${e(plural(t.access_count ?? 0, "time"))}${t.created_at ? ` · made ${when(t.created_at)}` : ""}</p><div class="organise-actions"><button type="button" class="btn" data-organise-unlink="${i}">Remove this link…</button></div></li>`).join("");
  const list = data.tunnels.length ? `<ul class="organise-list">${rows}</ul>${data.total > data.shown ? `<p class="organise-meta">The first ${data.shown} of ${data.total} links are shown.</p>` : ""}` : `<p class="organise-empty">No links between places have been made yet, or none your access includes. A link joins two rooms so each leads to the other; add one below.</p>`;
  const f = state.form || {};
  const field = (name, label) => `<label>${e(label)}<input name="${name}" value="${e(f[name] ?? "")}" autocomplete="off" spellcheck="false" required></label>`;
  const add = `<form class="organise-add" data-organise-add>${field("source_wing", "First place: wing")}${field("source_room", "First place: room")}${field("target_wing", "Second place: wing")}${field("target_room", "Second place: room")}<label>Label (optional)<input name="label" value="${e(f.label ?? "")}" autocomplete="off"></label><button class="btn" type="submit">Review the link…</button></form>`;
  const hallways = data.hallways
    ? `<p class="organise-meta">${e(plural(data.hallways.total ?? data.hallways.rows.length, "automatic link"))} inside single wings. These are made by memory itself and are shown here only to read.</p>`
    : `<p class="organise-meta">${e(data.hallway_note || "Automatic links inside a wing could not be read just now.")}</p>`;
  return `${section("Links between places", list)}${section("Add a link", add)}${section("Automatic links", hallways)}`;
}

// ---- Confirming a change ------------------------------------------------------------------------
const TITLES = { invalidate: "End this fact", supersede: "Replace this fact", link_add: "Add this link", link_remove: "Remove this link" };

function effectDetail(p) {
  if (p.action === "invalidate" || p.action === "supersede") {
    const f = p.fact || {};
    return `<dt>The fact now</dt><dd>${e(f.subject)} · ${e(String(f.predicate).replace(/_/g, " "))} · ${e(f.object)} (${validity(f)})</dd>${p.action === "supersede" ? `<dt>New value, exactly as typed</dt><dd>${words(p.new_object)}</dd>` : ""}`;
  }
  const ends = `${endpoint(p.source)} ↔ ${endpoint(p.target)}`;
  return `<dt>Link</dt><dd>${e(ends)}${p.label ? ` (label: ${e(p.label)})` : ""}</dd>`;
}

export function confirmHTML(pending) {
  const p = pending.preview;
  const failed = pending.error ? notice("error", pending.error.message, pending.error.next || "The stored state is unchanged.") : "";
  return section(TITLES[p.action] || "Confirm", `<dl class="organise-facts">${effectDetail(p)}<dt>What happens</dt><dd><ul>${(p.lines || []).map((line) => `<li>${e(line)}</li>`).join("")}</ul></dd></dl>${failed}<div class="organise-actions"><button type="button" class="btn primary" data-organise-confirm${pending.busy ? " disabled" : ""}>${pending.busy ? "Working…" : e(`Confirm: ${(TITLES[p.action] || "apply").toLowerCase()}`)}</button><button type="button" class="btn" data-organise-cancel>Cancel, change nothing</button></div>`, " data-organise-confirm-panel");
}

export function replaceFormHTML(fact, error) {
  return section("Replace this fact", `<p>${e(fact.subject)} · ${e(String(fact.predicate).replace(/_/g, " "))} · ${e(fact.object)}</p><form class="organise-add" data-organise-replace-form><label>New value<input name="new_object" autocomplete="off" required></label>${error ? notice("error", error.message, error.next) : ""}<div class="organise-actions"><button class="btn" type="submit">Review the change…</button><button type="button" class="btn" data-organise-cancel>Cancel, change nothing</button></div></form>`, " data-organise-confirm-panel");
}

// ---- The page ----------------------------------------------------------------------------------
export function mountOrganise(host, { read = (path, options) => api(path, options), location = globalThis.location, timeoutMs = 15000, fields = (el) => Object.fromEntries(new FormData(el)) } = {}) {
  let request = 0, view = "facts", data = {}, ui = {}, params = new URLSearchParams();
  const paint = (html) => { host.innerHTML = html; host.setAttribute?.("aria-busy", "false"); };
  const body = () => {
    if (ui.pending) return confirmHTML(ui.pending);
    if (ui.replacing) return replaceFormHTML(ui.replacing.fact, ui.error);
    if (ui.error) return notice("error", ui.error.message, ui.error.next || "Nothing was changed.");
    return ui.notice || "";
  };
  const draw = () => {
    const panel = body();
    const main = view === "facts" ? factsHTML(data.facts) : view === "sources" ? sourcesHTML(data.places, data.sources, params.get("wing") || "", params.get("room") || "", ui.open) : linksHTML(data.links, ui);
    paint(`${head(view)}${panel}${main}`);
  };

  async function load() {
    const mine = ++request;
    params = new URLSearchParams(location?.search || "");
    view = VIEWS.some(([id]) => id === params.get("view")) ? params.get("view") : "facts";
    host.setAttribute?.("aria-busy", "true");
    host.innerHTML = `${head(view)}<p role="status" class="organise-state">Loading…</p>`;
    const keep = ui.notice;
    ui = keep ? { notice: keep } : {};
    try {
      if (view === "facts") data = { facts: await read(`/api/organise/facts?${new URLSearchParams({ ...(params.get("entity") ? { entity: params.get("entity") } : {}), offset: params.get("offset") || "0" })}`, { timeoutMs }) };
      else if (view === "sources") {
        const wing = params.get("wing") || "", room = params.get("room") || "";
        const places = await read("/api/organise/places", { timeoutMs });
        const sources = wing || room ? await read(`/api/organise/sources?${new URLSearchParams({ wing, room, offset: params.get("offset") || "0" })}`, { timeoutMs }) : null;
        data = { places, sources };
      } else data = { links: await read("/api/organise/links", { timeoutMs }) };
      if (mine === request) draw();
    } catch (error) { if (mine === request) paint(`${head(view)}${failureHTML(error, "this view")}`); }
  }

  const send = (path, payload) => read(path, { method: "POST", body: payload, timeoutMs: 60000 });
  const factBody = (fact) => ({ subject: fact.subject, predicate: fact.predicate, object: fact.object, ...(fact.wing ? { wing: fact.wing } : {}) });
  const preview = (path, payload) => {
    const mine = request;
    send(path, payload).then((answer) => { if (mine === request) { ui = { pending: { path, payload, preview: answer.preview, effect_id: answer.effect_id } }; draw(); } },
      (error) => { if (mine === request) { ui = { ...ui, error: refusal(error) }; draw(); } });
  };
  const go = (search) => { if (location?.assign) location.assign(`${location.pathname || "/app/organise.html"}${search}`); };

  host.addEventListener?.("submit", (event) => {
    event.preventDefault?.();
    const hit = (selector) => event.target?.closest?.(selector);
    const form = hit("[data-organise-search]") || hit("[data-organise-replace-form]") || hit("[data-organise-add]");
    if (!form) return;
    const values = fields(form);
    if (hit("[data-organise-search]")) { const entity = String(values.entity || "").trim(); go(`?${new URLSearchParams({ view: "facts", ...(entity ? { entity } : {}) })}`); return; }
    if (hit("[data-organise-replace-form]")) { const fact = ui.replacing.fact; ui = { ...ui, error: null }; preview("/api/organise/facts/supersede", { ...factBody(fact), new_object: String(values.new_object ?? "") }); return; }
    ui = { ...ui, form: values, error: null };
    preview("/api/organise/links/add", { source_wing: String(values.source_wing || "").trim(), source_room: String(values.source_room || "").trim(), target_wing: String(values.target_wing || "").trim(), target_room: String(values.target_room || "").trim(), label: String(values.label || "") });
  });

  host.addEventListener?.("click", (event) => {
    const hit = (selector) => event.target?.closest?.(selector);
    const index = (el, name) => Number(el.getAttribute?.(name) ?? el.dataset?.[name.replace(/^data-/, "").replace(/-(\w)/g, (_, c) => c.toUpperCase())]);
    if (hit("[data-organise-retry]")) { void load(); return; }
    if (hit("[data-organise-cancel]")) { ui = { form: ui.form, open: ui.open }; draw(); return; }
    const page = hit("[data-organise-page]");
    if (page) { go(`?${new URLSearchParams({ view: "facts", ...(params.get("entity") ? { entity: params.get("entity") } : {}), offset: String(index(page, "data-organise-page")) })}`); return; }
    const end = hit("[data-organise-end]");
    if (end) { ui = {}; preview("/api/organise/facts/invalidate", factBody(data.facts.facts[index(end, "data-organise-end")])); return; }
    const replace = hit("[data-organise-replace]");
    if (replace) { ui = { replacing: { fact: data.facts.facts[index(replace, "data-organise-replace")] } }; draw(); return; }
    const unlink = hit("[data-organise-unlink]");
    if (unlink) { ui = {}; preview("/api/organise/links/remove", { id: data.links.tunnels[index(unlink, "data-organise-unlink")].id }); return; }
    const reading = hit("[data-organise-read]");
    if (reading) {
      const id = reading.getAttribute?.("data-organise-read") || reading.dataset?.organiseRead, mine = request;
      read(`/api/organise/source?id=${enc(id)}`, { timeoutMs }).then((one) => { if (mine === request) { ui = { ...ui, open: { ...ui.open, [id]: { text: String(one.text ?? "") } } }; draw(); } },
        (error) => { if (mine === request) { ui = { ...ui, open: { ...ui.open, [id]: { error: refusal(error).message } } }; draw(); } });
      return;
    }
    if (hit("[data-organise-confirm]") && ui.pending && !ui.pending.busy) {
      const { path, payload, effect_id, preview: shown } = ui.pending, mine = request;
      ui = { ...ui, pending: { ...ui.pending, busy: true, error: null } }; draw();
      const confirmed = { ...payload, confirm: true, effect_id, ...(shown.ended ? { ended: shown.ended } : {}), ...(shown.at ? { at: shown.at } : {}) };
      send(path, confirmed).then(async () => {
        if (mine !== request) return;
        ui = { notice: notice("done", `${TITLES[shown.action] || "Change"}: done.`, "Nothing was deleted. The list below is read again from what is stored.") };
        await load();
      }, (error) => { if (mine === request) { ui = { ...ui, pending: { ...ui.pending, busy: false, error: refusal(error) } }; draw(); } });
    }
  });
  return load();
}
