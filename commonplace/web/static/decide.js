// Phone queue for cards that ask a question. The POST waits here because the server
// has no undo: a tap only sends once the hold ends, the next answer starts, or the page hides.
import { actor, elapsed, escape as e } from "./api.js";

export const COPY = {
  offline: "Offline. Answers are paused until the connection is back.",
  notSent: (number) => `#${number} not sent. The server did not answer.`,
  elsewhere: (number) => `#${number} was answered elsewhere. Nothing was sent from here.`,
  changed: "The question changed.",
  loadFailed: "The server did not answer.",
  sendsIn: (seconds) => `Sends in ${seconds} ${seconds === 1 ? "second" : "seconds"}`,
  showLater: (count) => `Show the ${count} for later`,
  backHome: "Back to Home",
  next: "Next question",
  later: "Later",
  undo: "Undo",
  retry: "Retry",
  advised: "advised",
  reply: "Reply",
  answered: (count) => `${count} answered.`,
  allAnswered: (count) => `All ${count} answered.`,
  forLater: (count) => `${count} for later.`,
  answeredLine: "answered elsewhere",
};

export const RELOAD_ERRORS = new Set(["card is not in review", "choose one of the offered options"]);
export const HOLD_SECONDS = 5;
const ADVISED = /^(.*\S)\s*\((advies|advised)\)\s*$/i;

export function splitChoice(text) {
  const value = String(text ?? "");
  const match = value.match(ADVISED);
  if (!match) return { label: value, advised: false };
  return { label: match[1], advised: true };
}

export function answerPayload(card, optionIndex, reply) {
  const body = { version: card.version, then: "return" };
  const options = card.ask_options || [];
  if (Number.isInteger(optionIndex) && optionIndex >= 0 && optionIndex < options.length) body.choice = options[optionIndex];
  const text = typeof reply === "string" ? reply.trim() : "";
  if (text) body.reply = text;
  return body;
}

export function decideQueue(needs = {}) {
  const seen = new Set();
  const out = [];
  for (const key of ["asks", "done_review"]) {
    const cards = [...(needs[key] || [])].sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || "")) || a.number - b.number);
    for (const card of cards) if (card && !seen.has(card.number)) { seen.add(card.number); out.push(card); }
  }
  return out;
}

export function hasQuestions(needs) {
  return decideQueue(needs).length > 0;
}

export function offlineNow(onLine, fails) {
  return onLine === false || fails >= 2;
}

export function queueKey(key, typing) {
  if (typing) return null;
  if (/^[1-6]$/.test(key)) return { type: "answer", optionIndex: Number(key) - 1 };
  if (key === "j") return { type: "move", delta: 1 };
  if (key === "k") return { type: "move", delta: -1 };
  if (key === "l") return { type: "later" };
  if (key === "u") return { type: "undo" };
  if (key === "o") return { type: "open" };
  if (key === "Enter") return { type: "focus-reply" };
  if (key === "Escape") return { type: "home" };
  return null;
}

export function createSession(start = null, me = "") {
  return {
    phase: "loading", ready: false, start: start ?? null, me: me || "",
    items: [], cursor: 0, replay: [], offline: false, slow: false, notice: null,
  };
}

function blank(card) {
  return {
    number: card.number, card, question: card.ask ?? "", kind: "open",
    visited: false, again: false, missing: false, reply: "", pending: null, holdLeft: 0,
  };
}

function copy(session) {
  return {
    ...session,
    items: session.items.map((item) => ({ ...item })),
    replay: [...session.replay],
    notice: session.notice ? { ...session.notice } : null,
  };
}

function hold(session) {
  return session.items.find((item) => item.kind === "hold") || null;
}

function release(session, effects) {
  const item = hold(session);
  if (!item) return;
  item.kind = "sending";
  item.holdLeft = 0;
  item.visited = true;
  effects.push({ type: "post", number: item.number, body: item.pending });
}

function advance(session) {
  while (session.replay.length) {
    const number = session.replay.shift();
    const index = session.items.findIndex((item) => item.number === number);
    const item = session.items[index];
    if (item && item.kind === "open") {
      item.again = false;
      session.cursor = index;
      return true;
    }
  }
  const index = session.items.findIndex((item, i) => i > session.cursor && item.kind === "open");
  if (index >= 0) { session.cursor = index; return true; }
  return false;
}

function finish(session) {
  const current = session.items[session.cursor];
  if (current && current.kind !== "open") current.visited = true;
  if (!advance(session)) session.phase = "done";
  else session.phase = "queue";
  const landed = session.items[session.cursor];
  if (session.phase === "queue" && landed?.missing) return { type: "get", number: landed.number };
  return null;
}

function sameAnswer(event, pending) {
  const choice = pending.choice ?? null;
  const reply = pending.reply || "";
  return (event.choice ?? null) === choice && (event.text || "") === reply;
}

function applyCard(session, number, card, focus) {
  const index = session.items.findIndex((item) => item.number === number);
  if (index < 0 || !card) return;
  const item = session.items[index];
  const show = Boolean(focus) || session.cursor === index;
  const last = [...(card.events || [])].reverse().find((event) => event.kind === "decide");
  if (last && item.pending && last.actor === session.me && sameAnswer(last, item.pending)) {
    item.kind = "sent";
    item.visited = true;
    item.again = false;
    item.holdLeft = 0;
    session.replay = session.replay.filter((n) => n !== number);
    if (session.notice?.number === number) session.notice = null;
    return;
  }
  const previous = item.question;
  const previousOptions = JSON.stringify(item.card.ask_options || []);
  if (card.ask != null) item.question = card.ask;
  item.card = card;
  item.missing = false;
  if (card.status !== "review" || card.ask == null) {
    item.kind = "elsewhere";
    item.pending = null;
    item.holdLeft = 0;
    session.replay = session.replay.filter((n) => n !== number);
    if (session.notice?.number === number) session.notice = null;
    if (show) { session.cursor = index; session.phase = "queue"; }
    return;
  }
  const changed = card.ask !== previous || JSON.stringify(card.ask_options || []) !== previousOptions;
  if (!changed) {
    if (item.pending) item.pending = { ...item.pending, version: card.version };
    if (item.pending) fail(session, item);
    return;
  }
  item.kind = "open";
  item.pending = null;
  item.holdLeft = 0;
  item.again = false;
  session.replay = session.replay.filter((n) => n !== number);
  if (!show) return;
  session.cursor = index;
  session.phase = "queue";
  session.notice = { kind: "changed", number };
}

function fail(session, item) {
  item.kind = "open";
  item.again = true;
  item.visited = true;
  item.holdLeft = 0;
  session.notice = { kind: "failed", number: item.number, body: item.pending, text: COPY.notSent(item.number) };
  if (session.phase === "done") {
    session.phase = "queue";
    session.cursor = session.items.indexOf(item);
    session.replay = session.replay.filter((n) => n !== item.number);
    return;
  }
  session.replay = [item.number, ...session.replay.filter((n) => n !== item.number)];
}

function drop(session, index, text) {
  const number = session.items[index].number;
  session.items.splice(index, 1);
  if (session.cursor > index) session.cursor -= 1;
  if (session.cursor >= session.items.length) session.cursor = Math.max(0, session.items.length - 1);
  session.replay = session.replay.filter((n) => n !== number);
  session.notice = { kind: "error", text: text || COPY.loadFailed };
  if (!session.items.length) session.phase = "done";
  else if (session.phase === "done") session.phase = "queue";
}

export function reduce(session, event) {
  const effects = [];
  const next = copy(session);
  if (event.type === "offline") {
    next.offline = Boolean(event.offline);
    return { session: next, effects };
  }
  if (event.type === "slow") {
    if (!next.ready) next.slow = true;
    return { session: next, effects };
  }
  if (event.type === "load") {
    if (next.ready) return { session: next, effects };
    const ordered = (event.cards || []).map(blank);
    if (!ordered.length) return { session: { ...next, ready: true, phase: "empty", items: [], slow: false }, effects: [{ type: "home" }] };
    let itemsInOrder = ordered;
    if (next.start != null) {
      const index = ordered.findIndex((item) => item.number === next.start);
      if (index > 0) itemsInOrder = [...ordered.slice(index), ...ordered.slice(0, index)];
    }
    return { session: { ...next, ready: true, phase: "queue", items: itemsInOrder, cursor: 0, slow: false }, effects };
  }
  if (event.type === "sync") {
    if (!next.ready) return { session: next, effects };
    const cards = event.cards || [];
    const incoming = new Map(cards.map((card) => [card.number, card]));
    const known = new Set(next.items.map((item) => item.number));
    for (const card of cards) if (!known.has(card.number)) next.items.push(blank(card));
    next.items.forEach((item, index) => {
      if (item.kind === "hold" || item.kind === "sending" || item.kind === "sent") return;
      if (!incoming.has(item.number)) {
        item.missing = true;
        if (index === next.cursor && next.phase === "queue") effects.push({ type: "get", number: item.number });
        return;
      }
      const card = incoming.get(item.number);
      item.missing = false;
      if (item.kind === "elsewhere") return;
      const changed = card.ask !== item.question || JSON.stringify(card.ask_options || []) !== JSON.stringify(item.card.ask_options || []);
      if (card.ask != null) item.question = card.ask;
      item.card = card;
      if (changed && index === next.cursor && item.kind === "open") next.notice = { kind: "changed", number: item.number };
    });
    return { session: next, effects };
  }
  if (event.type === "tick") {
    const item = hold(next);
    if (!item) return { session: next, effects };
    item.holdLeft -= 1;
    if (item.holdLeft <= 0) release(next, effects);
    return { session: next, effects };
  }
  if (event.type === "flush") {
    release(next, effects);
    return { session: next, effects };
  }
  if (event.type === "undo") {
    const item = hold(next);
    if (!item) return { session: next, effects };
    item.kind = "open";
    item.holdLeft = 0;
    item.visited = false;
    item.again = false;
    next.cursor = next.items.indexOf(item);
    next.phase = "queue";
    next.replay = next.replay.filter((n) => n !== item.number);
    return { session: next, effects };
  }
  if (event.type === "later") {
    if (next.phase !== "queue") return { session: next, effects };
    const item = next.items[next.cursor];
    if (!item || !["open", "later"].includes(item.kind)) return { session: next, effects };
    item.kind = "later";
    item.visited = true;
    item.again = false;
    next.replay = next.replay.filter((n) => n !== item.number);
    const get = finish(next);
    if (get) effects.push(get);
    return { session: next, effects };
  }
  if (event.type === "show-later") {
    const later = next.items.filter((item) => item.kind === "later");
    if (!later.length) return { session: next, effects };
    for (const item of later) { item.kind = "open"; item.visited = false; item.again = false; }
    next.replay = [];
    next.cursor = next.items.findIndex((item) => item.kind === "open");
    next.phase = "queue";
    next.notice = next.notice?.kind === "changed" ? null : next.notice;
    return { session: next, effects };
  }
  if (event.type === "next") {
    const item = next.items[next.cursor];
    if (!item || item.kind !== "elsewhere") return { session: next, effects };
    const get = finish(next);
    if (get) effects.push(get);
    return { session: next, effects };
  }
  if (event.type === "move" || event.type === "jump") {
    if (next.phase !== "queue" || !next.items.length) return { session: next, effects };
    if (event.type === "jump") {
      const index = next.items.findIndex((item) => item.number === event.number);
      if (index < 0) return { session: next, effects };
      next.cursor = index;
    } else next.cursor = Math.max(0, Math.min(next.items.length - 1, next.cursor + (event.delta || 0)));
    const landed = next.items[next.cursor];
    if (landed?.missing) effects.push({ type: "get", number: landed.number });
    return { session: next, effects };
  }
  if (event.type === "retry") {
    if (next.offline || next.notice?.kind !== "failed" || !next.notice.body) return { session: next, effects };
    const item = next.items.find((row) => row.number === next.notice.number);
    if (!item || item.kind === "sending") return { session: next, effects };
    item.kind = "sending";
    item.again = false;
    item.holdLeft = 0;
    next.replay = next.replay.filter((n) => n !== item.number);
    effects.push({ type: "post", number: item.number, body: next.notice.body });
    return { session: next, effects };
  }
  if (event.type === "result") {
    const index = next.items.findIndex((item) => item.number === event.number);
    if (index < 0) return { session: next, effects };
    const item = next.items[index];
    if (event.status === 200) {
      item.kind = "sent";
      item.visited = true;
      item.holdLeft = 0;
      item.again = false;
      next.replay = next.replay.filter((n) => n !== item.number);
      if (next.notice?.number === item.number) next.notice = null;
      return { session: next, effects };
    }
    if (event.status === 409 || (event.status === 403 && RELOAD_ERRORS.has(event.error))) {
      if (event.card) applyCard(next, event.number, event.card, true);
      else effects.push({ type: "get", number: event.number });
      return { session: next, effects };
    }
    if (event.status === 400 || event.status === 403 || event.status === 404) {
      drop(next, index, event.error);
      return { session: next, effects };
    }
    if (!item.pending) {
      next.notice = { kind: "error", text: COPY.loadFailed };
      return { session: next, effects };
    }
    fail(next, item);
    return { session: next, effects };
  }
  if (event.type === "card") {
    applyCard(next, event.number, event.card, false);
    return { session: next, effects };
  }
  if (event.type === "answer" || event.type === "reply") {
    // An answer has to match the version on screen. A send from a dead connection is refused, not queued.
    if (next.offline || next.phase !== "queue") return { session: next, effects };
    const item = next.items[next.cursor];
    if (!item || !["open", "later"].includes(item.kind)) return { session: next, effects };
    const optionIndex = event.type === "reply" ? null : event.optionIndex;
    const reply = typeof event.reply === "string" ? event.reply : item.reply;
    const body = answerPayload(item.card, optionIndex, reply);
    if (!body.choice && !body.reply) return { session: next, effects };
    release(next, effects);
    item.kind = "hold";
    item.pending = body;
    item.reply = body.reply || "";
    item.holdLeft = HOLD_SECONDS;
    item.visited = true;
    item.again = false;
    next.replay = next.replay.filter((n) => n !== item.number);
    if (next.notice?.kind === "changed" || next.notice?.number === item.number) next.notice = null;
    const get = finish(next);
    if (get) effects.push(get);
    return { session: next, effects };
  }
  return { session: next, effects };
}

function meta(card) {
  return `#${card.number} ${card.board} ${actor(card.return_to)} ${elapsed(card.updated_at)}`;
}

function shown(item) {
  if (item.pending?.choice) return splitChoice(item.pending.choice).label;
  return item.pending?.reply || item.reply || "";
}

function cellClass(session, item, index) {
  if (session.phase === "done") return item.kind === "later" ? "now" : "done";
  if (session.phase === "queue" && index === session.cursor) return "now";
  if (item.kind === "sent" || item.kind === "hold" || item.kind === "sending") return "done";
  if (item.again && item.visited) return "done";
  return "wait";
}

export function meterHTML(session) {
  const count = session.items.length;
  const fraction = session.phase === "done" ? "done" : session.phase === "queue" && count ? `${session.cursor + 1}/${count}` : "";
  const cells = count > 12 || !count ? "" : session.items.map((item, index) => `<i class="${cellClass(session, item, index)}"></i>`).join("");
  const only = count > 12 || session.phase === "done" ? " only" : "";
  return `<span class="meter" aria-hidden="true">${cells}</span><span class="frac${only}">${e(fraction)}</span>`;
}

export function leftCount(session) {
  if (session.phase !== "queue") return null;
  return session.items.filter((item) => item.kind === "open" || item.kind === "later").length;
}

export function toolsHTML(session) {
  return `<div class="decide-tools">${meterHTML(session)}<kbd>j</kbd><kbd>k</kbd><a class="close" href="#/" aria-label="Back to Home">×</a></div>`;
}

function statusHTML(session) {
  const item = hold(session);
  if (item) {
    const blocks = Array.from({ length: HOLD_SECONDS }, (_, i) => `<i class="${i < item.holdLeft ? "on" : ""}"></i>`).join("");
    return `<div class="decide-status" role="status"><span class="text">#${item.number} ${e(shown(item))}</span><span class="end"><span class="blocks" aria-hidden="true">${blocks}</span><span class="sr-only">${e(COPY.sendsIn(item.holdLeft))}</span><button type="button" data-action="queue-undo">${COPY.undo}</button></span></div>`;
  }
  if (session.notice?.kind === "failed") {
    const sending = session.items.some((row) => row.number === session.notice.number && row.kind === "sending");
    return `<div class="decide-status bad" role="alert"><i class="decide-dot bad" aria-hidden="true"></i><span class="text">${e(session.notice.text)}</span><button type="button" data-action="queue-retry" ${sending ? "disabled" : ""}>${COPY.retry}</button></div>`;
  }
  if (session.notice?.kind === "error") return `<div class="decide-status bad" role="alert"><i class="decide-dot bad" aria-hidden="true"></i><span class="text">${e(session.notice.text)}</span></div>`;
  if (session.offline && session.phase === "queue") return `<div class="decide-status" role="status"><i class="decide-dot hollow" aria-hidden="true"></i><span class="text">${e(COPY.offline)}</span></div>`;
  const current = session.items[session.cursor];
  if (session.phase === "queue" && current?.kind === "elsewhere") return `<div class="decide-status" role="status"><i class="decide-dot ok" aria-hidden="true"></i><span class="text">${e(COPY.elsewhere(current.number))}</span></div>`;
  const changed = session.items[session.cursor];
  if (session.notice?.kind === "changed" && changed && session.notice.number === changed.number) return `<div class="decide-status" role="status"><span class="text">${e(COPY.changed)}</span></div>`;
  if (session.slow && session.phase === "loading") return `<div class="decide-status bad" role="alert"><i class="decide-dot bad" aria-hidden="true"></i><span class="text">${e(COPY.loadFailed)}</span><button type="button" data-action="retry">${COPY.retry}</button></div>`;
  return "";
}

function optionHTML(choice, index, disabled) {
  const { label, advised } = splitChoice(choice);
  const name = advised ? `${label}, advised` : label;
  const hint = index < 6 ? `<kbd class="k">${index + 1}</kbd>` : "";
  const tag = advised ? `<span class="advise">${COPY.advised}</span>` : "";
  return `<button type="button" class="opt${advised ? " go" : ""}" data-action="queue-answer" data-option="${index}" aria-label="${e(name)}" ${disabled ? "disabled" : ""}><span>${e(label)}</span>${tag}${hint}</button>`;
}

function listHTML(session) {
  if (!session.items.length) return `<ol class="decide-list"></ol>`;
  return `<ol class="decide-list">${session.items.map((item, index) => {
    const current = session.phase === "queue" && index === session.cursor;
    const answered = item.kind === "sent" || item.kind === "hold" || item.kind === "sending";
    const dot = answered ? "ok" : "now";
    const sub = answered ? `answered: ${shown(item)}` : item.kind === "later" ? COPY.later : item.kind === "elsewhere" ? COPY.answeredLine : meta(item.card);
    return `<li class="${current ? "sel" : ""}" ${current ? 'aria-current="true"' : ""}><button type="button" data-action="queue-jump" data-card="${item.number}"><i class="decide-dot ${dot}" aria-hidden="true"></i><span class="lt">${e(item.card.title)}</span><span class="ls">${e(sub)}</span></button></li>`;
  }).join("")}</ol>`;
}

function bar(session) {
  return `<header class="decide-bar"><a class="mark" href="#/" aria-label="Commonplace">c</a><div class="decide-bar-main"><h1>Decide</h1>${meterHTML(session)}<a class="close" href="#/" aria-label="Back to Home">×</a></div></header>`;
}

function replyRow(item, draft, disabled, solo) {
  return `<div class="decide-reply${solo ? " solo" : ""}"><form data-form="queue-reply" data-card="${item.number}"><input name="reply" aria-label="${COPY.reply}" placeholder="${COPY.reply}" data-draft="reply-${item.number}" autocomplete="off" value="${e(draft)}" ${item.card.ask_options?.length ? "" : "required"}><button class="send" type="submit" aria-label="Send reply" ${disabled ? "disabled" : ""}>↵</button></form><a class="opt card-link" href="#/card/${item.number}">Card<kbd class="k">O</kbd></a><button type="button" class="opt later" data-action="queue-later">${COPY.later}<kbd class="k">L</kbd></button></div>`;
}

function questionHTML(session, drafts) {
  const item = session.items[session.cursor];
  const status = statusHTML(session);
  if (!item) return `${status}<article class="decide-card"><span class="cursor" aria-hidden="true"></span></article>`;
  if (item.kind === "elsewhere") {
    return `${status}<article class="decide-card stale"><p class="meta">${e(meta(item.card))}</p><a class="decide-title" href="#/card/${item.number}">${e(item.card.title)}</a><h2 tabindex="-1" data-question="${item.number}">${e(item.question || "")}</h2></article><div class="decide-actions"><button type="button" class="opt go" data-action="queue-next">${COPY.next}</button></div>`;
  }
  if (item.kind === "sent" || item.kind === "sending" || item.kind === "hold") {
    return `${status}<article class="decide-card"><p class="meta">${e(meta(item.card))}</p><a class="decide-title" href="#/card/${item.number}">${e(item.card.title)}</a><h2 tabindex="-1" data-question="${item.number}">${e(item.question || "")}</h2><p class="said">${e(shown(item))}</p></article>`;
  }
  const options = item.card.ask_options || [];
  const disabled = session.offline;
  const draft = drafts[`reply-${item.number}`] ?? item.reply ?? "";
  const buttons = options.map((choice, index) => optionHTML(choice, index, disabled)).join("");
  return `${status}<article class="decide-card"><p class="meta">${e(meta(item.card))}</p><a class="decide-title" href="#/card/${item.number}">${e(item.card.title)}</a><h2 tabindex="-1" data-question="${item.number}">${e(item.question || item.card.ask || "")}</h2></article><div class="decide-actions">${buttons}${replyRow(item, draft, disabled, options.length === 0)}</div>`;
}

function doneHTML(session) {
  const answered = session.items.filter((item) => ["sent", "hold", "sending"].includes(item.kind)).length;
  const later = session.items.filter((item) => item.kind === "later").length;
  const heading = later ? `<span>${e(COPY.answered(answered))}</span><span>${e(COPY.forLater(later))}</span>` : `<span>${e(COPY.allAnswered(answered))}</span>`;
  const rows = session.items.filter((item) => ["sent", "hold", "sending", "later", "elsewhere"].includes(item.kind)).map((item) => {
    const ok = item.kind !== "later";
    const text = item.kind === "later" ? COPY.later : item.kind === "elsewhere" ? COPY.answeredLine : shown(item);
    return `<li><i class="decide-dot ${ok ? "ok" : "now"}" aria-hidden="true"></i><span class="num">#${item.number}</span><span>${e(text)}</span></li>`;
  }).join("");
  const show = later ? `<button type="button" class="opt" data-action="queue-show-later">${e(COPY.showLater(later))}</button>` : "";
  return `<div class="decide-done"><h2 class="done-title">${heading}</h2><ol>${rows}</ol></div><div class="decide-actions">${statusHTML(session)}${show}<a class="opt go" href="#/">${COPY.backHome}</a></div>`;
}

export function renderDecide(session, opts = {}) {
  const drafts = opts.drafts || {};
  let main = "";
  if (session.phase === "loading") main = `${statusHTML(session)}<article class="decide-card"><span class="cursor" aria-hidden="true"></span></article>`;
  else if (session.phase === "done") main = doneHTML(session);
  else if (session.phase === "queue") main = questionHTML(session, drafts);
  else return "";
  return `<section class="decide" data-phase="${e(session.phase)}">${bar(session)}${listHTML(session)}<div class="decide-main">${main}</div></section>`;
}
