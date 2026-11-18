// Read-only capabilities and status page. It only reads GET /api/status and writes nothing.
import { api, escape as e } from "./api.js";

const TITLES = { skills: "Skills", bridge: "Chat bridge", meetings: "Meeting notes", costs: "Cost summary" };
const ORDER = ["skills", "bridge", "meetings", "costs"];
const KNOWN = new Set(["ready", "empty", "off", "restricted", "error"]);
const LABELS = { empty: "Nothing yet", off: "Off", restricted: "Not available to you", error: "Could not read" };
const MEETING_STATES = { requested: "Requested", joining: "Joining", lobby: "Waiting in the lobby", announcing: "Announcing", live: "Live", ending: "Ending", processing: "Processing", removed: "Removed", done: "Done", cancelled: "Cancelled", timeout: "Timed out", discarded: "Discarded", interrupted: "Interrupted" };
const meetingState = (state) => String(state).startsWith("failed") ? "Failed" : MEETING_STATES[state] || `State: ${state}`;

const time = (value) => value ? `<time datetime="${e(value)}">${e(value.replace("T", " ").replace(/:\d\dZ$/, " UTC").replace(/Z$/, " UTC"))}</time>` : "";
const list = (items) => `<ul class="status-list">${items.join("")}</ul>`;

function skills(data) {
  const rows = data.items.map((item) => `<li class="status-row"><p class="status-name">${e(item.name)} <small>version ${e(item.version)}</small></p>
<p>${item.description ? e(item.description) : `<em>${e(item.description_note || "No description.")}</em>`}</p>
<p class="status-meta">Scope: ${e((item.scope || []).join(", ") || "unknown")}</p></li>`);
  const more = data.more ? `<p class="status-meta">${e(data.more)} more skills are not listed here.</p>` : "";
  return `${list(rows)}${more}<p class="status-meta">${e(data.notice || "")}</p>`;
}

function bridge(data) {
  const rows = data.installs.map((install) => `<li class="status-row"><p class="status-name">${e(install.label || install.platform)} <small>${e(install.platform)}</small></p>
<p>${install.enabled ? "On" : `Off. ${e(install.reason || "No reason was recorded.")}`}</p>
<p class="status-meta">${e(install.mapped_channels)} mapped channels · ${install.tested_at ? `tested ${time(install.tested_at)}` : "never tested"} · last seen: ${install.last_seen_at ? time(install.last_seen_at) : "no event yet"}</p></li>`);
  return `${list(rows)}<p class="status-meta">${e(data.last_seen_note)}</p>`;
}

function meetings(data) {
  const overview = data.overview ? `<p class="status-meta">Whole workspace: ${e(data.overview.meetings)} meetings, ${e(data.overview.connections_enabled)} of ${e(data.overview.connections)} connections on.</p>` : "";
  const rows = (data.meetings || []).map((meeting) => `<li class="status-row"><p class="status-name">${e(meetingState(meeting.state))} <small>${e(meeting.provider)}</small></p>
<p class="status-meta">${meeting.start_at ? `scheduled ${time(meeting.start_at)} · ` : ""}${meeting.started_at ? `started ${time(meeting.started_at)} · ` : ""}${meeting.ended_at ? `ended ${time(meeting.ended_at)}` : "not ended"} · ${e(meeting.counts?.notes ?? 0)} notes</p></li>`);
  return `${overview}${rows.length ? list(rows) : ""}`;
}

function money(row) {
  return row.length ? row.map((part) => `${e(part.display)} ${e(part.currency)}`).join(" · ") : "no cost reported";
}

function costs(data) {
  const projects = (data.by_project || []).map((row) => `<li class="status-row"><p class="status-name">${e(row.project || "No project")}</p><p class="status-meta">${e(row.runs)} runs · ${money(row.cost)}${row.unknown_cost_runs ? ` · ${e(row.unknown_cost_runs)} with unknown cost` : ""}</p></li>`);
  return `<p>${e(data.totals.runs)} runs · ${money(data.totals.cost)}${data.totals.unknown_cost_runs ? ` · ${e(data.totals.unknown_cost_runs)} with unknown cost` : ""}</p>
<p class="status-meta">${e(data.unknown_text)}</p>${projects.length ? `<h3>By project</h3>${list(projects)}` : ""}`;
}

const BODIES = { skills, bridge, meetings, costs };

// One section. An unknown or malformed answer is said so, never rendered as an empty panel.
export function sectionHTML(name, data) {
  const title = TITLES[name] || name;
  const state = data && KNOWN.has(data.state) ? data.state : "unknown";
  let reason = data && typeof data.reason === "string" ? data.reason : "";
  let body = "";
  if (state === "unknown") reason = "This page did not understand the answer for this section, so it shows nothing. Reload to try again.";
  else if (state === "ready") {
    try { body = BODIES[name](data); } catch { return sectionHTML(name, { state: "error", reason: "This section's answer was incomplete. Reload to try again." }); }
  } else if (!reason) reason = "The page was given no reason for this state, so it cannot explain it. Reload to try again.";
  const context = state === "ready" || state === "empty" ? contextHTML(name, data) : "";
  return `<section class="status-section" id="status-${e(name)}" data-state="${e(state)}"><h2>${e(title)}</h2>${state === "ready" ? "" : `<p class="status-state"><strong>${e(LABELS[state] || "Unknown")}.</strong> ${e(reason)}</p>`}${context}${body}</section>`;
}

function contextHTML(name, data) {
  if (name === "costs" && data.period) return `<p class="status-meta">Period: ${e(data.period)}.</p><p class="status-meta">Scope: ${e(data.scope_text || data.scope)}</p>`;
  if (name === "meetings" && data.state === "empty") return meetings(data);
  return "";
}

export function statusHTML(data) {
  return `<header class="status-head"><h1>Capabilities and status</h1>
<p>Read-only. Nothing on this page changes a setting. Times are UTC and show when something was last seen, not that a process is running now.</p></header>
${ORDER.map((name) => sectionHTML(name, data?.[name])).join("")}
<p class="status-meta">Viewing as ${e(data?.viewer?.role || "unknown role")} · read ${time(data?.generated_at) || "at an unknown time"}.</p>`;
}

// Only a 401 means signed out; a timeout or any other failure is a failed read, not an identity answer.
export function failureHTML(error) {
  if (error?.status === 401) return `<section class="status-section" data-state="signed-out"><h2>Sign in to see this</h2><p class="status-state">Your session has ended, or you are not signed in. <a href="/app/">Sign in</a>, then open Settings and this page again.</p></section>`;
  const why = error?.status === 403 ? "Your access does not include this page." : "The status could not be read just now. This does not mean you are signed out.";
  return `<section class="status-section" data-state="error"><h2>Could not load status</h2><p class="status-state">${e(why)}</p><button type="button" data-status-retry>Try again</button></section>`;
}

export function mountStatus(host, { read = (path, options) => api(path, options), timeoutMs = 15000 } = {}) {
  let request = 0;
  async function load() {
    const mine = ++request;
    host.setAttribute?.("aria-busy", "true");
    host.innerHTML = `<p role="status" class="status-state">Loading status…</p>`;
    let html;
    try { html = statusHTML(await read("/api/status", { timeoutMs })); } catch (error) { html = failureHTML(error); }
    if (mine !== request) return;
    host.innerHTML = html;
    host.setAttribute?.("aria-busy", "false");
  }
  host.addEventListener?.("click", (event) => { if (event.target.closest?.("[data-status-retry]")) void load(); });
  return load();
}
