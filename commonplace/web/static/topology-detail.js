/* The Compute map's node detail panel: four read-only tabs for the device selected on the canvas.

   Physical  hardware, OS and resources, and when each was last observed
   Config    how to connect, as copyable text and as the answer topology_route gives an agent
   Agents    what runs on the device now: connections, runs and the coordinator's checkpoint
   Sites     the website routes that start at, end at or pass through the device

   Every value says where it came from and how fresh it is; a stale value is marked, a value
   nobody recorded says so. Nothing here is a field to edit: changes go through agent proposals.
   The pieces that know a read's shape (detailModel, the *HTML functions) take plain data and
   return strings, so they run without a DOM; createNodePanel is the screen.

   Coordination with the canvas is two events and one select: it listens to `topology:select`
   (detail.key) on the map's root, and "Show on map" sets the canvas's own route select. */
import { escape as esc, elapsed, safeLink } from "./api.js";
import { KINDS, kindOf } from "./topology-icons.js";

export const NODE_PATH = "/api/topology/node";
export const ROUTE_PATH = "/api/topology/route";
export const TABS = [
  { id: "physical", label: "Physical" },
  { id: "config", label: "Config" },
  { id: "agents", label: "Agents" },
  { id: "sites", label: "Sites" },
];
export const DETAIL_COPY = {
  loading: "Loading…",
  failed: "The details could not be loaded.",
  refused: "You may not read the details of this device.",
  notRecorded: "Nothing is recorded for this device yet. An agent can add it.",
  readOnly: "Read-only. Changes go through agent proposals.",
  noPhysical: "No hardware, OS or resources are recorded for this device.",
  noConfig: "No way to connect to this device is recorded.",
  routeOff: "The route answer is not available right now.",
  routeRefused: "You may not read the route answer.",
  notRunner: "This device is not a runner machine, so no agents are recorded on it.",
  machineGone: "The runner machine recorded for this device is not enrolled in this workspace.",
  noAgents: "No agents are working on this device right now.",
  noConnections: "Connections are shown to workspace owners.",
  noSites: "No website is routed through or hosted on this device.",
  copied: "Copied.",
  copyFailed: "Copying is not available here. Select the text and copy it.",
};
const SOURCE_KIND = { agent: "Agent", runner: "Runner", owner: "Owner" };
const STATUS_WORDS = { up: "up", degraded: "degraded", down: "down", unknown: "unknown" };
const ROLE_WORDS = { site: "This website", hosted: "Hosted on this device", through: "Routed through this device" };
const METHODS = { direct: "direct", jump_host: "jump host", tailscale: "Tailscale", relay: "relay", dns: "DNS", proxy: "proxy", reverse_proxy: "reverse proxy", hosts: "hosts" };
const LEASE_ROLES = { machine: "Poll connection", worker: "Worker connection" };
const RESOURCES = [["cpu", "CPU", ""], ["gpu", "GPU", ""], ["cores", "Cores", ""], ["memory_gb", "Memory", " GB"], ["storage_gb", "Storage", " GB"]];

const method = (value) => METHODS[value] || String(value || "").replace(/_/g, " ");
const list = (value) => (Array.isArray(value) ? value.join(", ") : String(value ?? ""));
const ago = (at) => { const value = at ? elapsed(at) : "—"; return value === "—" ? "" : value === "now" ? "just now" : `${value} ago`; };

/** "agent conn_x" for one observation; "+2 more" when others saw the same thing. */
export function sourceWords(sources) {
  const [first, ...rest] = sources || [];
  if (!first) return "source unknown";
  return `${SOURCE_KIND[first.kind] || first.kind} ${first.id}${rest.length ? ` +${rest.length} more` : ""}`;
}

/** Freshness as text: "observed 3m ago", or "stale: last observed 40d ago". */
export function freshWords(at, stale) {
  const when = ago(at);
  return stale ? `stale${when ? `: last observed ${when}` : ""}` : when ? `observed ${when}` : "observed time unknown";
}

function meta(entry, { evidence = true, fresh = true } = {}) {
  const line = entry.sources?.[0]?.evidence;
  // A caller that already says "Last observed" in a header leaves the age out of each row, unless it is stale.
  const parts = [sourceWords(entry.sources), ...(fresh || entry.stale ? [freshWords(entry.observed_at, entry.stale)] : [])];
  if (evidence && line) parts.push(`“${line}”`);
  return `<span class="td-meta${entry.stale ? " stale" : ""}" title="${esc(entry.observed_at || "")}">${parts.map(esc).join(" · ")}</span>`;
}

const row = (label, value, entry, name, fresh = true) => `<div class="td-row${entry?.stale ? " stale" : ""}" data-field="${esc(name)}"><dt>${esc(label)}</dt><dd><span class="td-val">${esc(value)}</span>${entry ? meta(entry, { fresh }) : ""}${entry?.stale ? '<span class="td-flag">stale</span>' : ""}${entry?.contenders ? `<span class="td-flag">${entry.contenders} disagreeing</span>` : ""}</dd></div>`;

/** The read's node part as `{ found, fields }`; any shape problem reads as nothing recorded. */
export function detailModel(payload) {
  const node = payload?.node;
  return {
    found: !!node?.found,
    stale: !!node?.stale,
    conflicted: !!node?.conflicted,
    fields: node?.found && node.fields && typeof node.fields === "object" ? node.fields : {},
    agents: payload?.agents && typeof payload.agents === "object" ? payload.agents : null,
    sites: Array.isArray(payload?.sites) ? payload.sites : [],
    asOf: typeof payload?.as_of === "string" ? payload.as_of : "",
  };
}

const empty = (text, extra = "") => `<p class="td-empty" data-empty>${esc(text)}</p>${extra}`;

export function physicalHTML(detail, { compact = false } = {}) {
  const { fields } = detail;
  const rows = [];
  if (fields.hardware) rows.push(row("Hardware", fields.hardware.value, fields.hardware, "hardware", !compact));
  if (fields.os) rows.push(row("OS", fields.os.value, fields.os, "os", !compact));
  if (fields.resources?.value && typeof fields.resources.value === "object") {
    for (const [key, label, unit] of RESOURCES) if (key in fields.resources.value) rows.push(row(label, `${fields.resources.value[key]}${unit}`, fields.resources, `resources.${key}`, !compact));
  }
  if (!rows.length) return empty(detail.found ? DETAIL_COPY.noPhysical : DETAIL_COPY.notRecorded);
  const seen = ["hardware", "os", "resources"].map((name) => fields[name]).filter(Boolean).map((entry) => entry.observed_at).sort().pop();
  return `<p class="td-last" data-last-observed>Last observed ${esc(ago(seen) || "at an unknown time")}</p><dl class="td-list">${rows.join("")}</dl>`;
}

/** Facts that say how to reach a node: its own fields and the links that end at it. */
export function configLines(detail, links, key) {
  const { fields } = detail;
  const lines = [];
  const add = (label, name, value = fields[name]?.value) => { if (value !== undefined && value !== "") lines.push({ label, name, value: list(value), entry: fields[name] }); };
  add("Hostname", "hostname"); add("Tailscale name", "tailscale_name"); add("ssh alias", "aliases");
  add("Runner id", "runner_id"); add("Wings", "wings");
  const inbound = (links || []).filter((link) => link.to === key && link.method !== "hosts");
  return { lines, inbound };
}

const hopWords = (hop) => {
  const ends = `${hop.from?.name || hop.from?.key || hop.from} → ${hop.to?.name || hop.to?.key || hop.to}`;
  const extra = [hop.alias && `as ${hop.alias}`, hop.via && `via ${hop.via}`, hop.port && `port ${hop.port}`].filter(Boolean).join(", ");
  const state = [STATUS_WORDS[hop.status] || "unknown", !hop.known && "not recorded", hop.known && hop.stale && "stale"].filter(Boolean).join(", ");
  return `${ends}: ${method(hop.method)}${extra ? ` (${extra})` : ""}, ${state}`;
};

/** The answer topology_route gave, as the text an agent would read. */
export function routeText(answer) {
  if (!answer) return "";
  const routes = answer.route ? [answer.route] : answer.routes || [];
  return [answer.answer, ...routes.flatMap((route, index) => [routes.length > 1 ? `Way ${index + 1}${route.status ? ` (${route.status})` : ""}:` : "", ...(route.hops || []).map((hop) => `  ${hopWords(hop)}`)]).filter(Boolean)].filter(Boolean).join("\n");
}

function routeHTML(answer, failure) {
  if (failure) return `<section class="td-route" data-route><h3>As topology_route answers</h3>${empty(failure)}</section>`;
  if (!answer) return `<section class="td-route" data-route><h3>As topology_route answers</h3>${empty(DETAIL_COPY.loading)}</section>`;
  const text = routeText(answer);
  const fresh = answer.as_of ? `<span class="td-meta">answer as of ${esc(ago(answer.as_of) || answer.as_of)}</span>` : "";
  const routes = answer.route ? [answer.route] : answer.routes || [];
  const hops = routes.map((route, index) => `<ol class="td-hops" aria-label="${routes.length > 1 ? `Way ${index + 1}` : "Route"}">${(route.hops || []).map((hop) => `<li class="${hop.known === false || hop.stale ? "stale" : ""}" data-hop="${esc(hop.link)}">${esc(hopWords(hop))}${hop.observed_at ? ` <span class="td-meta">${esc(freshWords(hop.observed_at, hop.stale))}</span>` : ""}</li>`).join("")}</ol>`).join("");
  return `<section class="td-route" data-route><h3>As topology_route answers</h3><p class="td-answer" data-answer>${esc(answer.answer || "")}</p>${fresh}${hops}<button type="button" class="btn sm" data-td="copy" data-copy="route">Copy answer</button><pre class="td-copy" data-copy-text="route" hidden>${esc(text)}</pre></section>`;
}

export function configHTML(detail, { links = [], key = "", answer = null, routeFailure = "" } = {}) {
  const { lines, inbound } = configLines(detail, links, key);
  const rows = lines.map((line) => row(line.label, line.value, line.entry, line.name));
  const via = inbound.map((link) => `<div class="td-row${link.stale ? " stale" : ""}" data-link="${esc(link.id)}"><dt>${esc(method(link.method))}</dt><dd><span class="td-val">from ${esc(link.from)}${link.via ? ` via ${esc(link.via)}` : ""}</span><span class="td-meta${link.stale ? " stale" : ""}">${esc(`topology link · ${link.stale ? "stale" : "current"}`)}</span></dd></div>`);
  const text = [...lines.map((line) => `${line.label}: ${line.value}`), ...inbound.map((link) => `${method(link.method)}: from ${link.from}${link.via ? ` via ${link.via}` : ""}`)].join("\n");
  const facts = rows.length || via.length
    ? `<dl class="td-list">${rows.join("")}${via.join("")}</dl><button type="button" class="btn sm" data-td="copy" data-copy="facts">Copy</button><pre class="td-copy" data-copy-text="facts" hidden>${esc(text)}</pre>`
    : empty(detail.found ? DETAIL_COPY.noConfig : DETAIL_COPY.notRecorded);
  return `<section data-facts><h3>How to connect</h3>${facts}</section>${routeHTML(answer, routeFailure)}`;
}

const cardLink = (board, number) => `<a href="#/card/${esc(number)}">${esc(board)} #${esc(number)}</a>`;
const logLink = (ref) => { if (typeof ref !== "string" || !ref) return ""; const href = safeLink(ref); return ` · log: ${href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(ref)}</a>` : `<code>${esc(ref)}</code>`}`; };

export function agentsHTML(detail) {
  const agents = detail.agents;
  if (!agents) return empty(DETAIL_COPY.failed);
  if (!agents.applicable) return empty(agents.reason === "machine_not_found" ? DETAIL_COPY.machineGone : DETAIL_COPY.notRunner);
  const machine = agents.machine || {};
  const head = `<p class="td-last" data-machine>${esc(machine.label || machine.id)} · ${machine.revoked ? "revoked" : machine.enabled ? "enabled" : "switched off"}<span class="td-meta"> · source: runner registry · as of ${esc(ago(detail.asOf) || "now")}</span></p>`;
  const connections = agents.connections === null || agents.connections === undefined
    ? `<p class="td-empty" data-connections>${esc(DETAIL_COPY.noConnections)}</p>`
    : `<ul class="td-items" data-connections>${agents.connections.map((item) => `<li>${esc(LEASE_ROLES[item.kind] || item.kind)}: ${esc(item.name)}${item.revoked ? " (revoked)" : ""} <span class="td-meta">${esc(item.last_used_at ? `last used ${ago(item.last_used_at)}` : "never used")} · source: connection</span></li>`).join("") || `<li class="td-empty">No connections.</li>`}</ul>`;
  const runs = (agents.runs || []).map((run) => `<li data-run="${esc(run.job)}">${cardLink(run.board, run.card)} · ${esc(run.state)}${run.thread_id ? ` · thread ${esc(run.thread_id)}` : ""}<span class="td-meta"> · updated ${esc(ago(run.updated_at))} · source: start job</span>${logLink(run.log_ref)}</li>`);
  const coordinators = (agents.coordinators || []).map((item) => {
    const lease = item.lease;
    const holder = lease ? `${lease.live ? "lease held by" : "lease ended, last held by"} ${esc(lease.holder)}` : "no lease";
    const threads = (item.threads || []).map((thread) => `<li data-thread="${esc(thread.thread_id)}">${cardLink(item.board, thread.card)} · thread ${esc(thread.thread_id)} · ${esc(thread.state)}<span class="td-meta"> · updated ${esc(ago(thread.updated_at))} · source: checkpoint</span></li>`).join("");
    return `<li data-coordinator="${esc(item.board)}"><a href="#/board/${encodeURIComponent(item.board)}">${esc(item.board)}</a> · ${holder} · ${esc(item.open_steps)} open step${item.open_steps === 1 ? "" : "s"}<span class="td-meta"> · source: checkpoint${lease ? ` · expires ${esc(ago(lease.expires_at) || lease.expires_at)}` : ""}</span>${threads ? `<ul>${threads}</ul>` : ""}</li>`;
  });
  const none = !runs.length && !coordinators.length;
  return `${head}<h3>Connections</h3>${connections}<h3>Runs</h3>${runs.length ? `<ul class="td-items" data-runs>${runs.join("")}</ul>` : empty("No runs are queued or running on this machine.")}<h3>Coordinators</h3>${coordinators.length ? `<ul class="td-items" data-coordinators>${coordinators.join("")}</ul>` : empty("No board's coordinator runs on this machine.")}${none ? `<p class="td-empty" data-empty>${esc(DETAIL_COPY.noAgents)}</p>` : ""}`;
}

export function sitesHTML(detail, names = new Map()) {
  if (!detail.sites.length) return empty(detail.found ? DETAIL_COPY.noSites : DETAIL_COPY.notRecorded);
  const items = detail.sites.map((site) => {
    const where = site.at.map((hop) => `hop ${hop.index + 1} of ${site.hops}: ${names.get(hop.from) || hop.from} → ${names.get(hop.to) || hop.to} (${method(hop.method)}, ${STATUS_WORDS[hop.status] || "unknown"}${hop.known ? "" : ", not recorded"}${hop.known && hop.stale ? ", stale" : ""})`);
    return `<li class="td-site${site.stale ? " stale" : ""}" data-site="${esc(site.site)}"><div><strong>${esc(site.site)}</strong> · ${esc(ROLE_WORDS[site.role] || site.role)}${site.stale ? '<span class="td-flag">stale</span>' : ""}</div><ul class="td-where">${where.map((text) => `<li>${esc(text)}</li>`).join("")}</ul>${meta(site, { evidence: false })}<button type="button" class="btn sm" data-td="route" data-site="${esc(site.site)}">Show route on map</button></li>`;
  });
  return `<ul class="td-items" data-sites>${items.join("")}</ul>`;
}

export function panelHTML({ key, node, tab, detail, links, answer, routeFailure, names, loading, failure, own = null, compact = false }) {
  const kind = KINDS[node ? node.kind : kindOf()] || KINDS[kindOf()];
  const title = node?.name || key;
  const tabs = TABS.map((item) => `<button type="button" role="tab" id="td-tab-${item.id}" aria-controls="td-panel" aria-selected="${item.id === tab}" tabindex="${item.id === tab ? 0 : -1}" data-td-tab="${item.id}">${item.label}</button>`).join("");
  const body = own?.[tab] !== undefined ? own[tab] : failure ? empty(failure) : loading ? empty(DETAIL_COPY.loading)
    : tab === "physical" ? physicalHTML(detail, { compact })
      : tab === "config" ? configHTML(detail, { links, key, answer, routeFailure })
        : tab === "agents" ? agentsHTML(detail)
          : sitesHTML(detail, names);
  const flags = [node?.missing && "not recorded", detail?.stale && "stale", detail?.conflicted && "has a conflict"].filter(Boolean).join(" · ");
  return `<header class="td-head"><div><h2 class="td-title">${esc(title)}</h2><p class="td-sub">${esc(kind.label)}${node?.role ? ` · ${esc(node.role)}` : ""}${flags ? ` · ${esc(flags)}` : ""}</p></div><button type="button" class="iconbtn bordered" data-td="close" aria-label="Close details">${compact ? '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8"/></svg>' : "×"}</button></header><div class="td-tabs" role="tablist" aria-label="Device details">${tabs}</div><div class="td-body" id="td-panel" role="tabpanel" aria-labelledby="td-tab-${tab}" tabindex="0">${body}</div><p class="td-foot">${esc(DETAIL_COPY.readOnly)}</p>`;
}

/** Mounts the panel in `section` (the map's root). `model()` is the canvas's current drawable
 * model; `api` reads JSON. It reads the node on selection and again every `interval` ms while
 * the device stays selected and the page is visible, and removes itself with the markup. */
export function createNodePanel({ section, api, model, interval = 5000, own = null, compact = false, render = panelHTML, copy = (text) => globalThis.navigator.clipboard.writeText(text) }) {
  const doc = section.ownerDocument;
  // The panel's element exists from the first selection on, so a map nobody selects on pays nothing.
  let panel = null;
  let key = null, tab = "physical", detail = null, answer = null, routeFailure = "", failure = "", loading = false, timer = null, run = 0, shown = "";

  const node = () => model().nodes.find((item) => item.key === key) || null;
  function paint() {
    if (!key) { if (panel) { panel.hidden = true; panel.innerHTML = ""; } shown = ""; return; }
    panel ??= mountPanel();
    const current = model();
    const html = render({ key, node: node(), tab, detail: detail || detailModel(null), links: current.links.filter((link) => !link.missing), answer, routeFailure, names: new Map(current.nodes.map((item) => [item.key, item.name])), loading, failure, own: own?.(key) || null, compact });
    if (html === shown) return;
    const focused = panel.contains(doc.activeElement) ? doc.activeElement.getAttribute("data-td-tab") || doc.activeElement.getAttribute("data-td") : null;
    const scroll = panel.querySelector(".td-body")?.scrollTop || 0;
    panel.hidden = false; panel.innerHTML = html; shown = html;
    const body = panel.querySelector(".td-body"); if (body) body.scrollTop = scroll;
    if (focused) (panel.querySelector(`[data-td-tab="${focused}"]`) || panel.querySelector(`[data-td="${focused}"]`))?.focus({ preventScroll: true });
  }
  async function load() {
    const mine = run, target = key;
    if (!target) return;
    // A device the host describes itself has nothing to read from the topology.
    if (own?.(target)) { loading = false; paint(); return; }
    const [read, route] = await Promise.allSettled([api(`${NODE_PATH}?key=${encodeURIComponent(target)}`), api(`${ROUTE_PATH}?target=${encodeURIComponent(target)}`)]);
    if (mine !== run || target !== key) return;
    loading = false;
    if (read.status === "fulfilled") { detail = detailModel(read.value); failure = ""; }
    else failure = read.reason?.status === 403 ? DETAIL_COPY.refused : detail ? "" : DETAIL_COPY.failed;
    if (route.status === "fulfilled") { answer = route.value; routeFailure = ""; }
    else if (!answer) routeFailure = route.reason?.status === 403 ? DETAIL_COPY.routeRefused : DETAIL_COPY.routeOff;
    paint();
  }
  async function tick() {
    timer = null;
    const mine = run;
    if (!section.isConnected) { stop(); return; }
    if (key && !doc.hidden) await load();
    if (mine === run && key && interval > 0) timer = setTimeout(tick, interval);
  }
  function stop() { run += 1; clearTimeout(timer); timer = null; controller.abort(); panel?.remove(); }
  function select(next) {
    run += 1; clearTimeout(timer); timer = null;
    key = typeof next === "string" && next ? next : null;
    detail = null; answer = null; routeFailure = ""; failure = ""; loading = !!key; shown = "";
    paint();
    if (key) timer = setTimeout(tick, 0);
  }
  // Closing deselects the device on the canvas the way a second Enter on it would.
  function close() {
    const el = key && [...section.querySelectorAll("[data-key]")].find((item) => item.dataset.key === key);
    if (el) el.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    else select(null);
  }
  function showRoute(site) {
    const picker = section.querySelector('[data-topo="route"]');
    if (!picker) return;
    picker.value = site;
    picker.dispatchEvent(new Event("change", { bubbles: true }));
  }
  async function copyText(name, button) {
    const source = panel.querySelector(`[data-copy-text="${name}"]`);
    if (!source) return;
    let note = DETAIL_COPY.copied;
    try { await copy(source.textContent); } catch { note = DETAIL_COPY.copyFailed; source.hidden = false; }
    let status = panel.querySelector("[data-td-status]");
    if (!status) { status = doc.createElement("p"); status.setAttribute("data-td-status", ""); status.setAttribute("role", "status"); status.className = "td-meta"; button.after(status); }
    status.textContent = note;
  }
  function mountPanel() {
    const el = doc.createElement("aside");
    el.className = "topo-detail";
    el.setAttribute("aria-label", "Device details");
    el.setAttribute("data-topo-detail", "");
    el.hidden = true;
    section.append(el);
    const listen = (kind, handler) => el.addEventListener(kind, handler, { signal: controller.signal });
    listen("click", (event) => {
      const target = event.target.closest?.("[data-td], [data-td-tab]");
      if (!target) return;
      if (target.dataset.tdTab) { tab = target.dataset.tdTab; paint(); return; }
      const action = target.dataset.td;
      if (action === "close") close();
      else if (action === "route") showRoute(target.dataset.site);
      else if (action === "copy") void copyText(target.dataset.copy, target);
    });
    listen("keydown", (event) => {
      if (event.key === "Escape") { event.stopPropagation(); close(); return; }
      const current = event.target.closest?.("[data-td-tab]");
      if (!current || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const at = TABS.findIndex((item) => item.id === current.dataset.tdTab);
      const next = event.key === "Home" ? 0 : event.key === "End" ? TABS.length - 1 : (at + (event.key === "ArrowRight" ? 1 : -1) + TABS.length) % TABS.length;
      tab = TABS[next].id; paint(); panel.querySelector(`[data-td-tab="${tab}"]`)?.focus();
    });
    return el;
  }
  const controller = new AbortController();
  const on = (kind, handler) => section.addEventListener(kind, handler, { signal: controller.signal });
  on("topology:select", (event) => select(event.detail?.key));
  return { select, stop, refresh: () => (key && section.isConnected ? load() : undefined), get key() { return key; }, get tab() { return tab; } };
}
