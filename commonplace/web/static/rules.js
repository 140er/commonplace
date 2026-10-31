import { escape as e } from "./api.js";

const kinds = ["permission", "limit", "instruction", "style"];
const scopes = ["global", "board", "repo"];
const agentName = (agent) => agent === "*" ? "All agents" : String(agent).replace(/^conn:/, "");
const agentRef = (agent) => agent === "*" || agent.startsWith("conn:") ? agent : `conn:${agent}`;
const activeRules = (rules) => rules.filter((rule) => rule.status !== "retired");
export const waitingCount = (rules = []) => rules.filter((rule) => rule.status === "proposed" || Number(rule.pending) === 1).length;

const rewrite = "Keep its meaning and its strength (must / may / never). Don't add new obligations.\nName the trigger, the action and any exception. One to three sentences.";
export function clarifyPrompt(rule) {
  return `Rewrite this rule for an AI agent so it has exactly one reading.\n${rewrite}\n\nAgent: ${rule.agent}    Kind: ${rule.kind}    Scope: ${rule.scope}${rule.scope !== "global" && rule.scope_ref ? ` ${rule.scope_ref}` : ""}\nRule (quoted, verbatim):\n"""\n${rule.text}\n"""\n\nReturn only the improved rule text, nothing else.`;
}
export function clarifySetPrompt(agent, rules) {
  return `Rewrite these rules for an AI agent so each has exactly one reading.\n${rewrite}\nCheck for rules that conflict or overlap.\n\nAgent: ${agent}\nRules (quoted, verbatim):\n"""\n${activeRules(rules).map((rule) => `${rule.key}: ${rule.text}`).join("\n")}\n"""\n\nReturn only <key>: <improved text> lines, nothing else.`;
}
export function parseSetReply(text, rules) {
  const byKey = new Map(activeRules(rules).map((rule) => [rule.key, rule.id])), matches = new Map();
  for (const line of String(text).split(/\r?\n/)) {
    const colon = line.indexOf(":");
    if (colon < 0) continue;
    const key = line.slice(0, colon).trim(), value = line.slice(colon + 1).trim();
    if (byKey.has(key) && value) matches.set(byKey.get(key), { rule_id: byKey.get(key), text: value });
  }
  return [...matches.values()];
}
export function desktopBridge() {
  const bridge = globalThis.window?.commonplaceDesktop;
  return bridge?.version === 1 && typeof bridge.ask === "function" ? bridge : null;
}

// Hirschberg's LCS keeps memory linear even for long rule texts.
function lengths(a, b) {
  let row = new Uint32Array(b.length + 1);
  for (const word of a) {
    const next = new Uint32Array(b.length + 1);
    for (let j = 0; j < b.length; j++) next[j + 1] = word === b[j] ? row[j] + 1 : Math.max(row[j + 1], next[j]);
    row = next;
  }
  return row;
}
function commonWords(a, b) {
  if (!a.length || !b.length) return [];
  if (a.length === 1) return b.includes(a[0]) ? [a[0]] : [];
  const mid = Math.floor(a.length / 2), left = lengths(a.slice(0, mid), b), right = lengths(a.slice(mid).reverse(), [...b].reverse());
  let split = 0;
  for (let j = 1; j <= b.length; j++) if (left[j] + right[b.length - j] > left[split] + right[b.length - split]) split = j;
  return [...commonWords(a.slice(0, mid), b.slice(0, split)), ...commonWords(a.slice(mid), b.slice(split))];
}
export function wordDiff(before, after) {
  if (before === after) return e(before);
  const a = String(before ?? "").match(/\s+|\S+/g) || [], b = String(after ?? "").match(/\s+|\S+/g) || [];
  let i = 0, j = 0, html = "";
  const changes = (endA, endB) => {
    if (endA > i) html += `<del>${e(a.slice(i, endA).join(""))}</del>`;
    if (endB > j) html += `<ins>${e(b.slice(j, endB).join(""))}</ins>`;
    i = endA; j = endB;
  };
  for (const word of commonWords(a, b)) {
    changes(a.indexOf(word, i), b.indexOf(word, j));
    html += e(word); i++; j++;
  }
  changes(a.length, b.length);
  return html;
}

const actionButton = (action, label, attrs = "", cls = "opt") => `<button type="button" class="${cls}" data-rule-action="${action}" ${attrs}>${e(label)}</button>`;
const choices = (values, current) => values.map((value) => `<option value="${e(value)}"${value === current ? " selected" : ""}>${e(value)}</option>`).join("");
function fields(rule = {}) {
  return `<label>Text<textarea name="text" required>${e(rule.text || "")}</textarea></label><div class="rule-fields"><label>Kind<select name="kind">${choices(kinds, rule.kind || "instruction")}</select></label><label>Scope<select name="scope">${choices(scopes, rule.scope || "global")}</select></label><label data-scope-ref${!rule.scope || rule.scope === "global" ? " hidden" : ""}>Scope reference<input name="scope_ref" value="${e(rule.scope_ref || "")}"${rule.scope && rule.scope !== "global" ? " required" : ""}></label></div>`;
}
function clarifyPanel(attrs, bridge) {
  return `<details class="rule-clarify" ${attrs}><summary class="opt">Help me clarify</summary><div class="rule-clarify-panel"><div class="opts">${actionButton("copy-prompt", "Copy prompt")}${bridge ? actionButton("agents", "Ask an agent") : ""}</div><div data-rule-ask></div><div data-rule-reply role="status" aria-live="polite"></div></div></details>`;
}
function ruleRow(rule, suggestions, owner, bridge) {
  const waiting = waitingCount([rule]) > 0, edited = rule.status === "edited" && rule.agent_text != null;
  const controls = owner ? `<div class="opts">${rule.status === "retired" ? actionButton("restore", "Restore") : `${rule.status === "proposed" ? actionButton("accept", "Accept", "", "blockbtn") : ""}${actionButton("edit", "Edit")}${actionButton("retire", "Retire")}`}${actionButton("history", "History")}</div>` : actionButton("history", "History");
  return `<article class="rule-row" data-rule-id="${e(rule.id)}" data-version="${e(rule.version)}"><div class="rule-chips"><span class="pill">${e(rule.kind)}</span>${rule.scope !== "global" ? `<span class="pill">${e(rule.scope)} ${e(rule.scope_ref)}</span>` : ""}<span class="pill${waiting ? " you" : ""}">${e(rule.status)}</span>${rule.pending ? '<span class="pill you">Agent changed</span>' : ""}</div><div data-rule-body><p class="rule-text">${edited ? wordDiff(rule.agent_text, rule.text) : e(rule.text)}</p>${controls}</div>${rule.pending ? `<section class="rule-proposal"><h4>Agent’s wording</h4><p class="rule-text">${wordDiff(rule.text, rule.agent_text)}</p>${owner ? `<div class="opts">${actionButton("accept_agent", "Use agent's")}${actionButton("keep_edit", "Keep mine")}</div>` : ""}</section>` : ""}${suggestions.map((suggestion) => `<section class="rule-proposal" data-suggestion-id="${e(suggestion.id)}"><h4>${e(agentName(suggestion.from_agent))}</h4><p class="rule-text">${wordDiff(rule.text, suggestion.text)}</p>${owner ? `<div class="opts">${actionButton("suggestion-accept", "Accept")}${actionButton("suggestion-dismiss", "Dismiss")}</div>` : ""}</section>`).join("")}${clarifyPanel(`data-clarify-rule="${e(rule.id)}"`, owner && bridge)}<div data-rule-history hidden></div></article>`;
}
export function rulesView({ rules = [], suggestions = [], agents = [] } = {}, { owner = true } = {}) {
  const bridge = desktopBridge();
  const groups = [...new Set([...agents.map(agentRef), ...rules.map((rule) => rule.agent)])].map((agent) => ({ agent, rules: rules.filter((rule) => rule.agent === agent) }));
  groups.sort((a, b) => waitingCount(b.rules) - waitingCount(a.rules) || agentName(a.agent).localeCompare(agentName(b.agent)));
  return `<section class="rules-view" aria-label="Rules">${owner ? `<details class="rule-new"><summary class="blockbtn">New rule</summary><form class="form-tile" data-rule-form="new"><label>Agent<select name="agent"><option value="*">All agents</option>${[...new Set([...agents.map(agentRef), ...rules.map((rule) => rule.agent)])].filter((agent) => agent !== "*").map((agent) => `<option value="${e(agent)}">${e(agentName(agent))}</option>`).join("")}</select></label>${fields()}<div class="opts"><button class="blockbtn">Save</button>${actionButton("cancel-new", "Cancel")}</div></form></details>` : ""}<div class="rule-tiles">${groups.map(({ agent, rules: group }) => `<section class="rule-tile" data-rule-agent="${e(agent)}"><header><h2>${e(agentName(agent))}</h2><span class="pill${waitingCount(group) ? " you" : ""}">${waitingCount(group)}</span>${clarifyPanel(`data-clarify-agent="${e(agent)}"`, owner && bridge)}</header>${activeRules(group).map((rule) => ruleRow(rule, suggestions.filter((s) => s.rule_id === rule.id && (!s.status || s.status === "open")), owner, bridge)).join("") || '<p class="rule-empty">No active rules</p>'}${group.some((rule) => rule.status === "retired") ? `<details class="rule-retired"><summary>Retired (${group.filter((rule) => rule.status === "retired").length})</summary>${group.filter((rule) => rule.status === "retired").map((rule) => ruleRow(rule, [], owner, bridge)).join("")}</details>` : ""}</section>`).join("") || '<p class="message">No rules</p>'}</div></section>`;
}

async function copyText(text, button) {
  try {
    if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
    await navigator.clipboard.writeText(text);
  } catch {
    const input = document.createElement("textarea"), active = document.activeElement;
    input.className = "rule-copy"; input.value = text; input.setAttribute("readonly", "");
    document.body.append(input); input.select();
    try { if (!document.execCommand("copy")) throw new Error("Could not copy"); }
    finally { input.remove(); active?.focus({ preventScroll: true }); }
  }
  const label = button.textContent; button.textContent = "Copied";
  setTimeout(() => { button.textContent = label; }, 1500);
}

// Keep rule events and requests local to this view; the shell supplies its API and refresh.
export function rulesHandlers(root, api, refresh, showError) {
  let payload = { rules: [], agents: [] }, owner = false;
  const pending = new Set();
  const ruleFor = (row) => payload.rules.find((rule) => String(rule.id) === row?.dataset.ruleId);
  async function mutate(key, element, work) {
    if (pending.has(key)) return;
    pending.add(key);
    const buttons = [...element.querySelectorAll("button")];
    buttons.forEach((button) => { button.disabled = true; });
    try { if (await work() !== false) await refresh(); }
    catch (error) {
      if (error.status === 409) { await refresh(); showError("Rule changed. Review it and try again."); }
      else showError(error.message);
    } finally { pending.delete(key); buttons.forEach((button) => { button.disabled = false; }); }
  }
  const context = (panel) => {
    const rule = payload.rules.find((rule) => String(rule.id) === panel.dataset.clarifyRule);
    const rules = rule ? [rule] : payload.rules.filter((rule) => rule.agent === panel.dataset.clarifyAgent);
    return { rules, prompt: rule ? clarifyPrompt(rule) : clarifySetPrompt(panel.dataset.clarifyAgent, rules), single: !!rule };
  };
  root.addEventListener("change", (event) => {
    const form = event.target.closest("[data-rule-form]");
    if (!form || event.target.name !== "scope") return;
    const field = form.querySelector("[data-scope-ref]"), global = event.target.value === "global";
    field.hidden = global; field.querySelector("input").required = !global;
  });
  root.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-rule-action]");
    if (!button || button.disabled) return;
    const action = button.dataset.ruleAction, row = button.closest("[data-rule-id]"), rule = ruleFor(row);
    const panel = button.closest(".rule-clarify");
    try {
      if (action === "copy-prompt") await copyText(context(panel).prompt, button);
      else if (action === "copy-reply") await copyText(panel.querySelector("[data-reply-text]").textContent, button);
      else if (action === "agents" && owner) {
        const bridge = desktopBridge(); if (!bridge) return;
        button.disabled = true;
        try {
          const agents = await bridge.agents();
          panel.querySelector("[data-rule-ask]").innerHTML = agents.length ? `<div class="rule-ask"><label>Agent<select data-ask-agent>${agents.map((agent) => `<option value="${e(agent.id)}">${e(agent.name)}</option>`).join("")}</select></label>${actionButton("ask", "Ask")}</div>` : '<p role="status">No agents available</p>';
        } finally { button.disabled = false; }
      } else if (action === "ask" && owner) {
        const bridge = desktopBridge(); if (!bridge) return;
        const select = panel.querySelector("[data-ask-agent]"), agent = select.value, name = select.selectedOptions[0].textContent;
        const { rules, prompt, single } = context(panel), reply = panel.querySelector("[data-rule-reply]");
        await mutate(`ask-${panel.dataset.clarifyRule || panel.dataset.clarifyAgent}`, panel, async () => {
          reply.textContent = `Asking ${name}…`;
          try {
            const answer = await bridge.ask({ agent, prompt, purpose: "rule-clarify" });
            if (typeof answer?.text !== "string" || !answer.text.trim()) throw new Error("Agent returned no rule text");
            const matches = single ? [{ rule_id: rules[0].id, text: answer.text }] : parseSetReply(answer.text, rules);
            if (!matches.length) {
              reply.innerHTML = `<p class="rule-text" data-reply-text>${e(answer.text)}</p>${actionButton("copy-reply", "Copy")}`;
              return false;
            }
            for (const match of matches) await api(`/api/rules/${encodeURIComponent(match.rule_id)}/suggestions`, { method: "POST", body: { text: match.text, from_agent: name } });
            reply.textContent = "";
          } catch (error) { reply.textContent = ""; throw error; }
        });
      } else if (action === "edit" && owner && rule) {
        row.querySelector("[data-rule-body]").innerHTML = `<form class="form-tile" data-rule-form="edit">${fields(rule)}<div class="opts"><button class="blockbtn">Save</button>${actionButton("cancel-edit", "Cancel")}</div></form>`;
        row.querySelector("textarea").focus();
      } else if (action === "cancel-edit") await refresh();
      else if (action === "cancel-new") { const details = button.closest("details"); details.open = false; details.querySelector("form").reset(); details.querySelector("[data-scope-ref]").hidden = true; details.querySelector('[name="scope_ref"]').required = false; }
      else if (action === "history" && rule) {
        const history = row.querySelector("[data-rule-history]");
        if (!history.hidden) { history.hidden = true; return; }
        button.disabled = true;
        try {
          const result = await api(`/api/rules/${encodeURIComponent(rule.id)}/history`);
          const versions = Array.isArray(result) ? result : result.history || result.versions || [];
          history.innerHTML = `<h4>History</h4>${versions.map((version) => `<section><span class="quiet">${e(version.version)} · ${e(version.status)}</span><p class="rule-text">${e(version.text)}</p>${version.note ? `<p class="rule-text quiet">${e(version.note)}</p>` : ""}</section>`).join("")}`;
          history.hidden = false;
        } finally { button.disabled = false; }
      } else if (owner && action.startsWith("suggestion-")) {
        const suggestion = button.closest("[data-suggestion-id]").dataset.suggestionId;
        await mutate(`suggestion-${suggestion}`, row, () => api(`/api/rules/suggestions/${encodeURIComponent(suggestion)}`, { method: "POST", body: { action: action.slice(11) } }));
      } else if (owner && rule && ["accept", "retire", "restore", "accept_agent", "keep_edit"].includes(action)) {
        await mutate(`rule-${rule.id}`, row, () => api(`/api/rules/${encodeURIComponent(rule.id)}/action`, { method: "POST", body: { action, version: Number(row.dataset.version) } }));
      }
    } catch (error) { showError(error.message); }
  });
  root.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-rule-form]"); if (!form) return;
    event.preventDefault(); if (!owner) return;
    const row = form.closest("[data-rule-id]"), rule = ruleFor(row), body = Object.fromEntries(new FormData(form));
    body.scope_ref = body.scope === "global" ? null : body.scope_ref;
    if (form.dataset.ruleForm === "edit" && !rule) return;
    await mutate(rule ? `rule-${rule.id}` : "new-rule", form, () => api(rule ? `/api/rules/${encodeURIComponent(rule.id)}` : "/api/rules", { method: rule ? "PATCH" : "POST", body: rule ? { ...body, version: Number(row.dataset.version) } : body }));
  });
  return {
    update(next, options = {}) { payload = next; owner = options.owner ?? true; },
    busy() { return pending.size > 0 || !!root.querySelector('.rules-view form[data-rule-form="edit"], .rules-view details[open], .rules-view [data-rule-history]:not([hidden])'); },
  };
}
