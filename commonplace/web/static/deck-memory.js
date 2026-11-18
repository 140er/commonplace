// Memory presentation uses only readable source data; authority stays in memory.js.
import { escape as e, actor } from "./api.js";
import { wingLabel } from "./wing-label.js";
import { artHTML } from "./deck-art.js";

export function roomName(board) {
  if (!board) return "Saved memory";
  if (board.wing) return wingLabel(board.wing, board.name);
  if (board.name && board.name !== board.slug) return board.name;
  const words = String(board.name || board.slug || "Saved memory").replace(/[-_]+/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
// A result shows a window of its words around the first match; the stored text stays whole and "Show full text" opens it.
export const EXCERPT = 220;
export function excerpt(text, query = "", max = EXCERPT) {
  const value = String(text ?? "");
  if (value.length <= max) return { text: value, cut: false };
  const words = String(query).trim().split(/\s+/).filter(Boolean).map(word => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const found = words.length ? new RegExp(words.join("|"), "i").exec(value) : null;
  let start = found ? Math.max(0, found.index - Math.floor(max / 4)) : 0;
  if (start > 0) { const gap = value.slice(start).search(/\s/); if (gap >= 0 && gap < 20) start += gap + 1; }
  let end = Math.min(value.length, start + max);
  if (end < value.length) { const gap = value.lastIndexOf(" ", end); if (gap > start + max / 2) end = gap; }
  if (/[\uDC00-\uDFFF]/.test(value[start] || "")) start++;
  if (/[\uD800-\uDBFF]/.test(value[end - 1] || "")) end--;
  return { text: `${start > 0 ? "…" : ""}${value.slice(start, end).trim()}${end < value.length ? "…" : ""}`, cut: true };
}
export const resultKey = row => `${row.kind}:${row.id}`;
/** The words shown for a result and, when they were cut, the toggle that opens them whole. */
export function resultWords(row, index, { query = "", expanded = new Set(), highlight }) {
  const part = excerpt(row.text, query), open = expanded.has(resultKey(row));
  return {
    html: highlight(open || !part.cut ? row.text : part.text, query),
    toggle: part.cut ? `<button type="button" class="res-expand" data-memory-expand="${index}" aria-expanded="${open}">${open ? "Show less" : "Show full text"}</button>` : "",
  };
}
const REASONS = { timeout: "no answer in time", busy: "memory was busy", warming: "memory was starting up", denied: "no access", failed: "the server had a problem", skipped: "not reached before time ran out" };
/** The search warning: which sources did not answer and why, in plain words. */
export function sourcesWarning(errors = [], boards = []) {
  const seen = new Map();
  for (const error of errors) {
    const name = error.source === "note" ? "notes" : error.source === "project" ? `${roomName(boards.find(board => board.slug === error.board) || { slug: error.board })} chat and cards` : "other sources";
    const reason = REASONS[error.reason || (error.source === "skipped" ? "skipped" : "")];
    seen.set(`${name}|${reason || ""}`, reason ? `${name} (${reason})` : name);
  }
  const list = [...seen.values()], shown = list.slice(0, 4), more = list.length - shown.length;
  return `<p class="memory-warning" role="alert" data-memory-sources>Some sources could not be searched${shown.length ? `: ${e(shown.join("; "))}${more > 0 ? ` and ${more} more` : ""}` : ""}. <button class="btn" data-memory-action="retry">Try again</button> for all results.</p>`;
}
export function memoryRoom(row, boards = []) {
  return roomName(boards.find(board => board.slug === row.board));
}
export function savedBy(row, people = {}, viewer = "") {
  const ref = row.actor || row.author;
  const connection = ref && !/^(conn:|user:)/.test(ref) && row.kind === "note" ? `conn:${ref}` : ref;
  if (viewer && [ref, connection].includes(viewer)) return "you";
  const display = people[connection] || people[ref];
  if (display && !/^owner$/i.test(display)) return display;
  const resolved = actor(connection);
  if (connection && resolved !== String(connection).replace(/^(conn:|user:)/, "") && !/^owner$/i.test(resolved)) return resolved;
  // An unresolved connection is an internal identifier, not a display name.
  if (row.author && row.author !== ref && !/^(conn:|user:)/.test(row.author) && !/^owner$/i.test(row.author)) return row.author.replace(/ \(agent\)$/, "");
  return row.kind === "reply" ? "Quick reply" : "Unknown author";
}
function savedTime(row) {
  const at = new Date(row.at);
  return row.at && !Number.isNaN(at.getTime()) ? `${at.toLocaleDateString("en-GB", { day: "numeric", month: "short" })}, ${at.toTimeString().slice(0, 5)}` : "Time unavailable";
}
export function savedLine(row, state = {}) {
  return `Saved by ${savedBy(row, state.people, state.viewer)} · ${memoryRoom(row, state.boards)} · ${savedTime(row)}`;
}
export function savedMarkup(row, state = {}) {
  return `Saved by <b class="memory-saver">${e(savedBy(row, state.people, state.viewer))}</b> · ${e(memoryRoom(row, state.boards))} · ${e(savedTime(row))}`;
}
const countQuery = (filters = {}) => JSON.stringify([filters.query || "", filters.source || "", filters.from || "", filters.when || ""]);
export function rememberRoomCounts(state, results, filters) {
  const query = countQuery(filters);
  if (state.roomCountQuery !== query) { state.roomCounts = {}; state.roomCountQuery = query; }
  if (filters.project) state.roomCounts[filters.project] = results.length;
  else {
    state.roomCounts = Object.fromEntries(state.boards.map(board => [board.slug, 0]));
    for (const row of results) state.roomCounts[row.board] = (state.roomCounts[row.board] || 0) + 1;
    state.roomCounts[""] = results.length;
  }
}
export function roomChips(state) {
  const { boards = [], filters = {}, results = [] } = state;
  let counts = state.roomCountQuery === countQuery(filters) ? state.roomCounts : null;
  if (!counts && state.searched && !filters.project && !state.busy) {
    const snapshot = { boards }; rememberRoomCounts(snapshot, results, filters); counts = snapshot.roomCounts;
  }
  const count = value => Number.isSafeInteger(counts?.[value]) ? ` <span class="memory-room-count">${counts[value]}</span>` : "";
  return `<nav class="memory-rooms" aria-label="Rooms">${[["", "All"], ...boards.map(board => [board.slug, roomName(board)])].map(([value, label]) => `<button type="button" data-memory-room="${e(value)}" aria-pressed="${value === (filters.project || "")}">${e(label)}${count(value)}</button>`).join("")}</nav>`;
}
// Memory answered "starting" or "busy" with a wait hint; the page counts it down and retries by itself.
export const retryText = (retry) => `Memory is ${retry.warming ? "warming up" : "busy"}, retrying in ${retry.left} s`;
export const retryLine = (state) => state.retry ? `<p class="memory-retry" role="status" data-memory-retry>${e(retryText(state.retry))}</p>` : "";
const searchIcon = '<svg class="memory-search-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="7" cy="7" r="4.5"/><path d="m10.5 10.5 3 3"/></svg>';
const uploadIcon = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M8 10V2m-3 3 3-3 3 3M3 9v4h10V9"/></svg>';
export function renderDeckMemory(state, { highlighted }) {
  const { boards = [], results = [], filters = {}, busy, searched, selected } = state;
  const options = (rows, value) => rows.map(([id, label]) => `<option value="${e(id)}"${id === (value || "") ? " selected" : ""}>${e(label)}</option>`).join("");
  const authors = Object.entries(state.people || {}).map(([ref, label]) => [ref, ref === state.viewer ? "you" : /^owner$/i.test(label) ? "Unknown author" : label]);
  const status = busy ? "Searching…" : searched ? `${results.length} results` : "";
  return `<section class="memory-search deck-memory" aria-label="Memory">
    <header class="memory-heading"><div><h1>Memory</h1><p>Your exact words, and which bot saved them.</p></div>${artHTML("header")}</header>
    <form class="memory-toolbar" data-memory-search><div class="search-big" role="search">${searchIcon}<label class="sr" for="mq">Search saved memory</label><input id="mq" name="query" type="search" value="${e(filters.query)}" placeholder="Search saved memory"></div>
    ${state.restoredQuery ? '<p class="memory-restored" role="status">Kept from last time</p>' : ""}
    <div class="memory-filterbar"><div class="memory-chip-scroll">${roomChips(state)}<details class="memory-filters"${state.filtersOpen ? " open" : ""}><summary>More filters</summary><div class="filters"><label>Room<select name="project">${options([["", "All"], ...boards.map(board => [board.slug, roomName(board)])], filters.project)}</select></label><label>Source<select name="source">${options([["", "Any"], ["chat", "Chat"], ["reply", "Quick replies"], ["card", "Cards"], ["note", "Notes"]], filters.source)}</select></label><label>Saved by<select name="from">${options([["", "Anyone"], ...authors], filters.from)}</select></label><label>When<select name="when">${options([["", "Any time"], ["1", "Last 24 hours"], ["7", "Last 7 days"], ["30", "Last 30 days"]], filters.when)}</select></label></div></details></div><span class="memory-status" role="status">${e(status)}</span><button type="button" class="btn memory-map-launch" data-memory-action="map">Map</button><a class="btn memory-history-launch" href="/app/history.html">Saved threads</a></div></form>
    ${state.truncated ? '<p class="memory-warning">More matches are available; narrow your search.</p>' : ""}
    ${state.error ? `<div class="memory-empty" role="alert"><h2>Search couldn't run</h2><p>${e(state.error)}</p><button class="btn" data-memory-action="retry">Try again</button></div>` : ""}
    ${state.errors?.length && !state.error && !busy ? sourcesWarning(state.errors, boards) : ""}
    ${busy ? retryLine(state) : ""}
    <div class="memory-results list" aria-busy="${Boolean(busy)}">${busy && !results.length && !state.retry ? '<div class="memory-loading" role="status"><span class="memory-spinner" aria-hidden="true"></span> Searching saved memory… this can take a few seconds.</div>' : ""}${results.map((row, i) => { const words = resultWords(row, i, { query: busy ? state.completedQuery : filters.query, expanded: state.expanded, highlight: highlighted }); return `<button class="res${row === selected ? " is-selected" : ""}" data-memory-result="${i}"${row === selected ? ' aria-current="true"' : ""}><span class="ex">${words.html}</span><span class="src">${savedMarkup(row, state)}</span></button>${words.toggle}`; }).join("")}${!busy && state.moreNotes ? '<button class="btn memory-more" data-memory-action="more">Show more</button>' : ""}${!busy && !state.error && !results.length ? `<div class="memory-empty" role="status">${searched ? artHTML("search") : ""}<h2>${searched ? "No exact words match" : "Search your memory"}</h2><p>${searched ? "Try fewer or different words, or search every room." : "Exact words from chats, cards and notes. Never summarised."}</p>${searched ? '<button class="btn" data-memory-action="clear">Search all rooms</button>' : ""}</div>` : ""}</div>
    <button class="btn memory-import-launch" data-memory-action="import">${uploadIcon}<span>Import memory files</span></button></section>`;
}
export function memoryDetails(row) {
  return `<details class="memory-source-details"><summary>Source details</summary><dl><dt>Drawer ID</dt><dd><code>${e(row.id)}</code></dd></dl></details>`;
}
export function importConsent(state) {
  return `<label class="memory-import-consent"><input type="checkbox" data-import-consent${state.consent ? " checked" : ""}> <span>I own this export and agree to import it.</span></label>`;
}
export function queryHistory(me, storage) {
  const key = me?.actor && me?.workspace ? `cmmn.memory.query:${JSON.stringify([me.workspace, me.actor])}` : null;
  return {
    read() { try { return key ? storage?.getItem(key) || "" : ""; } catch { return ""; } },
    save(query) { try { if (key) { if (query) storage?.setItem(key, query); else storage?.removeItem(key); } } catch { /* Search works without storage. */ } },
  };
}
export function memoryStyles(doc) {
  if (globalThis.commonplaceAssetManifest?.styles.includes('deck-memory.css')) return;
  if (!doc?.head || doc.querySelector('link[data-memory-style]')) return;
  const link = doc.createElement('link');
  link.rel = 'stylesheet'; link.href = new URL('./deck-memory.css', import.meta.url).href;
  link.dataset.memoryStyle = ''; doc.head.append(link);
}
