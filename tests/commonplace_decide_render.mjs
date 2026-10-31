import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const url = (source) => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const read = (name) => readFile(new URL(`../commonplace/web/static/${name}`, import.meta.url), "utf8");
const apiUrl = url(await read("api.js"));
const decide = await import(url((await read("decide.js")).replace('"./api.js"', `"${apiUrl}"`)));
const {
  COPY, HOLD_SECONDS, RELOAD_ERRORS, answerPayload, decideQueue, hasQuestions, offlineNow,
  queueKey, createSession, reduce, renderDecide, splitChoice, meterHTML,
} = decide;

const at = "2026-10-31T08:00:00Z";
const card = (number, fields) => ({
  number, board: "lab", title: `Question ${number}`, status: "review", need: "you",
  ask: `Question ${number}?`, ask_options: [`Keep ${number} (advised)`, `Change ${number}`],
  return_to: "conn:ada", version: 1, updated_at: at, created_at: `2026-10-31T0${number}:00:00Z`,
  ...fields,
});
const cards = [card(1), card(2), card(3), card(4), card(5)];

assert.deepEqual(answerPayload(cards[0], 0, "  note  "), {
  version: 1, then: "return", choice: "Keep 1 (advised)", reply: "note",
});
assert.deepEqual(answerPayload(cards[0], 7, "   "), { version: 1, then: "return" });
assert.deepEqual(answerPayload({ ...cards[0], ask_options: [] }, null, "only words"), {
  version: 1, then: "return", reply: "only words",
});
assert.deepEqual(splitChoice("Keep 1 (advised)"), { label: "Keep 1", advised: true });
assert.deepEqual(splitChoice("Keep 1 (advies)"), { label: "Keep 1", advised: true });
assert.deepEqual(splitChoice("Change 1"), { label: "Change 1", advised: false });
assert.equal(splitChoice("(advised)").advised, false);

const older = card(9, { created_at: "2026-10-30T00:00:00Z" });
const queued = decideQueue({ asks: [cards[2], older, cards[0]], done_review: [cards[0], card(8, { ask: "Done?" })] });
assert.deepEqual(queued.map((item) => item.number), [9, 1, 3, 8]);
assert.equal(hasQuestions({ asks: [cards[0]] }), true);
assert.equal(hasQuestions({ ready_to_merge: [cards[0]] }), false);
assert.equal(offlineNow(false, 1), true);
assert.equal(offlineNow(true, 1), false);
assert.equal(offlineNow(true, 2), true);
assert.equal(queueKey("1", true), null);
assert.equal(queueKey("j", false).type, "move");
assert.equal(queueKey("u", false).type, "undo");
assert.equal(queueKey("Escape", false).type, "home");
assert.ok(RELOAD_ERRORS.has("card is not in review"));

function step(session, event) {
  const next = reduce(session, event);
  return next;
}
function load(list = cards, me = "user:me") {
  return step(createSession(null, me), { type: "load", cards: list }).session;
}

let session = load();
let moved = step(session, { type: "answer", optionIndex: 0, reply: "note" });
assert.equal(moved.effects.length, 0);
assert.equal(moved.session.items[0].kind, "hold");
assert.equal(moved.session.cursor, 1);
assert.equal(moved.session.items[0].pending.choice, "Keep 1 (advised)");
session = moved.session;
for (let i = 0; i < HOLD_SECONDS - 1; i += 1) {
  const tick = step(session, { type: "tick" });
  assert.equal(tick.effects.length, 0);
  session = tick.session;
}
const sent = step(session, { type: "tick" });
assert.equal(sent.effects.length, 1);
assert.equal(sent.effects[0].type, "post");
assert.equal(sent.effects[0].body.then, "return");
assert.equal(sent.effects[0].body.choice, "Keep 1 (advised)");
assert.equal(sent.effects[0].body.reply, "note");
assert.equal(step(sent.session, { type: "flush" }).effects.length, 0);

session = load();
session = step(session, { type: "answer", optionIndex: 0, reply: "note" }).session;
const undone = step(session, { type: "undo" });
assert.equal(undone.effects.length, 0);
assert.equal(undone.session.cursor, 0);
assert.equal(undone.session.items[0].kind, "open");
assert.equal(undone.session.items[0].reply, "note");
const restored = renderDecide(undone.session, { drafts: { "reply-1": "note" } });
assert.match(restored, /value="note"/);
assert.doesNotMatch(restored, /data-action="queue-undo"/);

session = load();
session = step(session, { type: "answer", optionIndex: 0 }).session;
const second = step(session, { type: "answer", optionIndex: 1, reply: "next" });
assert.equal(second.effects.filter((effect) => effect.type === "post").length, 1);
assert.equal(second.effects[0].number, 1);
assert.equal(second.session.items[1].kind, "hold");
assert.equal(second.session.cursor, 2);

session = load();
session = step(session, { type: "answer", optionIndex: 0 }).session;
const flushed = step(session, { type: "flush" });
assert.equal(flushed.effects.length, 1);
assert.equal(flushed.effects[0].type, "post");
assert.equal(step(flushed.session, { type: "flush" }).effects.length, 0);

session = load();
session = step(session, { type: "later" }).session;
assert.equal(session.items[0].kind, "later");
assert.equal(session.cursor, 1);
for (const item of session.items.slice(1)) session = step(session, { type: "later" }).session;
assert.equal(session.phase, "done");
const done = renderDecide(session);
assert.match(done, /0 answered\./);
assert.match(done, /5 for later\./);
assert.match(done, /Show the 5 for later/);
assert.match(done, /Back to Home/);
const resumed = step(session, { type: "show-later" });
assert.equal(resumed.effects.length, 0);
assert.equal(resumed.session.phase, "queue");
assert.ok(resumed.session.items.every((item) => item.kind === "open"));

session = load();
session = step(session, { type: "offline", offline: true }).session;
const blocked = step(session, { type: "answer", optionIndex: 0, reply: "nope" });
assert.equal(blocked.effects.length, 0);
assert.equal(blocked.session.items[0].kind, "open");
const offline = renderDecide(blocked.session);
assert.match(offline, new RegExp(COPY.offline));
assert.match(offline, /data-action="queue-answer"[^>]*disabled/);
assert.match(offline, /data-action="queue-later"/);
assert.doesNotMatch(offline.slice(offline.indexOf("queue-later")), /^[^<]*disabled/);
const parked = step(blocked.session, { type: "later" });
assert.equal(parked.effects.length, 0);
assert.equal(parked.session.items[0].kind, "later");

session = load();
session = step(session, { type: "answer", optionIndex: 0 }).session;
const failed = step(session, { type: "result", number: 1, status: 500 });
assert.equal(failed.session.cursor, 1);
assert.equal(failed.session.items[0].kind, "open");
assert.equal(failed.session.replay[0], 1);
assert.match(renderDecide(failed.session), /#1 not sent/);
assert.match(renderDecide(failed.session), /data-action="queue-retry"/);
assert.match(meterHTML(failed.session), /<i class="done"><\/i><i class="now">/);

const elsewhereCard = { ...cards[0], status: "todo", ask: null, events: [{ kind: "decide", actor: "user:sam", choice: "Change 1", text: null }] };
session = load();
session = step(session, { type: "answer", optionIndex: 0 }).session;
const elsewhere = step(session, { type: "result", number: 1, status: 409, card: elsewhereCard });
assert.equal(elsewhere.effects.filter((effect) => effect.type === "post").length, 0);
assert.equal(elsewhere.session.items[0].kind, "elsewhere");
assert.equal(elsewhere.session.cursor, 0);
const stale = renderDecide(elsewhere.session);
assert.match(stale, /#1 was answered elsewhere/);
assert.match(stale, /Next question/);
assert.doesNotMatch(stale, /data-action="queue-answer"/);

const changedCard = { ...cards[0], ask: "A new question?", ask_options: ["Now"], version: 4, events: [] };
session = load();
const changed = step(session, { type: "result", number: 1, status: 409, card: changedCard });
assert.equal(changed.effects.length, 0);
assert.equal(changed.session.items[0].question, "A new question?");
assert.equal(changed.session.notice.kind, "changed");
assert.match(renderDecide(changed.session), /The question changed\./);
assert.match(renderDecide(changed.session), /data-action="queue-answer"/);

const lost = { ...cards[0], status: "todo", ask: null, events: [{ kind: "decide", actor: "user:me", choice: "Keep 1 (advised)", text: "note" }] };
session = load();
session = step(session, { type: "answer", optionIndex: 0, reply: "note" }).session;
const recovered = step(session, { type: "result", number: 1, status: 409, card: lost });
assert.equal(recovered.session.items[0].kind, "sent");
assert.equal(recovered.session.notice, null);

session = load();
const dropped = step(session, { type: "result", number: 1, status: 403, error: "not allowed" });
assert.equal(dropped.session.items.length, 4);
assert.match(renderDecide(dropped.session), /not allowed/);
assert.doesNotMatch(renderDecide(dropped.session), /Question 1\?/);

const reviewed = { ...cards[0], status: "todo", ask: null, events: [] };
session = load();
const reloaded = step(session, { type: "result", number: 1, status: 403, error: "card is not in review", card: reviewed });
assert.equal(reloaded.session.items[0].kind, "elsewhere");

session = load([cards[0], cards[1]]);
session = step(session, { type: "move", delta: 1 }).session;
const quiet = step(session, { type: "card", number: 1, card: reviewed });
assert.equal(quiet.session.cursor, 1);
assert.equal(quiet.session.items[0].kind, "elsewhere");

const added = step(load([cards[0]]), { type: "sync", cards: [cards[0], cards[4]] });
assert.equal(added.session.cursor, 0);
assert.deepEqual(added.session.items.map((item) => item.number), [1, 5]);

const many = Array.from({ length: 13 }, (_, index) => card(index + 1));
const wide = step(createSession(), { type: "load", cards: many }).session;
assert.match(meterHTML(wide), /1\/13/);
assert.doesNotMatch(meterHTML(wide), /<i /);

const empty = step(createSession(), { type: "load", cards: [] });
assert.equal(empty.session.phase, "empty");
assert.deepEqual(empty.effects, [{ type: "home" }]);

const slow = step(createSession(), { type: "slow" }).session;
const waiting = renderDecide(slow);
assert.match(waiting, /class="cursor"/);
assert.match(waiting, /The server did not answer/);
assert.match(waiting, /data-action="retry"/);
assert.doesNotMatch(waiting, /data-action="queue-answer"/);

const advised = renderDecide(load([card(1, { ask_options: ["Keep the old name (advised)", "Rename it"] })]));
assert.match(advised, /aria-label="Keep the old name, advised"/);
assert.match(advised, /class="opt go"/);
assert.match(advised, /<span>Keep the old name<\/span><span class="advise">advised<\/span>/);
const free = renderDecide(load([card(1, { ask_options: [] })]));
assert.doesNotMatch(free, /data-action="queue-answer"/);
assert.match(free, /data-action="queue-later"/);
assert.match(free, /required/);

const css = await read("decide.css");
assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/);
assert.match(css, /min-height: 56px/);
assert.match(css, /min-height: 52px/);
assert.match(css, /min-height: 44px/);
assert.match(css, /120ms/);
assert.match(css, /env\(safe-area-inset-bottom\)/);
assert.match(css, /100dvh/);
assert.match(css, /prefers-reduced-motion: reduce/);
assert.match(css, /animation: none/);
