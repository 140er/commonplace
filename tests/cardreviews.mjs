import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const load = (name) => import(new URL(`../commonplace/web/static/${name}`, import.meta.url));
const { cardReviewsHTML, cardReviewsSlot, attachCardReviews, previousReviews, focusedKey, refusalWords, offlineWords, summaryWords, roundWords, replyWords, commentHTML, FOCUS_KEYS } = await load('cardreviews.js');
const settings = await load('reviewsettings.js');
const { renderDetails, createDetails } = await load('details.js');
const pipeline = await load('deck-pipeline.js');
const card = { number: 7, board: 'atlas', version: 2, title: 'Count things', body: '', status: 'doing', events: [], pr_url: 'https://github.com/acme/widgets/pull/7', pr_number: 7 };
const SHA = 'b'.repeat(40);
const comment = (extra = {}) => ({
  id: 1, kind: 'inline', gh_id: '101', path: 'app.py', line: 1, author: 'reviewer1', text: 'Rename x to count.', text_redacted: false,
  created_at: '2026-11-20T08:00:00Z', rev: 1, status: 'new', status_text: 'New', round: 0, fix_commit: null, fix_confirmed: false,
  reply: null, reply_posted: false, note: null, reason: null, reason_text: '', next: '', ...extra,
});
const answers = [
  { choice: 'again', label: 'Two more rounds', explain: 'The Coworker gets two more rounds on these comments and on any that still wait.' },
  { choice: 'mine', label: 'I will do it myself', explain: 'The Coworker stops answering this pull request\'s comments. The card moves to Backlog, so no Coworker picks it up while you work on them.' },
  { choice: 'dismiss', label: 'Ignore', explain: 'The waiting comments are set aside and nobody acts on them. The card goes back to its Coworker.' },
];
const view = (comments, extra = {}) => {
  const counts = { new: 0, working: 0, addressed: 0, answered: 0, needs_you: 0, skipped: 0 };
  for (const c of comments) counts[c.status] += 1;
  return {
    card: 7, board: 'atlas', repo: 'acme/widgets', pr: 7, loop: { enabled: true, rounds: 2 }, can_decide: true, answers,
    pr_info: { head: SHA, state: 'open', fetched_at: '2026-11-20T08:05:00Z', rounds_used: 1, rounds_allowed: 2, loop_stopped: false, public: true, reply_mode: 'show' },
    counts, comments, comments_total: comments.length, has_more: false, history: [], ...extra,
  };
};
const wait = () => new Promise((done) => setTimeout(done, 0));

globalThis.document = { activeElement: null };
function fakeSlot() {
  const handlers = {};
  const body = { innerHTML: '' };
  const log = [];
  const status = { _text: '', log, get textContent() { return this._text; }, set textContent(value) { this._text = value; log.push(value); } };
  const element = (name, value) => ({
    name, value, disabled: false, focus() { globalThis.document.activeElement = this; },
    hasAttribute: (attr) => attr === name, getAttribute: (attr) => attr === name ? value : null,
  });
  const slot = {
    hidden: true, isConnected: true, dataset: { cardKey: 'atlas/7' }, body, status,
    addEventListener: (type, fn) => { handlers[type] = fn; },
    contains: (el) => Boolean(el && el.inSlot === slot),
    querySelector(selector) {
      if (selector === '[data-review-body]') return body;
      if (selector === '[data-review-status]') return status;
      const match = selector.match(/^\[(data-review-[a-z-]+)="([^"]*)"\]$/);
      if (!match) return null;
      const [, name, value] = match;
      if (name !== 'data-review-heading' && !body.innerHTML.includes(`${name}="${value}"`)) return null;
      const el = element(name, value); el.inSlot = slot; return el;
    },
  };
  return { slot, handlers, body, status, mount: { querySelector: (selector) => selector === '[data-card-reviews]' ? slot : null } };
}
const clickOn = (attr, id) => {
  const button = { dataset: { [attr]: id }, disabled: false, closest: () => ({ querySelectorAll: () => [] }) };
  const name = `[data-${attr.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}]`;
  return { target: { closest: (selector) => selector === name ? button : null } };
};
const focused = () => { const a = globalThis.document.activeElement; return a ? `${a.name}=${a.value}` : null; };
async function partialAnswer(reason) {
  for (const choice of ['again', 'mine', 'dismiss']) {
    const { status, handlers, mount } = fakeSlot();
    let current = view([ALL[4]]);
    await attachCardReviews(mount, card, { api: async (url, init) => {
      if (init?.method === 'POST') {
        current = view([]);
        return { choice, card_answered: false, card_answer_reason: reason, card_question_pending: true };
      }
      return current;
    }, schedule: () => {} });
    handlers.click(clickOn('reviewAnswer', choice));
    await wait(); await wait();
    const text = status.textContent;
    assert.ok(!text.includes('card moved to Backlog'), text);
    assert.ok(text.includes('The card still has'), text);
    assert.ok(text.includes('answer it on the card'), text);
    if (reason === 'unrelated_question') assert.ok(text.includes('another question'), text);
    if (reason === 'card_refused') assert.ok(text.includes('could not be saved'), text);
    assert.equal(focused(), 'data-review-heading=reviews');
  }
}
const ALL = [
  comment(),
  comment({ id: 2, status: 'working', status_text: 'The Coworker is working on it', round: 1 }),
  comment({ id: 3, status: 'addressed', status_text: 'Fixed', fix_commit: SHA, fix_confirmed: true, reply: 'Renamed.', round: 1 }),
  comment({ id: 4, kind: 'conversation', path: null, line: null, status: 'answered', status_text: 'Answered', reply: 'It is read by two functions.', reply_posted: true }),
  comment({ id: 5, status: 'needs_you', status_text: 'Needs you', reason: 'coworker_asks', reason_text: 'The Coworker needs a decision it cannot make.', next: 'Read its note and decide.', note: 'Choosing a language is a product decision.' }),
  comment({ id: 6, status: 'skipped', status_text: 'Not for the Coworker', reason: 'author_not_trusted', reason_text: 'Written by someone the workspace does not act on.', next: 'Add them as a trusted reviewer, or ignore.' }),
];

const cases = {
  async loading() {
    const f = fakeSlot();
    let loaded;
    const pending = attachCardReviews(f.mount, card, { api: () => new Promise((resolve) => { loaded = resolve; }) });
    assert.equal(f.slot.hidden, false);
    assert.ok(f.body.innerHTML.includes('Loading review comments…'));
    assert.equal(f.status.textContent, '');
    loaded(view([]));
    await pending;
    assert.ok(f.body.innerHTML.includes('No review comments yet.') && !f.body.innerHTML.includes('data-review-loading'));
  },
  states() {
    const html = cardReviewsHTML(view(ALL));
    for (const text of ['New', 'The Coworker is working on it', 'Fixed', 'Answered', 'Needs you', 'Not for the Coworker',
      'Fixed in commit', `href="https://github.com/acme/widgets/commit/${SHA}"`, '>bbbbbbb<', 'Proposed reply (not posted)', 'Reply on GitHub',
      'The Coworker says:', 'Choosing a language is a product decision.', 'The Coworker needs a decision it cannot make. Read its note and decide.',
      'Written by someone the workspace does not act on. Add them as a trusted reviewer, or ignore.', 'Line comment by <b>reviewer1</b> on <code>app.py:1</code>',
      'Comment by <b>reviewer1</b>', '6 comments: 1 fixed, 1 answered, 1 need you, 1 waiting, 1 being worked on, 1 not for the Coworker',
      'Round 1 of 2 used.', 'Replies are shown here first and are not posted on GitHub']) {
      assert.ok(html.includes(text), `missing: ${text}`);
    }
    assert.ok(!/feeder|bundle|orchestrator/i.test(html));
  },
  waiting() {
    // A fix GitHub does not show yet says so; a comment back to new says why.
    const html = cardReviewsHTML(view([comment({ status: 'working', status_text: 'The Coworker is working on it', fix_commit: SHA, fix_confirmed: false }), comment({ id: 2, reason: 'run_failed', reason_text: 'The run ended without reporting every comment.', next: 'The comments return to new once; after the second time they ask a person.' })]));
    assert.ok(html.includes('Waiting for GitHub to show that commit on the pull request.'));
    assert.ok(html.includes('The run ended without reporting every comment.'));
  },
  decision() {
    const html = cardReviewsHTML(view([ALL[4]]));
    assert.ok(html.includes('1 comment needs you.') && html.includes('Choose what happens next.'));
    assert.deepEqual([...html.matchAll(/data-review-answer="([^"]+)">([^<]+)</g)].map((m) => [m[1], m[2]]), [['again', 'Two more rounds'], ['mine', 'I will do it myself'], ['dismiss', 'Ignore']]);
    for (const answer of answers) assert.ok(html.includes(answer.explain.replace(/'/g, '&#39;')) || html.includes(answer.explain));
  },
  member() {
    // A member reads everything but answers nothing; the owner's settings link is not offered.
    const html = cardReviewsHTML(view([ALL[4]], { can_decide: false, answers: [], loop: { enabled: false, rounds: 2 } }));
    assert.ok(html.includes('A workspace owner chooses what happens next.') && !html.includes('data-review-answer'));
    assert.ok(html.includes('A workspace owner can switch it on.') && !html.includes('data-review-settings'));
  },
  off() {
    // The owner is told where to switch it on, and that place is a real link.
    const html = cardReviewsHTML(view([], { loop: { enabled: false, rounds: 2 }, pr_info: null }));
    assert.ok(html.includes('The Coworker does not answer review comments in this project yet.'));
    assert.ok(html.includes('href="#/board/atlas/settings"') && html.includes('Open project settings') && html.includes('under Review comments'));
  },
  empty() {
    const html = cardReviewsHTML(view([]));
    assert.ok(html.includes('No review comments yet. When someone comments on pull request #7'));
  },
  rounds() {
    assert.equal(roundWords({ rounds_used: 2, rounds_allowed: 2 }), 'Round 2 of 2 used. No rounds are left: new comments wait for a workspace owner\'s answer.');
    assert.equal(roundWords({ loop_stopped: true }), 'A person is handling this pull request\'s comments; the Coworker does not answer them.');
    assert.equal(replyWords({ reply_mode: 'post' }), 'The Coworker posts its replies on the pull request.');
    assert.equal(summaryWords({}), '');
  },
  long() {
    const text = 'word '.repeat(200);
    const short = commentHTML(comment({ text }), 'acme/widgets', {});
    assert.ok(short.includes('Show all of it') && short.includes('aria-expanded="false"') && !short.includes(text.trim()));
    const all = commentHTML(comment({ text }), 'acme/widgets', { expanded: true });
    assert.ok(all.includes('Show less') && all.includes(text.trim()));
  },
  redacted() {
    const html = commentHTML(comment({ text: 'Use [redacted] here.', text_redacted: true }), 'acme/widgets', {});
    assert.ok(html.includes('looked like a password or key') && html.includes('data-review-original="1"'));
    const shown = commentHTML(comment({ text: 'Use [redacted] here.', text_redacted: true }), 'acme/widgets', { original: 'Use the real words here.' });
    assert.ok(shown.includes('Use the real words here.') && !shown.includes('data-review-original'));
  },
  escape() {
    const html = cardReviewsHTML(view([comment({ text: '<img src=x onerror=alert(1)>', author: '"><script>', path: '<b>p</b>', reply: '<i>r</i>', status: 'answered', status_text: 'Answered' })]));
    assert.ok(!html.includes('<img src=x') && !html.includes('"><script>') && !html.includes('<b>p</b>') && !html.includes('<i>r</i>'));
    // A commit that is not a SHA, or a repository that is not owner/name, is never a link.
    const odd = commentHTML(comment({ status: 'addressed', status_text: 'Fixed', fix_commit: 'javascript:alert(1)' }), 'acme/widgets');
    assert.ok(!odd.includes('href='));
  },
  slot() {
    assert.equal(cardReviewsSlot({ ...card, pr_url: null, pr_number: null }), '');
    const slot = cardReviewsSlot(card);
    assert.ok(slot.includes('role="status"') && slot.includes('data-review-status') && slot.includes('hidden'));
    // The status line is outside the redrawn body.
    assert.ok(slot.indexOf('data-review-body') < slot.indexOf('data-review-status'));
  },
  focuskeys() {
    const app = readFileSync(new URL('../commonplace/web/static/app.js', import.meta.url), 'utf8');
    const listed = JSON.parse(app.match(/const REVIEW_FOCUS_KEYS = (\[[^\]]*\]);/)[1]);
    assert.deepEqual(listed, FOCUS_KEYS);
    const { slot } = fakeSlot();
    assert.equal(focusedKey(slot, { activeElement: null }), null);
  },
  async mount() {
    const { slot, body, mount } = fakeSlot();
    const calls = [];
    await attachCardReviews(mount, card, { api: async (url) => { calls.push(url); return view(ALL); }, schedule: () => {} });
    assert.equal(slot.hidden, false);
    assert.ok(body.innerHTML.includes('Rename x to count.'));
    assert.deepEqual(calls, ['/api/pr-review/cards/7?limit=50']);
  },
  async hidden() {
    for (const status of [403, 404]) {
      const { slot, mount } = fakeSlot();
      await attachCardReviews(mount, card, { api: async () => { throw Object.assign(new Error('x'), { status }); } });
      assert.equal(slot.hidden, true, String(status));
    }
    const { slot, mount } = fakeSlot();
    await attachCardReviews(mount, card, { api: async () => view([], { repo: null, pr: null }) });
    assert.equal(slot.hidden, true);
  },
  async offline() {
    const { slot, body, status, mount } = fakeSlot();
    await attachCardReviews(mount, card, { api: async () => { throw new Error('network'); } });
    assert.equal(slot.hidden, false);
    assert.ok(body.innerHTML.includes('data-review-retry="load"'));
    assert.equal(status.textContent, offlineWords(new Error('network')));
  },
  async answer() {
    const { body, status, handlers, mount } = fakeSlot();
    const sent = [];
    let current = view([ALL[4]]);
    await attachCardReviews(mount, card, { api: async (url, init) => {
      if (init?.method === 'POST') { sent.push([url, init.body]); current = view([{ ...ALL[4], status: 'new', status_text: 'New', reason: null, reason_text: '', next: '' }]); return { choice: init.body.choice, card_answered: true }; }
      return current;
    }, schedule: () => {} });
    handlers.click(clickOn('reviewAnswer', 'again'));
    assert.equal(status.textContent, 'Saving your answer…');
    await wait(); await wait();
    assert.deepEqual(sent, [['/api/pr-review/cards/7/decision', { choice: 'again' }]]);
    assert.equal(status.textContent, 'Done. The comments go back to the Coworker for another round.');
    assert.ok(!body.innerHTML.includes('data-review-answer'));
    // The buttons are gone: focus lands on the heading, never nowhere.
    assert.equal(focused(), 'data-review-heading=reviews');
  },
  async refused() {
    const { status, handlers, mount } = fakeSlot();
    await attachCardReviews(mount, card, { api: async (url, init) => {
      if (init?.method === 'POST') throw Object.assign(new Error('x'), { status: 403, payload: { error: 'Only a workspace owner can decide this.' } });
      return view([ALL[4]]);
    }, schedule: () => {} });
    handlers.click(clickOn('reviewAnswer', 'mine'));
    await wait(); await wait();
    assert.equal(status.textContent, 'Only a workspace owner can decide this.');
    assert.equal(focused(), 'data-review-answer=mine');
  },
  async answerunrelated() { await partialAnswer('unrelated_question'); },
  async answercardrefused() { await partialAnswer('card_refused'); },
  async answernoconfirmation() { await partialAnswer(undefined); },
  async repaint() {
    // A background reload keeps focus on the same control and says nothing new.
    const { status, mount } = fakeSlot();
    let later = null;
    const { slot } = { slot: mount.querySelector('[data-card-reviews]') };
    await attachCardReviews(mount, card, { api: async () => view([ALL[1], ALL[4]]), schedule: (fn) => { later = fn; } });
    slot.querySelector('[data-review-answer="dismiss"]').focus();
    const before = status.log.length;
    assert.ok(later, 'a working comment schedules a reload');
    later();
    await wait(); await wait();
    assert.equal(focused(), 'data-review-answer=dismiss');
    assert.equal(status.log.length, before);
  },
  async expand() {
    const { body, handlers, mount } = fakeSlot();
    const text = 'word '.repeat(200);
    await attachCardReviews(mount, card, { api: async () => view([comment({ text })]), schedule: () => {} });
    handlers.click(clickOn('reviewExpand', '1'));
    assert.ok(body.innerHTML.includes('Show less'));
    assert.equal(focused(), 'data-review-expand=1');
  },
  async original() {
    const { body, status, handlers, mount } = fakeSlot();
    const calls = [];
    await attachCardReviews(mount, card, { api: async (url) => { calls.push(url); return url.endsWith('/original') ? { id: 1, text: 'The real words.' } : view([comment({ text: '[redacted]', text_redacted: true })]); }, schedule: () => {} });
    handlers.click(clickOn('reviewOriginal', '1'));
    await wait(); await wait();
    assert.ok(calls.includes('/api/pr-review/cards/7/comments/1/original'));
    assert.ok(body.innerHTML.includes('The real words.'));
    assert.equal(status.textContent, 'Showing the original words.');
  },
  async more() {
    const { handlers, mount } = fakeSlot();
    const calls = [];
    await attachCardReviews(mount, card, { api: async (url) => { calls.push(url); return view([comment()], { has_more: true, comments_total: 80 }); }, schedule: () => {} });
    handlers.click(clickOn('reviewMore', 'more'));
    await wait(); await wait();
    assert.deepEqual(calls, ['/api/pr-review/cards/7?limit=50', '/api/pr-review/cards/7?limit=100']);
    assert.equal(focused(), 'data-review-more=more');
  },
  async stale() {
    const { body, slot, mount } = fakeSlot();
    const release = [];
    const pending = attachCardReviews(mount, card, { api: () => new Promise((done) => { release.push(done); }) });
    slot.isConnected = false;
    release.forEach((done) => done(view(ALL)));
    await pending;
    assert.equal(body.innerHTML, '');
  },
  async kept() {
    // A redraw of the same card paints what was shown a moment ago, with focus where it was.
    const { body, mount } = fakeSlot();
    body.innerHTML = cardReviewsHTML(view([ALL[4]]));
    mount.querySelector('[data-card-reviews]').hidden = false;
    mount.querySelector('[data-card-reviews]').querySelector('[data-review-answer="mine"]').focus();
    const kept = previousReviews(mount);
    assert.equal(kept.key, 'atlas/7');
    assert.equal(kept.focus, '[data-review-answer="mine"]');
  },
  panel() {
    for (const deck of [false, true]) {
      const html = renderDetails(card, { board: { slug: 'atlas' }, me: 'user:maya', deck });
      const runs = html.indexOf('data-card-runs'), reviews = html.indexOf('data-card-reviews'), previews = html.indexOf('data-card-previews');
      assert.ok(runs > 0 && reviews > runs && previews > reviews, `deck=${deck}`);
      assert.ok(!renderDetails({ ...card, pr_url: null, pr_number: null }, { board: { slug: 'atlas' }, me: 'user:maya', deck }).includes('data-card-reviews'));
    }
  },
  async loads() {
    const body = { innerHTML: '' }, status = { textContent: '' };
    const slot = { hidden: true, isConnected: true, dataset: {}, addEventListener() {}, contains: () => false, querySelector: (s) => s === '[data-review-body]' ? body : s === '[data-review-status]' ? status : null };
    const mount = { innerHTML: '', addEventListener() {}, removeEventListener() {}, querySelector: (selector) => selector === '[data-card-reviews]' ? slot : null };
    const calls = [];
    const component = createDetails({ mount, api: async (url) => { calls.push(url); return url.startsWith('/api/pr-review') ? view([comment()]) : url.startsWith('/api/card-starts') ? [] : url.startsWith('/api/previews') ? { previews: [] } : {}; } });
    await component.show(card, { board: { slug: 'atlas' }, me: 'user:maya' });
    await wait();
    assert.deepEqual(calls.filter((url) => url.startsWith('/api/pr-review')), ['/api/pr-review/cards/7?limit=50']);
    assert.ok(body.innerHTML.includes('Rename x to count.'));
    component.stop();
  },
  settings() {
    const html = settings.settingsHTML({ board: { enabled: false, rounds: 2, post_replies: false }, reader: { state: 'no_token', last_read_at: null }, people: [] });
    for (const text of ['Waiting for the owner\'s token.', 'Let the Coworker answer review comments', 'Off by default.', 'Rounds before it asks you', 'Let the Coworker post its replies on public repositories', 'shown on the card first and not posted', 'Owners, members and collaborators of the repository are trusted already.', 'GitHub account name']) {
      assert.ok(html.includes(text) || html.includes(text.replace(/'/g, '&#39;')), `missing: ${text}`);
    }
    assert.ok(!/feeder|token=|ghp_|bundle/i.test(html));
    assert.ok(!html.includes(' checked') && html.includes('<option value="2" selected>'));
    const on = settings.settingsHTML({ board: { enabled: true, rounds: 3, post_replies: true }, reader: { state: 'reading', last_read_at: new Date().toISOString() }, people: [{ login: 'helper', role: 'trusted' }, { login: 'bot-acct', role: 'own' }] });
    assert.equal((on.match(/ checked/g) || []).length, 2);
    assert.ok(on.includes('The comment reader is working.') && on.includes('Last read just now.'));
    assert.ok(on.includes('<b>helper</b> · Trusted reviewer') && on.includes('<b>bot-acct</b> · The Coworker&#39;s own account') || on.includes("<b>bot-acct</b> · The Coworker's own account"));
    for (const state of ['waiting_first_read', 'stale']) assert.ok(settings.readerHTML({ state }).includes(settings.READER_WORDS[state].slice(0, 30).replace(/'/g, '&#39;')) || settings.readerHTML({ state }).includes(settings.READER_WORDS[state].slice(0, 30)));
  },
  async settingsmount() {
    // The owner's section loads three reads and saves the form; a member gets 403 and sees no section.
    const calls = [];
    const make = () => {
      const body = { innerHTML: '' }, status = { textContent: '' }, handlers = {};
      const host = { innerHTML: '', addEventListener: (type, fn) => { handlers[type] = fn; }, contains: () => false, querySelector: (s) => s === '[data-reviews-body]' ? body : s === '[data-reviews-status]' ? status : null };
      const section = { hidden: true, querySelector: (s) => s === '[data-project-reviews]' ? host : null };
      return { body, status, handlers, section };
    };
    const owner = make();
    await settings.mountReviewSettings(owner.section, { project: { board: 'atlas' }, api: async (url, init) => { calls.push([url, init?.method || 'GET', init?.body]); return url.includes('/boards/') ? { board: 'atlas', enabled: false, rounds: 2, post_replies: false } : url.endsWith('/status') ? { reader: { state: 'no_token' } } : { people: [] }; } });
    assert.equal(owner.section.hidden, false);
    assert.ok(owner.body.innerHTML.includes('Waiting for the owner'));
    assert.deepEqual(calls.map(([url]) => url).sort(), ['/api/pr-review/boards/atlas', '/api/pr-review/people', '/api/pr-review/status']);
    const form = { matches: (s) => s === '[data-reviews-form]', elements: { enabled: { checked: true }, rounds: { value: '3' }, post_replies: { checked: false } }, querySelectorAll: () => [] };
    owner.handlers.submit({ target: { closest: () => form }, preventDefault() {} });
    await wait(); await wait();
    assert.deepEqual(calls.find(([, method]) => method === 'PUT'), ['/api/pr-review/boards/atlas', 'PUT', { enabled: true, rounds: 3, post_replies: false }]);
    assert.equal(owner.status.textContent, 'Saved. The Coworker answers review comments in this project, 3 rounds before it asks you.');
    const member = make();
    await settings.mountReviewSettings(member.section, { project: { board: 'atlas' }, api: async () => { throw Object.assign(new Error('x'), { status: 403 }); } });
    assert.equal(member.section.hidden, true);
  },
  chip() {
    assert.equal(pipeline.reviewChip(null), '');
    assert.ok(pipeline.reviewChip({ card: 7, counts: { needs_you: 2 } }).includes('2 review comments need you'));
    assert.ok(pipeline.reviewChip({ card: 7, counts: { working: 1 } }).includes('1 review comment is being answered'));
    assert.ok(pipeline.reviewChip({ card: 7, counts: { addressed: 3 } }).includes('3 review comments, all handled'));
    const pr = { repo: 'acme/widgets', pr: 7, title: 'Count', state: 'running', steps: [], lead: {} };
    const html = pipeline.prHTML(pr, { review: { card: 7, counts: { needs_you: 1 } } });
    assert.ok(html.includes('data-pl-reviews') && html.includes('href="#/card/7"'));
    assert.ok(!pipeline.prHTML(pr, {}).includes('data-pl-reviews'));
  },
};
assert.ok(Object.hasOwn(cases, process.argv[2]), 'Unknown case');
await cases[process.argv[2]]();
console.log(`${process.argv[2]} PASS`);
