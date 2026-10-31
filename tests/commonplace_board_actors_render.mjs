import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
const url = (source) => `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
const read = (name) => readFile(new URL(`../commonplace/web/static/${name}`, import.meta.url), "utf8");
const apiUrl = url(await read("api.js"));
const load = async (name) => import(url((await read(name)).replace('"./api.js"', `"${apiUrl}"`)));
const { childProgress } = await load("children.js");
const { homeView } = await load("home.js");
const dot = (c) => `<i class="status-dot ${c.need}"></i>`;
const card = {
  number: 4, board: "work", title: "Top actor", need: "working", status: "doing",
  updated_at: new Date().toISOString(), children: { done: 2, total: 4 }, children_need_you: true,
  child_cards: ["done", "done", "review", "todo"].map((status, i) => ({ number: i + 5, title: 'Original <script> & "words"', status, need: status === "review" ? "you" : "idle" })),
};
const collapsed = childProgress(card, false, dot);
assert.match(collapsed, /2\/4 klaar/);
assert.match(collapsed, /aria-expanded="false"/);
assert.match(collapsed, /status-dot you/);
assert.doesNotMatch(collapsed, /Original/);
const expanded = childProgress(card, true, dot);
assert.match(expanded, /aria-expanded="true"/);
assert.equal((expanded.match(/<li>/g) || []).length, 4);
assert.match(expanded, /#5<\/a>.*Original &lt;script&gt; &amp; &quot;words&quot;/);
assert.match(expanded, /<span>review<\/span>/);
assert.doesNotMatch(expanded, /<script>/);
assert.equal(childProgress({ number: 1 }, false, dot), "");
const rows = { dot, taskRow: () => "row", children: (c, scope) => childProgress(c, true, dot, scope) };
const html = homeView({ open: [card] }, rows);
assert.match(html, /id="children-home-4"/);
assert.match(html, /id="children-stack-4"/);
assert.equal((html.match(/2\/4 klaar/g) || []).length, 2);
assert.doesNotMatch(childProgress({ ...card, children_need_you: false }, false, dot), /status-dot you/);
