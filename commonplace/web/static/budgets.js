// Spend and budgets page. Reads GET /api/costs and GET /api/settings/budgets; the workspace owner
// writes with PUT/DELETE /api/settings/budgets after an explicit confirm. Members only read spend.
import { api, escape as e } from "./api.js";
import { HARD_STOP, badgeText, formatMicros, otherCurrencyText, parseAmount, runningText, scopeLabel, unknownCostText, windowText } from "./budget-format.js";

const SHOWN = [["workspace", "Workspace"], ["agent", "Agent connections"], ["project", "Projects"]];
const KNOWN = { "invalid limit_micros": "The limit must be a positive amount.", "invalid currency": "The currency must be a three-letter code such as USD.", "unknown agent connection": "No agent connection has that key.", "too many budgets": "This workspace has reached the number of budgets it can hold.", "invalid scope key": "That name has characters a budget cannot use.", "invalid unknown_runs_warn": "The run count must be a whole number of 1 or more.", busy: "The hub was busy. Try again." };
const SIGNED_OUT = `<section class="budget-section" data-state="signed-out"><h2>Sign in to see this</h2><p class="budget-state">Your session has ended, or you are not signed in. <a href="/app/">Sign in</a>, then open Settings and this page again.</p></section>`;

const money = (cost) => { const parts = Object.entries(cost || {}).sort(([a], [b]) => a.localeCompare(b)); return parts.length ? parts.map(([code, micros]) => `${e(formatMicros(micros))} ${e(code)}`).join(" · ") : "no cost reported"; };
const key = (kind, value) => `${kind}:${value ?? ""}`;

// Server wording for a refused write, in plain words. Anything unknown still says what happened.
export function writeError(error, verb = "save") {
  if (error?.status === 401) return `Not ${verb === "save" ? "saved" : "cleared"}. You are signed out. Sign in and try again.`;
  if (error?.status === 404) return `Not ${verb === "save" ? "saved" : "cleared"}. Only the workspace owner can change budgets.`;
  const raw = String(error?.message || "").trim();
  const reason = Object.hasOwn(KNOWN, raw) ? KNOWN[raw] : error?.status ? `The hub refused it${raw ? `: ${raw}` : ""}.` : "The hub could not be reached.";
  return `Not ${verb === "save" ? "saved" : "cleared"}. ${reason}`;
}

// One scope row per spend group and per budget; the workspace row is always there.
export function scopeRows(costs, budgets, names = {}) {
  const rows = new Map(), put = (kind, value, label) => { const id = key(kind, value); if (!rows.has(id)) rows.set(id, { kind, key: value, label, spend: null, budget: null }); return rows.get(id); };
  put("workspace", "", "The workspace").spend = costs?.workspace || null;
  for (const group of costs?.by_agent || []) put("agent", group.agent, group.agent == null ? "No agent connection recorded" : scopeLabel({ kind: "agent", key: group.agent }, names)).spend = group;
  for (const group of costs?.by_project || []) put("project", group.project, group.project == null ? "No project recorded" : scopeLabel({ kind: "project", key: group.project })).spend = group;
  for (const budget of budgets || []) put(budget.scope.kind, budget.scope.key ?? "", scopeLabel(budget.scope, names)).budget = budget;
  return [...rows.values()];
}

function spendLine(spend, known) {
  if (!known) return `<p class="budget-meta">Spend could not be read just now, so it is not shown. It is not zero.</p>`;
  if (!spend || !spend.runs) return `<p class="budget-meta">No run started in this window.</p>`;
  const notes = [];
  if (spend.unknown_cost_runs) notes.push(unknownCostText(spend.unknown_cost_runs));
  return `<p class="budget-meta">${e(spend.runs)} ${spend.runs === 1 ? "run" : "runs"} · ${money(spend.cost_micros)}${notes.length ? ` · ${e(notes.join(" · "))}` : ""}</p>`;
}

function budgetLine(budget) {
  const notes = [];
  if (budget.unknown_cost_runs) notes.push(unknownCostText(budget.unknown_cost_runs));
  if (budget.other_currency_runs) notes.push(otherCurrencyText(budget.other_currency_runs, budget.currency));
  if (budget.running_runs) notes.push(runningText(budget.running_runs));
  if (budget.unknown_runs_high) notes.push(`${budget.unknown_runs_warn} or more runs have no cost, so this budget is flagged`);
  return `<p class="budget-line"><span class="budget-badge" data-budget-state="${e(budget.state)}">${e(badgeText(budget))}</span> ${e(formatMicros(budget.spent_micros))} of ${e(formatMicros(budget.limit_micros))} ${e(budget.currency)} spent. Warning at ${e(formatMicros(Math.floor(budget.limit_micros * 0.8)))}, new starts refused at ${e(formatMicros(budget.limit_micros))}.</p>${notes.length ? `<p class="budget-meta">${e(notes.join(" · "))}</p>` : ""}`;
}

const data = (row) => `data-kind="${e(row.kind)}" data-key="${e(row.key ?? "")}"`;
function rowHTML(row, editable, known) {
  const actions = !editable ? "" : row.budget
    ? `<button type="button" data-budget-edit ${data(row)}>Change budget</button> <button type="button" data-budget-clear ${data(row)}>Clear budget</button>`
    : row.key == null ? "" : `<button type="button" data-budget-edit ${data(row)}>Set a budget</button>`;
  return `<li class="budget-row" data-scope="${e(key(row.kind, row.key))}"><p class="budget-name">${e(row.label)}</p>${spendLine(row.spend, known)}${row.budget ? budgetLine(row.budget) : editable ? `<p class="budget-meta">No budget. New starts are never refused on this scope.</p>` : ""}${actions ? `<div class="budget-actions">${actions}</div>` : ""}</li>`;
}

function formHTML(draft, window) {
  const unknown = draft.unknown_runs_warn ?? "";
  const scope = draft.kind === "workspace" ? "the whole workspace" : `${draft.kind} ${draft.key}`;
  const picker = draft.fixed ? `<input type="hidden" name="kind" value="${e(draft.kind)}"><input type="hidden" name="key" value="${e(draft.key)}">` : `<label>Scope<select name="kind">${SHOWN.map(([kind, name]) => `<option value="${kind}"${kind === draft.kind ? " selected" : ""}>${e(name)}</option>`).join("")}</select></label><label>Name of the agent connection or project (not needed for the workspace)<input name="key" value="${e(draft.key)}" autocomplete="off"></label>`;
  return `<form class="budget-form" data-budget-form aria-label="Set a budget">
<h3>${draft.fixed ? `Budget for ${e(scope)}` : "Set a budget"}</h3>${picker}
<label>Limit for ${e(windowText(window))}<input name="limit" inputmode="decimal" value="${e(draft.limit)}" autocomplete="off" required></label>
<label>Currency<input name="currency" value="${e(draft.currency)}" maxlength="3" autocomplete="off" required></label>
<label>Also warn when this many runs have no cost (optional)<input name="unknown" inputmode="numeric" value="${e(unknown)}" autocomplete="off"></label>
${draft.error ? `<p class="budget-state" role="alert">${e(draft.error)}</p>` : ""}
<p class="budget-meta">A warning shows at 80% of the limit. At 100% ${e(HARD_STOP.charAt(0).toLowerCase() + HARD_STOP.slice(1))}</p>
<button type="submit" class="budget-primary">Review budget</button> <button type="button" data-budget-cancel>Cancel</button></form>`;
}

function confirmHTML(draft, window) {
  const scope = draft.kind === "workspace" ? "the whole workspace" : `${draft.kind} ${draft.key}`;
  const limit = formatMicros(draft.micros), warn = formatMicros(Math.floor(draft.micros * 0.8));
  return `<section class="budget-confirm" data-budget-confirm role="group" aria-label="Confirm budget">
<h3>Confirm this budget</h3>
<p>Budget for ${e(scope)}, ${e(windowText(window))}: <strong>${e(limit)} ${e(draft.currency)}</strong>.</p>
<ul class="budget-list"><li>At 80% (${e(warn)} ${e(draft.currency)}) the workspace owner gets a warning.</li><li>At 100% (${e(limit)} ${e(draft.currency)}) a hard stop applies. ${e(HARD_STOP)}</li><li>A run with no cost, or in another currency, is never added to the spend.</li>${draft.replaces ? `<li>This replaces the current budget and starts a new warning cycle.</li>` : ""}</ul>
${draft.error ? `<p class="budget-state" role="alert">${e(draft.error)}</p>` : ""}
<button type="button" class="budget-primary" data-budget-confirm-save${draft.busy ? " disabled" : ""}>Confirm and save</button> <button type="button" data-budget-back>Back</button></section>`;
}

function clearHTML(draft, window) {
  const scope = draft.kind === "workspace" ? "the whole workspace" : `${draft.kind} ${draft.key}`;
  return `<section class="budget-confirm" data-budget-confirm role="group" aria-label="Confirm clearing">
<h3>Clear this budget?</h3><p>The budget for ${e(scope)} (${e(windowText(window))}) is removed. New starts on this scope are no longer limited by it, and its warning goes away.</p>
${draft.error ? `<p class="budget-state" role="alert">${e(draft.error)}</p>` : ""}
<button type="button" class="budget-primary" data-budget-clear-confirm${draft.busy ? " disabled" : ""}>Confirm and clear</button> <button type="button" data-budget-back>Back</button></section>`;
}

const section = (id, title, state, inner) => `<section class="budget-section" id="budgets-${id}" data-state="${state}"><h2>${e(title)}</h2>${inner}</section>`;
const failure = (what, error) => `<p class="budget-state"><strong>Could not read ${what}.</strong> ${e(error?.status === 403 ? "Your access does not include this." : "The read failed just now. This does not mean there is nothing, and it does not mean you are signed out.")}</p><button type="button" data-budget-retry>Try again</button>`;

const rowsHTML = (rows, owner, known) => SHOWN.map(([kind, title]) => {
  const list = rows.filter((row) => row.kind === kind);
  return list.length ? `<h3>${e(title)}</h3><ul class="budget-list">${list.map((row) => rowHTML(row, owner, known)).join("")}</ul>` : "";
}).join("");

// The whole page for one state. Every state says what it is; none renders an empty panel.
export function pageHTML(state) {
  if (state.costsError?.status === 401 || state.budgetsError?.status === 401) return SIGNED_OUT;
  const head = `<header class="budget-head"><h1>Spend and budgets</h1><p>What agent runs cost and the limits on it. Times are UTC. A run whose cost was not reported is never counted as zero.</p></header>`;
  const notice = state.notice ? `<p class="budget-notice" role="status" data-budget-notice="${e(state.notice.kind)}">${e(state.notice.text)}</p>` : "";
  const owner = Boolean(state.budgets), budgets = state.budgets?.budgets || [];
  const window = state.costs?.window || budgets[0]?.window || state.budgets?.window;
  const rows = scopeRows(state.costs, budgets, state.names);
  let spend;
  if (!state.costs) spend = section("spend", "Spend", "error", failure("the spend", state.costsError));
  else {
    const scope = state.costs.scope === "readable" ? `<p class="budget-meta">You see only the projects you can read, so these sums may be lower than the whole workspace.</p>` : "";
    spend = section("spend", "Spend", "ready", `<p class="budget-window" data-budget-window>Window: ${e(windowText(window))}.</p>${scope}${rowsHTML(rows, owner, true)}<p class="budget-meta">${e(unknownCostText(state.costs.unknown_cost?.runs || 0))}. Costs are shown per currency and never converted.</p>`);
  }
  if (!owner) return `${head}${notice}${spend}${section("budgets", "Budgets", state.budgetsError && state.budgetsError.status !== 404 ? "error" : "restricted", accessText(state))}`;
  const draft = state.draft;
  const editor = draft?.step === "confirm" ? confirmHTML(draft, window) : draft?.step === "clear" ? clearHTML(draft, window) : draft ? formHTML(draft, window) : `<button type="button" data-budget-new>Set a budget</button>`;
  const listed = state.costs || !budgets.length ? "" : `<p class="budget-meta">Window: ${e(windowText(window))}.</p>${rowsHTML(rows, true, false)}`;
  return `${head}${notice}${spend}${section("budgets", "Budgets", "ready", `${budgets.length ? "" : `<p>No budget is set. Starts are never refused for cost until you set one.</p>`}${listed}${editor}`)}`;
}

function accessText(state) {
  if (!state.budgetsError || state.budgetsError.status === 404) return `<p class="budget-state"><strong>Only the workspace owner can see budgets.</strong> You can read the spend above; setting or changing limits needs the owner.</p>`;
  return failure("the budgets", state.budgetsError);
}

const parseDraft = (values) => {
  const kind = String(values.kind || ""), key = String(values.key ?? "").trim(), currency = String(values.currency || "").trim().toUpperCase(), micros = parseAmount(values.limit);
  const unknownText = String(values.unknown ?? "").trim();
  const draft = { step: "form", kind, key: kind === "workspace" ? "" : key, limit: String(values.limit ?? "").trim(), currency, unknown_runs_warn: unknownText === "" ? null : Number(unknownText), micros, fixed: values.fixed };
  const bad = !SHOWN.some(([k]) => k === kind) && kind !== "card" ? "Choose what the budget is for."
    : kind !== "workspace" && !draft.key ? "Name the agent connection or project this budget is for."
    : micros === null ? "The limit must be an amount above zero, with at most six decimals, for example 50 or 12.50."
    : !/^[A-Z]{3}$/.test(currency) ? "The currency must be a three-letter code such as USD."
    : unknownText !== "" && !/^[1-9]\d{0,5}$/.test(unknownText) ? "The run count must be a whole number of 1 or more."
    : "";
  return bad ? { ...draft, error: bad } : draft;
};
export { parseDraft };

export function mountBudgets(host, { read = (path, options) => api(path, options), formValues = (form) => Object.fromEntries(new FormData(form)), timeoutMs = 15000 } = {}) {
  let request = 0, state = { costs: null, budgets: null };
  const paint = () => { host.innerHTML = pageHTML(state); host.setAttribute?.("aria-busy", "false"); };
  async function load(notice = null) {
    const mine = ++request;
    host.setAttribute?.("aria-busy", "true");
    if (!state.costs) host.innerHTML = `<p role="status" class="budget-state">Loading spend and budgets…</p>`;
    const [costs, budgets, connections] = await Promise.allSettled([read("/api/costs", { timeoutMs }), read("/api/settings/budgets", { timeoutMs }), read("/api/connections", { timeoutMs })]);
    if (mine !== request) return;
    const names = {};
    if (connections.status === "fulfilled") for (const row of connections.value?.connections || []) names[row.id] = row.display_name || row.name || row.id;
    // A failed read keeps what was shown before, so a failed reload never blanks known numbers.
    state = { costs: costs.status === "fulfilled" ? costs.value : null, costsError: costs.reason, budgets: budgets.status === "fulfilled" ? budgets.value : null, budgetsError: budgets.reason, names, draft: null, notice };
    paint();
  }
  const draftOf = (kind, value, fixed) => {
    const budget = (state.budgets?.budgets || []).find((b) => b.scope.kind === kind && (b.scope.key ?? "") === (value ?? ""));
    const currency = budget?.currency || Object.keys(state.costs?.workspace?.cost_micros || {}).sort()[0] || "USD";
    return { step: "form", kind, key: value ?? "", limit: budget ? formatMicros(budget.limit_micros) : "", currency, unknown_runs_warn: budget?.unknown_runs_warn ?? null, fixed, replaces: Boolean(budget), error: "" };
  };
  async function write(send, verb, done) {
    const draft = state.draft;
    state.draft = { ...draft, busy: true, error: "" }; paint();
    try { await send(); } catch (error) {
      if (error?.status === 401) { host.innerHTML = SIGNED_OUT; return; }
      // The old budget is unchanged; say so and keep the form so nothing typed is lost.
      if (error?.status === 404 && verb === "clear") { await load({ kind: "error", text: "That budget was already gone. The list is up to date." }); return; }
      const unsure = error?.status ? "The budget is unchanged." : "The answer did not arrive, so the budget may or may not have changed. Reload to check.";
      state.draft = { ...draft, busy: false, error: `${writeError(error, verb)} ${unsure}` };
      paint(); return;
    }
    await load({ kind: "ok", text: done });
  }
  host.addEventListener?.("click", (event) => {
    const hit = (selector) => event.target.closest?.(selector);
    if (hit("[data-budget-retry]")) return void load();
    const scoped = (selector) => { const el = hit(selector); return el ? { kind: el.dataset.kind, key: el.dataset.key } : null; };
    const edit = scoped("[data-budget-edit]"), clear = scoped("[data-budget-clear]");
    if (edit) { state.draft = draftOf(edit.kind, edit.key, true); state.notice = null; return void paint(); }
    if (clear) { state.draft = { step: "clear", kind: clear.kind, key: clear.key }; state.notice = null; return void paint(); }
    if (hit("[data-budget-new]")) { state.draft = draftOf("project", "", false); state.draft.replaces = false; return void paint(); }
    if (hit("[data-budget-cancel]")) { state.draft = null; return void paint(); }
    if (hit("[data-budget-back]")) { state.draft = state.draft.step === "confirm" ? { ...state.draft, step: "form", error: "" } : null; return void paint(); }
    if (hit("[data-budget-confirm-save]") && state.draft?.step === "confirm" && !state.draft.busy) {
      const d = state.draft, body = { scope: d.kind === "workspace" ? { kind: "workspace" } : { kind: d.kind, key: d.key }, limit_micros: d.micros, currency: d.currency, unknown_runs_warn: d.unknown_runs_warn };
      return void write(() => read("/api/settings/budgets", { method: "PUT", body, timeoutMs }), "save", "Budget saved.");
    }
    if (hit("[data-budget-clear-confirm]") && state.draft?.step === "clear" && !state.draft.busy) {
      const d = state.draft, params = new URLSearchParams({ kind: d.kind, ...(d.kind === "workspace" ? {} : { key: d.key }) });
      return void write(() => read(`/api/settings/budgets?${params}`, { method: "DELETE", timeoutMs }), "clear", "Budget cleared.");
    }
  });
  host.addEventListener?.("submit", (event) => {
    const form = event.target.closest?.("[data-budget-form]") || event.target;
    event.preventDefault?.();
    const values = formValues(form), draft = parseDraft({ ...values, fixed: state.draft?.fixed });
    const existing = (state.budgets?.budgets || []).some((b) => b.scope.kind === draft.kind && (b.scope.key ?? "") === draft.key);
    state.draft = draft.error ? { ...draft, replaces: existing } : { ...draft, step: "confirm", replaces: existing };
    paint();
  });
  return load();
}
