// Memory presentation uses only readable source data; authority stays in memory.js.
import { escape as e, actor } from "./api.js";

export function roomName(board) {
  if (!board) return "Saved memory";
  if (board.name && board.name !== board.slug) return board.name;
  const words = String(board.name || board.slug || "Saved memory").replace(/[-_]+/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
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
const searchIcon = '<svg class="memory-search-icon" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><circle cx="7" cy="7" r="4.5"/><path d="m10.5 10.5 3 3"/></svg>';
const uploadIcon = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M8 10V2m-3 3 3-3 3 3M3 9v4h10V9"/></svg>';
export function renderDeckMemory(state, { highlighted }) {
  const { boards = [], results = [], filters = {}, busy, searched, selected } = state;
  const options = (rows, value) => rows.map(([id, label]) => `<option value="${e(id)}"${id === (value || "") ? " selected" : ""}>${e(label)}</option>`).join("");
  const authors = Object.entries(state.people || {}).map(([ref, label]) => [ref, ref === state.viewer ? "you" : /^owner$/i.test(label) ? "Unknown author" : label]);
  const status = busy ? "Searching…" : searched ? `${results.length} results` : "";
  return `<section class="memory-search deck-memory" aria-label="Memory">
    <header class="memory-heading"><div><h1>Memory</h1><p>Your exact words, and which bot saved them.</p></div></header>
    <form class="memory-toolbar" data-memory-search><div class="search-big" role="search">${searchIcon}<label class="sr" for="mq">Search saved memory</label><input id="mq" name="query" type="search" value="${e(filters.query)}" placeholder="Search saved memory"></div>
    ${state.restoredQuery ? '<p class="memory-restored" role="status">Kept from last time</p>' : ""}
    <div class="memory-filterbar"><div class="memory-chip-scroll">${roomChips(state)}<details class="memory-filters"${state.filtersOpen ? " open" : ""}><summary>More filters</summary><div class="filters"><label>Room<select name="project">${options([["", "All"], ...boards.map(board => [board.slug, roomName(board)])], filters.project)}</select></label><label>Source<select name="source">${options([["", "Any"], ["chat", "Chat"], ["reply", "Quick replies"], ["card", "Cards"], ["note", "Notes"]], filters.source)}</select></label><label>Saved by<select name="from">${options([["", "Anyone"], ...authors], filters.from)}</select></label><label>When<select name="when">${options([["", "Any time"], ["1", "Last 24 hours"], ["7", "Last 7 days"], ["30", "Last 30 days"]], filters.when)}</select></label></div></details></div><span class="memory-status" role="status">${e(status)}</span><button type="button" class="btn memory-map-launch" data-memory-action="map">Map</button></div></form>
    ${state.truncated ? '<p class="memory-warning">More matches are available; narrow your search.</p>' : ""}
    ${state.error ? `<div class="memory-empty" role="alert"><h2>Search couldn't run</h2><p>${e(state.error)}</p><button class="btn" data-memory-action="retry">Try again</button></div>` : ""}
    ${state.errors?.length ? '<p class="memory-warning" role="alert">Some sources could not be searched. Try again for all results.</p>' : ""}
    <div class="memory-results list" aria-busy="${Boolean(busy)}">${results.map((row, i) => `<button class="res${row === selected ? " is-selected" : ""}" data-memory-result="${i}"${row === selected ? ' aria-current="true"' : ""}><span class="ex">${highlighted(row.text, busy ? state.completedQuery : filters.query)}</span><span class="src">${savedMarkup(row, state)}</span></button>`).join("")}${!busy && !state.error && !results.length ? `<div class="memory-empty" role="status"><h2>${searched ? "No exact words match" : "Search your memory"}</h2><p>${searched ? "Try fewer or different words, or search every room." : "Exact words from chats, cards and notes. Never summarised."}</p>${searched ? '<button class="btn" data-memory-action="clear">Search all rooms</button>' : ""}</div>` : ""}</div>
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
  if (!doc?.head || doc.querySelector('link[data-memory-style]')) return;
  const link = doc.createElement('link');
  link.rel = 'stylesheet'; link.href = new URL('./deck-memory.css', import.meta.url).href;
  link.dataset.memoryStyle = ''; doc.head.append(link);
}
