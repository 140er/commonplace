// Home: what needs you, one tile per space, then every open task. Data in, HTML out;
// the task row and its dot come from app.js so all three parts share #19's colours.
import { escape as e, elapsed, safeLink } from "./api.js";
import { hasQuestions } from "./decide.js";

const enc = encodeURIComponent;
const BUCKETS = ["asks", "done_review", "backlog_from_agents", "ready_to_merge", "stale"];
// The server's need order, with a stale claim slotted in after "ready to merge".
const ORDER = ["you", "plan", "merge", "stale", "checks", "working", "idle", "done"];
const LABELS = { you: "Needs you", plan: "Plan to approve", merge: "Ready to merge", stale: "Stale", checks: "Checks running", working: "Working", idle: "Idle", done: "Done", quiet: "Quiet" };
const rank = (state) => (ORDER.includes(state) ? ORDER.indexOf(state) : ORDER.indexOf("idle"));
const staleSet = (needs) => new Set((needs.stale || []).map((c) => c.number));
const stateOf = (card, stale) => (stale.has(card.number) && rank(card.need) > rank("stale") ? "stale" : card.need);

// One entry per card in the Needs you queue, ordered like the spoken digest:
// addressed asks first, then need, then card number.
export function needItems(needs) {
  const byNumber = new Map();
  for (const key of BUCKETS) for (const card of needs[key] || []) {
    if (!byNumber.has(card.number)) byNumber.set(card.number, { card, buckets: new Set() });
    byNumber.get(card.number).buckets.add(key);
  }
  const asked = ({ card, buckets }) => Boolean(card.ask) && (buckets.has("asks") || buckets.has("done_review"));
  const key = (item) => (asked(item) ? 0 : Math.max(0, ["you", "plan", "merge", "checks", "working", "idle", "done"].indexOf(item.card.need)));
  return [...byNumber.values()]
    .sort((a, b) => key(a) - key(b) || a.card.number - b.card.number)
    .map((item) => ({ card: item.card, reason: reason(item, asked(item)) }));
}
function reason({ card, buckets }, asked) {
  if (asked) return `asks: ${card.ask.split("\n")[0]}`;
  if (card.need === "plan") return "plan to approve";
  if (card.need === "merge" || buckets.has("ready_to_merge")) return "ready to merge";
  if (buckets.has("stale")) return "claim went stale";
  if (buckets.has("backlog_from_agents")) return "filed by an agent";
  return LABELS[card.need] || "";
}

const pill = (state, dot) => `<span class="pill ${e(state)}">${dot({ need: ["stale", "quiet"].includes(state) ? "idle" : state })}${e(LABELS[state] || LABELS.idle)}</span>`;
function action(card, state) {
  const open = (label) => `<a class="opt" href="#/card/${card.number}">${label}</a>`;
  if (state === "you") return open("Answer");
  if (state === "plan") return open("Approve");
  if (state === "merge") {
    const href = safeLink(card.pr_url);
    return href ? `<a class="opt" href="${e(href)}" target="_blank" rel="noopener noreferrer">Merge</a>` : open("Merge");
  }
  if (state === "stale") return `<button class="opt" data-action="move" data-card="${card.number}" data-status="todo">Retake</button>`;
  return "";
}

function needBox(items, selected, speak, dot, answer) {
  const count = items.length;
  const heading = count ? `<b class="n">${count}</b> ${count === 1 ? "thing needs" : "things need"} you` : "Nothing needs you";
  const speakBtn = speak ? '<button class="opt speak" data-action="speak-digest" aria-controls="digest-captions">Speak</button>' : "";
  const answerBtn = answer ? '<a class="opt go answer" href="#/decide">Answer</a>' : "";
  const actions = answerBtn || speakBtn ? `<div class="need-actions">${answerBtn}${speakBtn}</div>` : "";
  return `<section class="needbox ${count ? "waiting" : ""}" aria-labelledby="needbox-title"><header><h2 id="needbox-title">${heading}</h2>${actions}</header><div id="digest-captions" class="digest-captions" role="region" aria-label="Spoken digest" hidden></div>${count ? `<ol>${items.map(({ card, reason: why }) => `<li class="nline ${card.number === selected ? "sel" : ""}" data-row="${card.number}" data-key="${card.number}" tabindex="-1">${dot(card)}<a class="tt" href="${askedLink(card, why)}">${e(card.title)}</a><span class="why">${e(why)}</span></li>`).join("")}</ol>` : ""}</section>`;
}
function askedLink(card, why) {
  return why.startsWith("asks: ") ? `#/decide/${card.number}` : `#/card/${card.number}`;
}

function spaces(boards, open, items, stale, dot) {
  return `<section class="home-spaces" aria-label="Spaces">${boards.map((b) => {
    const cards = open.filter((c) => c.board === b.slug);
    const worst = cards.map((c) => stateOf(c, stale)).reduce((w, s) => (rank(s) < rank(w) ? s : w), "idle");
    const counts = b.counts || {};
    const waiting = items.filter((i) => i.card.board === b.slug).length;
    const facts = [`${(counts.todo || 0) + (counts.doing || 0) + (counts.review || 0)} open`, waiting ? `<span class="hot">${waiting} waiting</span>` : `${counts.doing || 0} doing`];
    return `<article class="home-space" data-key="space-${e(b.slug)}"><header><h3>${e(b.name || b.slug)}</h3><a class="opt" href="#/board/${enc(b.slug)}">Open</a></header><div class="home-facts">${pill(["idle", "done"].includes(worst) ? "quiet" : worst, dot)}<span>${facts.join("</span><span>")}</span></div></article>`;
  }).join("")}</section>`;
}

function tasks(open, total, stale, boards, selected, { dot, taskRow }) {
  const names = new Map(boards.map((b) => [b.slug, b.name || b.slug]));
  const rows = open.map((card) => ({ card, state: stateOf(card, stale) })).sort((a, b) => rank(a.state) - rank(b.state) || a.card.number - b.card.number);
  const space = (card) => `<a href="#/board/${enc(card.board)}">${e(names.get(card.board) || card.board)}</a>`;
  const sub = (card) => [card.branch ? `<span>${e(card.branch)}</span>` : "", card.pr_number ? `<span>PR #${e(card.pr_number)}</span>` : ""].filter(Boolean).join("");
  return `<section class="home-tasks" aria-labelledby="tasks-title"><h2 id="tasks-title">Open tasks <span class="n">${total ?? rows.length}</span></h2>${rows.length ? `<table><thead><tr><th scope="col">Task</th><th scope="col">Space</th><th scope="col">Status</th><th scope="col">Elapsed</th><th scope="col"><span class="sr-only">Action</span></th></tr></thead><tbody>${rows.map(({ card, state }) => `<tr data-row="${card.number}" data-key="task-${card.number}" class="${card.number === selected ? "sel" : ""}"><td class="task"><a href="#/card/${card.number}">${e(card.title)}</a>${sub(card) ? `<span class="sub">${sub(card)}</span>` : ""}</td><td>${space(card)}</td><td>${pill(state, dot)}</td><td><time data-elapsed="${e(card.updated_at)}" datetime="${e(card.updated_at)}">${elapsed(card.updated_at)}</time></td><td class="act">${action(card, state)}</td></tr>`).join("")}</tbody></table><div class="tstack">${rows.map(({ card, state }) => `<article class="trow-card ${card.number === selected ? "sel" : ""}" data-row="${card.number}" data-key="stack-${card.number}">${taskRow(card, space(card))}${action(card, state) ? `<div class="opts">${action(card, state)}</div>` : ""}</article>`).join("")}</div>` : '<p class="empty">No open tasks</p>'}</section>`;
}

export function homeView({ needs = {}, boards = [], open = [], total = null, selected = null, speak = false }, rows) {
  const items = needItems(needs), stale = staleSet(needs);
  return `<section class="queue home">${needBox(items, selected, speak, rows.dot, hasQuestions(needs))}${spaces(boards, open, items, stale, rows.dot)}${tasks(open, total, stale, boards, selected, rows)}</section>`;
}
