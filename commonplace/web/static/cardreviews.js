import { api as request, escape as e, elapsed } from "./api.js";

// "Review comments" in the card panel: what reviewers wrote on the card's pull request and what
// the card's Coworker did with each comment, from GET /api/pr-review/cards/<n>. Every status,
// reason and next step is the server's own words (status_text, reason_text, next); this file
// only draws them. A card without a pull request has no section; a card the person cannot see
// answers 404 and the section stays hidden. The owner answers comments that need a person here;
// everyone else reads.
//
// Focus and announcements, as in the Previews section: the list is drawn into its own body
// element, results are said once in one status line that stays in place outside that body, and
// a redraw (an action, or the reload while the Coworker works) keeps focus on the same control
// by its data-review-* key, or moves it where the action asked.

// Controls that keep their identity across a redraw (also listed in the shell's focus keys).
export const FOCUS_KEYS = ["data-review-heading", "data-review-retry", "data-review-answer", "data-review-more", "data-review-original", "data-review-expand", "data-review-settings"];

const TONES = { new: "wait", working: "wait", addressed: "ok", answered: "ok", needs_you: "bad", skipped: "quiet" };
const KIND_WORDS = { inline: "Line comment", review: "Review", conversation: "Comment" };
const SUMMARY_WORDS = { addressed: "fixed", answered: "answered", needs_you: "need you", new: "waiting", working: "being worked on", skipped: "not for the bot" };
const LONG = 400;
const PAGE = 50;
const POLL_MS = 5000;
const LOADING = '<p class="run-empty" data-review-loading>Loading review comments…</p>';

const sentence = (value) => value ? `${value[0].toUpperCase()}${value.slice(1)}${/[.!?]$/.test(value) ? "" : "."}` : "";
const networkError = (error) => !error?.status;
const ago = (at) => {
  const value = elapsed(at);
  return value === "now" ? "just now" : value === "—" ? "" : `${value} ago`;
};
const when = (at) => at ? `<time datetime="${e(at)}" title="${e(at)}">${e(ago(at) || at)}</time>` : "";
const SHA = /^[0-9a-f]{40}(?:[0-9a-f]{24})?$/;
const REPO = /^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+$/;

export const refusalWords = (error) => {
  const payload = error?.payload || {};
  const text = String(payload.error || "").replace(/^no: /, "");
  if (text) return [sentence(text), sentence(String(payload.next || ""))].filter(Boolean).join(" ");
  if (error?.status === 404) return "This card no longer exists, or you cannot open it.";
  if (error?.status === 403) return "Only a workspace owner can decide this.";
  return networkError(error) ? "That did not reach the server. Check the connection and try again." : "That did not work on the server. Try again.";
};

// "5 comments: 3 fixed, 1 answered, 1 need you"
export function summaryWords(counts = {}) {
  const total = Object.values(counts).reduce((sum, n) => sum + (Number(n) || 0), 0);
  if (!total) return "";
  const parts = Object.keys(SUMMARY_WORDS).filter((key) => counts[key]).map((key) => `${counts[key]} ${SUMMARY_WORDS[key]}`);
  return `${total} ${total === 1 ? "comment" : "comments"}: ${parts.join(", ")}`;
}

// What the person can expect from the loop for this pull request, in one or two sentences.
export function roundWords(info) {
  if (!info) return "";
  if (info.loop_stopped) return "A person is handling this pull request's comments; the bot does not answer them.";
  const used = Number(info.rounds_used) || 0, allowed = Number(info.rounds_allowed) || 0;
  const head = `Round ${used} of ${allowed} used.`;
  return used >= allowed ? `${head} No rounds are left: new comments wait for a workspace owner's answer.` : head;
}

export function replyWords(info) {
  if (!info) return "";
  return info.reply_mode === "post"
    ? "The bot posts its replies on the pull request."
    : "Replies are shown here first and are not posted on GitHub: this repository is public, and posting is switched off in this project's settings.";
}

const commitLink = (repo, sha) => REPO.test(repo || "") && SHA.test(sha || "") ? `<a href="https://github.com/${e(repo)}/commit/${e(sha)}" target="_blank" rel="noopener noreferrer">${e(sha.slice(0, 7))}</a>` : e(String(sha || "").slice(0, 7));

function textHTML(comment, { expanded, original }) {
  const shown = original ?? comment.text ?? "";
  const long = shown.length > LONG && !expanded;
  const body = long ? `${shown.slice(0, LONG)}…` : shown;
  const more = shown.length > LONG ? `<button class="btn sm" type="button" data-review-expand="${e(comment.id)}" aria-expanded="${expanded ? "true" : "false"}">${expanded ? "Show less" : "Show all of it"}</button>` : "";
  const hidden = comment.text_redacted && original == null
    ? `<p class="run-reason" data-review-hidden>Something in this comment looked like a password or key, so it is hidden here.</p><button class="btn sm" type="button" data-review-original="${e(comment.id)}">Show the original words</button>`
    : "";
  return `<p class="review-text" data-review-text>${e(body)}</p>${more}${hidden}`;
}

export function commentHTML(comment, repo, state = {}) {
  const status = TONES[comment.status] ? comment.status : "new";
  const where = comment.path ? ` on <code>${e(comment.path)}${comment.line ? `:${e(comment.line)}` : ""}</code>` : "";
  const fix = comment.fix_commit
    ? `<p class="run-reason" data-review-fix>Fixed in commit ${commitLink(repo, comment.fix_commit)}${comment.fix_confirmed ? "" : ". Waiting for GitHub to show that commit on the pull request."}</p>`
    : "";
  const reply = comment.reply
    ? `<div class="review-reply" data-review-reply><p class="run-reason"><b>${comment.reply_posted ? "Reply on GitHub" : "Proposed reply (not posted)"}</b></p><p class="review-text">${e(comment.reply)}</p></div>`
    : "";
  const note = comment.note ? `<p class="run-reason" data-review-note><b>The bot says:</b> ${e(comment.note)}</p>` : "";
  const why = comment.reason_text && ["needs_you", "skipped", "new"].includes(status)
    ? `<p class="run-reason" data-review-reason>${e(comment.reason_text)}${comment.next ? ` ${e(comment.next)}` : ""}</p>`
    : "";
  return `<li data-review-comment="${e(comment.id)}" data-state="${e(status)}" data-tone="${TONES[status]}"><p class="run-line"><span class="tag run-state">${e(comment.status_text || status)}</span><span>${e(KIND_WORDS[comment.kind] || "Comment")} by <b>${e(comment.author)}</b>${where}</span>${comment.created_at ? `<span>· ${when(comment.created_at)}</span>` : ""}${comment.rev > 1 ? "<span>· edited</span>" : ""}</p>${textHTML(comment, state)}${why}${note}${fix}${reply}</li>`;
}

function decisionHTML(view) {
  const waiting = Number(view?.counts?.needs_you) || 0;
  if (!waiting) return "";
  const head = `<p class="run-reason" data-review-decision-head><b>${waiting === 1 ? "1 comment needs you." : `${waiting} comments need you.`}</b> Choose what happens next.</p>`;
  if (!view.can_decide || !Array.isArray(view.answers) || !view.answers.length) {
    return `<div class="review-decision" data-review-decision>${head.replace("Choose what happens next.", "A workspace owner chooses what happens next.")}</div>`;
  }
  const choices = view.answers.map((answer) => `<div class="review-answer"><button class="btn sm" type="button" data-review-answer="${e(answer.choice)}">${e(answer.label)}</button><p class="run-reason">${e(answer.explain)}</p></div>`).join("");
  return `<div class="review-decision" role="group" aria-label="What happens to the comments that need you" data-review-decision>${head}${choices}</div>`;
}

function offHTML(view) {
  const settings = `#/board/${encodeURIComponent(view.board || "")}/settings`;
  const text = "The bot does not answer review comments in this project yet.";
  if (view.can_decide && view.board) {
    return `<p class="run-empty" data-review-off>${text} You can switch it on in this project's settings, under Review comments.</p><a class="btn sm" href="${e(settings)}" data-review-settings="open">Open project settings</a>`;
  }
  return `<p class="run-empty" data-review-off>${text} A workspace owner can switch it on.</p>`;
}

const originalKey = (comment) => `${comment.id}/${comment.rev}`;

// The list part of the section for a loaded answer (the status line is separate).
export function cardReviewsHTML(view, state = {}) {
  const comments = Array.isArray(view?.comments) ? view.comments : [];
  const info = view?.pr_info;
  const lines = [summaryWords(view?.counts), roundWords(info), info ? replyWords(info) : ""].filter(Boolean);
  const facts = lines.length ? `<p class="run-reason" data-review-summary>${lines.map(e).join(" ")}</p>` : "";
  const off = view?.loop && !view.loop.enabled ? offHTML(view) : "";
  const list = comments.length
    ? `<ul class="run-list">${comments.map((comment) => commentHTML(comment, view.repo, { expanded: state.expanded?.has(String(comment.id)), original: state.originals?.get(originalKey(comment)) })).join("")}</ul>`
    : `<p class="run-empty" data-review-empty>No review comments yet. When someone comments on pull request #${e(view?.pr)}, the comments and what the bot did with each appear here.</p>`;
  const more = view?.has_more ? `<button class="btn sm" type="button" data-review-more="more">Show more comments (${e(comments.length)} of ${e(view.comments_total)} shown)</button>` : "";
  return `${off}${facts}${decisionHTML(view)}${list}${more}`;
}

export const offlineWords = (error) => networkError(error) ? "Could not load the review comments. Check the connection and try again." : "Could not load the review comments. Try again.";
export const offlineHTML = (error) => `<p class="run-empty" data-review-offline>${e(offlineWords(error))}</p><button class="btn sm" type="button" data-review-retry="load">Try again</button>`;

const HEAD = `<div class="sec-h"><h3 tabindex="-1" data-review-heading="reviews">Review comments</h3></div>`;
const keyOf = (card) => `${card.board}/${card.number}`;

// The one insertion renderDetails makes: an empty, hidden section that attachCardReviews fills.
// Only a card with a pull request gets it.
export const cardReviewsSlot = (card) => card?.pr_url || card?.pr_number ? `<section class="card-reviews" data-card-reviews data-card-key="${e(keyOf(card))}" aria-label="Review comments" hidden>${HEAD}<div data-review-body></div><p class="run-note" role="status" aria-live="polite" data-review-status></p></section>` : "";

const hasList = (html) => /data-review-comment="|data-review-empty|data-review-off/.test(html || "");

export function focusedKey(scope, doc = globalThis.document) {
  const active = doc?.activeElement;
  if (!active || !scope?.contains?.(active)) return null;
  for (const name of FOCUS_KEYS) {
    if (active.hasAttribute?.(name)) return `[${name}="${String(active.getAttribute(name)).replace(/["\\]/g, "\\$&")}"]`;
  }
  return null;
}

// What the panel showed before a redraw, so the same card's list does not blink away and back.
export function previousReviews(mount) {
  const slot = mount?.querySelector?.("[data-card-reviews]");
  const body = slot?.querySelector?.("[data-review-body]");
  if (!slot || slot.hidden || !body?.innerHTML) return null;
  return { key: slot.dataset?.cardKey, html: body.innerHTML, focus: focusedKey(slot) };
}

const ANSWER_DONE = {
  again: "Done. The comments go back to the bot for another round.",
  mine: "Done. The bot stops answering these comments and the card moved to Backlog.",
  dismiss: "Done. The waiting comments are set aside.",
};

function decisionWords(choice, result) {
  if (result?.card_answered === true) return ANSWER_DONE[choice] || "Done.";
  const comment = {
    again: "Done. The comments are ready for another round.",
    mine: "Done. The bot stops answering these comments.",
    dismiss: ANSWER_DONE.dismiss,
  }[choice] || "Done.";
  if (result?.card_answer_reason === "unrelated_question") {
    return `${comment} The card still has another question; answer it on the card before continuing.`;
  }
  if (result?.card_answer_reason === "card_refused") {
    return `${comment} The answer to the card's question could not be saved. The card still has that question; answer it on the card before continuing.`;
  }
  if (result?.card_question_pending) {
    return `${comment} The card still has a question; answer it on the card before continuing.`;
  }
  return `${comment} The card's move was not confirmed. Check its status on the card before continuing.`;
}

// Load the card's review comments into the slot inside `mount` and wire the answers.
export async function attachCardReviews(mount, card, { api = request, previous = null, schedule = (fn, ms) => setTimeout(fn, ms) } = {}) {
  const slot = mount.querySelector?.("[data-card-reviews]");
  if (!slot || card?.number == null) return;
  const body = slot.querySelector?.("[data-review-body]");
  const status = slot.querySelector?.("[data-review-status]");
  if (!body || !status) return;
  const key = keyOf(card);
  const state = { expanded: new Set(), originals: new Map() };
  let revision = 0, last = null, wanted = null, polling = false, limit = PAGE;
  const live = (token) => {
    if (!slot.isConnected) {
      if (body.innerHTML === LOADING) body.innerHTML = "";
      return false;
    }
    return token === revision;
  };
  const say = (note) => { status.textContent = note || ""; };
  const focus = (selector) => {
    const target = selector && slot.querySelector?.(selector);
    if (!target?.focus) return false;
    target.focus({ preventScroll: true });
    return globalThis.document?.activeElement === target;
  };
  const paint = (html) => {
    const had = focusedKey(slot);
    body.innerHTML = html;
    slot.hidden = false;
    const target = wanted || had;
    wanted = null;
    if (target && !focus(target)) focus('[data-review-heading="reviews"]');
  };
  const show = (view, note) => {
    const changedPR = last && (last.repo !== view.repo || last.pr !== view.pr);
    const current = new Set((view.comments || []).map(originalKey));
    let discarded = false;
    for (const cached of state.originals.keys()) {
      if (changedPR || !current.has(cached)) { state.originals.delete(cached); discarded = true; }
    }
    last = view;
    paint(cardReviewsHTML(view, state));
    if (note !== undefined) say(note);
    else if (discarded) say("The comments changed. Choose Show the original words again to read the current comment.");
    // While the Coworker works on comments, or a fix waits for GitHub, the card follows by itself.
    const busy = (view?.comments || []).some((c) => c.status === "working");
    if (busy && !polling) {
      polling = true;
      schedule(() => { polling = false; if (slot.isConnected) void load(); }, POLL_MS);
    }
  };
  const painted = previous?.key === key && hasList(previous.html);
  if (painted) {
    body.innerHTML = previous.html;
    slot.hidden = false;
    focus(previous.focus);
  } else {
    paint(LOADING);
  }
  async function load(note) {
    const token = ++revision;
    try {
      const view = await api(`/api/pr-review/cards/${encodeURIComponent(card.number)}?limit=${limit}`);
      if (!live(token)) return;
      if (!view?.repo || !view?.pr) { last = null; slot.hidden = true; body.innerHTML = ""; say(""); return; }
      show(view, note);
    } catch (error) {
      if (!live(token)) return;
      if ([403, 404].includes(error?.status)) { last = null; slot.hidden = true; body.innerHTML = ""; say(""); return; }
      if (last) { show(last, note ?? "Could not refresh the review comments. Showing the last list."); return; }
      if (hasList(body.innerHTML)) { say(note ?? "Could not refresh the review comments. Showing the last list."); return; }
      paint(offlineHTML(error));
      say(note ?? offlineWords(error));
    }
  }
  async function answer(trigger, choice) {
    const group = trigger.closest?.("[data-review-decision]");
    [...(group?.querySelectorAll?.("button") || [])].forEach((button) => { button.disabled = true; });
    say("Saving your answer…");
    let note;
    try {
      const result = await api(`/api/pr-review/cards/${encodeURIComponent(card.number)}/decision`, { method: "POST", body: { choice } });
      note = decisionWords(choice, result);
      wanted = '[data-review-heading="reviews"]';
    } catch (error) {
      note = refusalWords(error);
      wanted = `[data-review-answer="${choice}"]`;
    }
    await load(note);
  }
  async function original(trigger, id) {
    const comment = last?.comments?.find((item) => String(item.id) === String(id));
    if (!comment || !slot.isConnected) return;
    const cached = originalKey(comment), expectedRev = comment.rev, repo = last.repo, pr = last.pr;
    const current = () => slot.isConnected && last?.repo === repo && last.pr === pr
      && last.comments?.some((item) => originalKey(item) === cached);
    trigger.disabled = true;
    try {
      const found = await api(`/api/pr-review/cards/${encodeURIComponent(card.number)}/comments/${encodeURIComponent(id)}/original`);
      if (!current()) return;
      if (String(found?.id) !== String(id) || found?.rev !== expectedRev) {
        trigger.disabled = false;
        await load("This comment changed. Choose Show the original words again to read its current revision.");
        return;
      }
      state.originals.set(cached, String(found?.text ?? ""));
      wanted = `[data-review-expand="${id}"]`;
      if (last) show(last, "Showing the original words.");
      if (!focus(wanted)) focus(`[data-review-comment="${id}"] [data-review-text]`) || focus('[data-review-heading="reviews"]');
    } catch (error) {
      if (!current()) return;
      trigger.disabled = false;
      say(refusalWords(error));
    }
  }
  slot.addEventListener("click", (event) => {
    const choice = event.target.closest?.("[data-review-answer]");
    if (choice) { void answer(choice, choice.dataset.reviewAnswer); return; }
    const expand = event.target.closest?.("[data-review-expand]");
    if (expand) {
      const id = expand.dataset.reviewExpand;
      if (state.expanded.has(id)) state.expanded.delete(id); else state.expanded.add(id);
      wanted = `[data-review-expand="${id}"]`;
      if (last) show(last);
      return;
    }
    const shown = event.target.closest?.("[data-review-original]");
    if (shown) { void original(shown, shown.dataset.reviewOriginal); return; }
    if (event.target.closest?.("[data-review-more]")) {
      limit = Math.min(limit + PAGE, 500);
      wanted = '[data-review-more="more"]';
      void load("Showing more comments.");
      return;
    }
    if (event.target.closest?.("[data-review-retry]")) { wanted = '[data-review-heading="reviews"]'; void load(); }
  });
  await load();
}
