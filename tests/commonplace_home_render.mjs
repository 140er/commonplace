import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
const url = (source) => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const read = (name) => readFile(new URL(`../commonplace/web/static/${name}`, import.meta.url), "utf8");
const apiUrl = url(await read("api.js"));
const { homeView, needItems } = await import(url((await read("home.js")).replace('"./api.js"', `"${apiUrl}"`)));
const rows = {
  dot: (card) => `<i class="status-dot ${card.need}" data-need="${card.need}"></i>`,
  taskRow: (card, context) => `<div class="trow">#${card.number}${context}</div>`,
};
const at = new Date(Date.now() - 3 * 3600000).toISOString();
const card = (number, fields) => ({ number, board: "work", title: `Card ${number}`, status: "doing", need: "working", updated_at: at, ...fields });
const ask = card(1, { title: "Ship <script>alert(1)</script>?", status: "review", need: "you", ask: "Which one?\nSecond line stays in the card.", ask_options: ["A", "B"] });
const plan = card(2, { status: "review", need: "plan" });
const merge = card(3, { need: "merge", branch: "feat/x", pr_number: 19, pr_url: "https://github.com/140er/mempalace/pull/19" });
const stale = card(4, { board: "lab", need: "idle" });
const working = card(5, { board: "lab", need: "working" });
const backlog = card(6, { status: "backlog", need: "idle" });
const quiet = card(7, { board: "quiet", status: "todo", need: "idle" });
const needs = { asks: [plan, ask], done_review: [], backlog_from_agents: [backlog], ready_to_merge: [merge], stale: [stale], count: 5 };
const boards = [
  { slug: "work", name: "Work <b>", counts: { todo: 0, doing: 1, review: 2 } },
  { slug: "lab", name: "Lab", counts: { doing: 2 } },
  { slug: "quiet", name: "Quiet", counts: { todo: 1 } },
];
const html = homeView({ needs, boards, open: [ask, plan, merge, stale, working, quiet], selected: 1, speak: true }, rows);

// Three parts, top to bottom: the box, one tile per space, the task table.
const at_ = (s) => html.indexOf(s);
assert.ok(at_('class="needbox waiting"') >= 0 && at_('class="needbox') < at_('class="home-spaces"') && at_('class="home-spaces"') < at_('class="home-tasks"'));
assert.match(html, /<h2 id="needbox-title"><b class="n">5<\/b> things need you<\/h2><button class="opt speak" data-action="speak-digest"/);
assert.ok(html.includes('id="digest-captions"'));

// One line per item, ordered like the spoken digest; reasons from need and queue.
assert.deepEqual(needItems(needs).map((i) => [i.card.number, i.reason]), [
  [1, "asks: Which one?"], [2, "plan to approve"], [3, "ready to merge"], [4, "claim went stale"], [6, "filed by an agent"],
]);
assert.ok(html.includes('<a class="tt" href="#/card/1">Ship &lt;script&gt;alert(1)&lt;/script&gt;?</a><span class="why">asks: Which one?</span>'));
assert.ok(!html.includes("<script>") && !html.includes("Second line"));
assert.ok(html.includes('class="nline sel" data-row="1"'));
// The ask is answered in its card, its one clickable place; Home never repeats the choices.
assert.ok(!html.includes('data-action="decide"') && !html.includes("data-form=\"reply\""));

// Spaces: the worst state among open cards, else quiet, two facts, one Open.
const tile = (slug) => html.slice(at_(`data-key="space-${slug}"`), html.indexOf("</article>", at_(`data-key="space-${slug}"`)));
assert.match(tile("work"), /<h3>Work &lt;b&gt;<\/h3><a class="opt" href="#\/board\/work">Open<\/a>/);
assert.match(tile("work"), /class="pill you">.*Needs you<\/span><span>3 open<\/span><span><span class="hot">4 waiting<\/span>/);
assert.match(tile("lab"), /class="pill stale">.*Stale<\/span><span>2 open<\/span><span><span class="hot">1 waiting/);
assert.match(tile("quiet"), /class="pill quiet"><i class="status-dot idle".*Quiet<\/span><span>1 open<\/span><span>0 doing<\/span>/);
assert.equal(tile("quiet").match(/<a /g).length, 1);

// Table: attention first, branch and PR in the title cell, an action only where needed.
const table = html.slice(at_("<tbody>"), at_("</tbody>"));
assert.deepEqual([...table.matchAll(/data-key="task-(\d+)"/g)].map((m) => Number(m[1])), [1, 2, 3, 4, 5, 7]);
const row = (n) => table.slice(table.indexOf(`data-key="task-${n}"`), table.indexOf("</tr>", table.indexOf(`data-key="task-${n}"`)));
assert.match(row(1), /<a class="opt" href="#\/card\/1">Answer<\/a>/);
assert.match(row(2), /<a class="opt" href="#\/card\/2">Approve<\/a>/);
assert.match(row(3), /<span class="sub"><span>feat\/x<\/span><span>PR #19<\/span><\/span>/);
assert.match(row(3), /href="https:\/\/github.com\/140er\/mempalace\/pull\/19" target="_blank" rel="noopener noreferrer">Merge<\/a>/);
assert.match(row(4), /<button class="opt" data-action="move" data-card="4" data-status="todo">Retake<\/button>/);
assert.match(row(4), />Lab<\/a>/);
assert.match(row(4), /<time data-elapsed="[^"]+" datetime="[^"]+">3h<\/time>/);
for (const n of [5, 7]) assert.doesNotMatch(row(n), /class="opt"/);
// Phone: the same rows through the shared task-row component.
assert.equal((html.match(/class="trow-card"/g) || []).length, 6);
assert.ok(html.includes('<article class="trow-card" data-key="stack-1"><div class="trow">#1<a href="#/board/work">Work &lt;b&gt;</a></div><div class="opts"><a class="opt" href="#/card/1">Answer</a></div></article>'));

const empty = homeView({ needs: { count: 0 }, boards: [], open: [], speak: false }, rows);
assert.ok(empty.includes('<section class="needbox " aria-labelledby="needbox-title"><header><h2 id="needbox-title">Nothing needs you</h2></header>'));
assert.ok(empty.includes("No open tasks") && !empty.includes("<table>") && !empty.includes("speak-digest"));
const one = homeView({ needs: { asks: [ask] }, boards, open: [ask] }, rows);
assert.ok(one.includes("<b class=\"n\">1</b> thing needs you"));
