// Memory presentation uses only readable source data; authority stays in memory.js.
import { escape as e, actor } from "./api.js";

export function memoryRoom(row, boards = []) {
  return boards.find(board => board.slug === row.board)?.name || "Saved memory";
}
export function savedBy(row, people = {}) {
  const ref = row.actor || row.author;
  const connection = ref && !/^(conn:|user:)/.test(ref) && row.kind === "note" ? `conn:${ref}` : ref;
  const display = people[connection] || people[ref];
  if (display) return display;
  const resolved = actor(connection);
  if (connection && resolved !== String(connection).replace(/^(conn:|user:)/, "")) return resolved;
  // An unresolved connection is an internal identifier, not a display name.
  if (row.author && row.author !== ref && !/^(conn:|user:)/.test(row.author)) return row.author.replace(/ \(agent\)$/, "");
  return row.kind === "reply" ? "Quick reply" : "Unknown author";
}
export function savedLine(row, state = {}) {
  const at = new Date(row.at);
  const time = row.at && !Number.isNaN(at.getTime()) ? `${at.toLocaleDateString("en-GB", { day: "numeric", month: "short" })}, ${at.toTimeString().slice(0, 5)}` : "Time unavailable";
  return `Saved by ${savedBy(row, state.people)} · ${memoryRoom(row, state.boards)} · ${time}`;
}
export function roomChips({ boards = [], filters = {} }) {
  return `<nav class="memory-rooms" aria-label="Rooms">${[["", "All"], ...boards.map(board => [board.slug, board.name || "Saved memory"])].map(([value, label]) => `<button type="button" data-memory-room="${e(value)}" aria-pressed="${value === (filters.project || "")}">${e(label)}</button>`).join("")}</nav>`;
}
export function renderDeckMemory(state, { highlighted }) {
  const { boards = [], results = [], filters = {}, busy, searched, selected } = state;
  const options = (rows, value) => rows.map(([id, label]) => `<option value="${e(id)}"${id === (value || "") ? " selected" : ""}>${e(label)}</option>`).join("");
  const authors = Object.entries(state.people || {});
  const status = busy ? "Searching…" : searched ? `${results.length} results${state.truncated ? " · more matches available; narrow your search" : ""}` : "";
  return `<section class="memory-search deck-memory" aria-label="Memory">
    <header class="memory-heading"><div><h1>Memory</h1><p>Your exact words, and which bot saved them.</p></div><button class="btn" data-memory-action="import">Import memory files</button></header>
    <form class="memory-toolbar" data-memory-search><div class="search-big" role="search"><label class="sr" for="mq">Search saved memory</label><input id="mq" name="query" type="search" value="${e(filters.query)}" placeholder="Search saved memory"><button class="btn ghost" type="submit" aria-label="Search saved memory">Search</button></div>
    ${state.restoredQuery ? '<p class="memory-restored" role="status">Kept from last time</p>' : ""}
    ${roomChips(state)}<details class="memory-filters"><summary>More filters</summary><div class="filters"><label>Room<select name="project">${options([["", "All"], ...boards.map(board => [board.slug, board.name || "Saved memory"])], filters.project)}</select></label><label>Source<select name="source">${options([["", "Any"], ["chat", "Chat"], ["reply", "Quick replies"], ["card", "Cards"], ["note", "Notes"]], filters.source)}</select></label><label>Saved by<select name="from">${options([["", "Anyone"], ...authors], filters.from)}</select></label><label>When<select name="when">${options([["", "Any time"], ["1", "Last 24 hours"], ["7", "Last 7 days"], ["30", "Last 30 days"]], filters.when)}</select></label></div></details><span class="memory-status" role="status">${e(status)}</span></form>
    ${state.error ? `<div class="memory-empty" role="alert"><h2>Search couldn't run</h2><p>${e(state.error)}</p><button class="btn" data-memory-action="retry">Try again</button></div>` : ""}
    ${state.errors?.length ? '<p class="memory-warning" role="alert">Some sources could not be searched. Try again for all results.</p>' : ""}
    <div class="memory-results list" aria-busy="${Boolean(busy)}">${results.map((row, i) => `<button class="res${row === selected ? " is-selected" : ""}" data-memory-result="${i}"${row === selected ? ' aria-current="true"' : ""}><span class="ex">${highlighted(row.text, busy ? state.completedQuery : filters.query)}</span><span class="src">${e(savedLine(row, state))}</span></button>`).join("")}${!busy && !state.error && !results.length ? `<div class="memory-empty" role="status"><h2>${searched ? "No exact words match" : "Search your memory"}</h2><p>${searched ? "Try fewer or different words, or search every room." : "Exact words from chats, cards and notes. Never summarised."}</p>${searched ? '<button class="btn" data-memory-action="clear">Search all rooms</button>' : ""}</div>` : ""}</div></section>`;
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
