// Direction A, "Contacts": conversation first. Your bots are the navigation, like people in a
// messenger; the open thread is the page; what the bot is doing sits in a quiet column beside it.
import { e, icon, device, topology, initial } from "./kit.js";

const av = (b, cls = "") => `<span class="av ${cls}" style="--h:${b.hue}" data-kind="${b.kind}">${initial(b.name)}</span>`;
const pill = (D, s) => `<span class="pill ${s}">${D.STATUS[s]}</span>`;
const stateDot = (b) => (b.state === "idle" ? "" : `<span class="sd ${b.state}" title="${b.state === "working" ? "Working" : "Needs you"}"></span>`);
const bot = (D, id) => D.bots.find((b) => b.id === id);
const node = (D, id) => D.nodes.find((n) => n.id === id);

function side(route, D) {
  const row = (b) => `<a class="bot${b.id === "atlas" && (route === "chat" || route === "project") ? " on" : ""}" href="#/chat">${av(b)}<span class="bt"><b>${e(b.name)}</b><time>${e(b.ago)}</time><span class="ln">${e(b.last)}</span>${b.state === "needs_you" ? `<i class="un">${D.waiting.filter((w) => w.bot === b.id).length}</i>` : stateDot(b)}</span></a>`;
  const nav = (id, label, extra = "") => `<a class="nav${route === id ? " on" : ""}" href="#/${id}">${icon(id)}<span>${label}</span>${extra}</a>`;
  return `<aside class="side">
<a class="brand" href="#/bots"><svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><path d="M3 4.5A1.5 1.5 0 014.5 3h13A1.5 1.5 0 0119 4.5v9a1.5 1.5 0 01-1.5 1.5H10l-4.5 4v-4h-1A1.5 1.5 0 013 13.5z" fill="var(--accent)"/></svg><b>Commonplace</b><span class="wsn">${e(D.ws.name)}</span></a>
<label class="find">${icon("search")}<input placeholder="Ask any bot, or find something" aria-label="Ask any bot, or find something"><kbd>/</kbd></label>
<div class="grp"><a href="#/bots">Bots</a><a class="add" href="#/bots" aria-label="New bot">${icon("plus")}</a></div>
<nav class="bots">${D.bots.map(row).join("")}</nav>
<nav class="foot">${nav("compute", "Compute", `<em>${D.counts.agents} agents on ${new Set(D.agents.map((a) => a.machine)).size} machines</em>`)}${nav("settings", "Settings")}
<div class="me"><span class="av me-av">${D.ws.user.initial}</span><span>${e(D.ws.user.name)}</span></div></nav>
</aside>`;
}

function approval(D, w, o) {
  if (o.accepted) return `<div class="ask done">${icon("check")}<div><b>Accepted.</b> Going live on Build box now. I'll tell you when it's up.</div></div>`;
  return `<div class="ask"><div class="ask-h"><b>${e(w.title)}</b><span>${e(w.why)}</span></div>
<ul>${w.facts.map((f) => `<li>${icon("check", 14)}${e(f)}</li>`).join("")}</ul>
<div class="ask-b"><button class="btn go" data-accept>${e(w.accept)}</button><button class="btn">${e(w.decline)}</button><a href="#/project">See the change</a></div></div>`;
}

function thread(D, o) {
  const msg = (m) => {
    if (m.role === "you") return `<div class="m you"><p>${e(m.text)}</p></div>`;
    return `<div class="m bot">${av(bot(D, "atlas"), "sm")}<div class="mb"><p>${e(m.text)}</p>
${m.did ? `<ul class="did">${m.did.map((d) => `<li>${icon("check", 14)}${e(d)}</li>`).join("")}</ul>` : ""}
${m.work ? `<a class="work" href="#/project">${pill(D, m.work.status)}<b>${e(m.work.title)}</b><span class="wn">${e(m.work.note)}</span>${icon("chev", 14)}</a>` : ""}
${m.waiting ? approval(D, D.waiting.find((w) => w.id === m.waiting), o) : ""}
${m.suggest ? `<div class="sug">${m.suggest.map((s) => `<button class="chip">${e(s)}</button>`).join("")}</div>` : ""}
</div></div>`;
  };
  return `<div class="thread"><div class="day">Today, 10:02</div>${D.thread.map(msg).join("")}</div>
<form class="composer" onsubmit="return false">${icon("clip", 18)}<input placeholder="Message Atlas" aria-label="Message Atlas">${icon("mic", 18)}<button class="send" aria-label="Send">${icon("send")}</button></form>`;
}

const botHead = (D, route, o) => {
  const b = bot(D, "atlas");
  const mine = D.agents.filter((a) => a.bot === "atlas");
  const tab = (id, label) => `<a class="${route === id ? "on" : ""}" href="#/${id}">${label}</a>`;
  return `<header class="bh">${o.phone ? `<a class="back" href="#/bots" aria-label="Bots">${icon("back", 18)}</a>` : ""}${av(b)}<div class="bh-t"><h1>${e(b.name)}</h1><span>${e(b.job)}</span></div>
<nav class="tabs">${tab("chat", "Chat")}${tab("project", "Project")}</nav>
${o.phone ? "" : `<span class="live"><span class="sd working"></span>${mine.length} agent working on ${e(node(D, mine[0].machine).name)}</span>`}</header>`;
};

function rail(D) {
  const p = D.project;
  const by = (s) => p.work.filter((w) => w.status === s);
  const rows = (list) => list.map((w) => `<li><b>${e(w.title)}</b><span>${e(w.note)}</span></li>`).join("");
  return `<aside class="rail"><h2>Right now</h2>
<section><h3>Working <i>${by("working").length}</i></h3><ul>${rows(by("working"))}</ul></section>
<section><h3>Blocked <i>${by("blocked").length}</i></h3><ul>${rows(by("blocked"))}</ul></section>
<section><h3>Up next <i>${by("queued").length}</i></h3><ul>${rows(by("queued"))}</ul></section>
<section><h3>Atlas remembers</h3><ul class="mem">${p.memory.map((m) => `<li>${e(m)}</li>`).join("")}</ul></section>
</aside>`;
}

function chat(D, o) {
  return `${botHead(D, "chat", o)}<div class="split"><div class="conv">${thread(D, o)}</div>${o.phone ? "" : rail(D)}</div>`;
}

function projectView(D, o) {
  const p = D.project;
  const groups = ["needs_you", "working", "blocked", "queued"];
  const item = (w) => `<li class="wk">${pill(D, w.status)}<div><b>${e(w.title)}</b><span>${e(w.note)}</span></div><span class="wh">${e(w.where)}</span><time>${e(w.when)}</time>${w.status === "needs_you" ? `<a class="btn go sm" href="#/chat">Review</a>` : ""}</li>`;
  return `${botHead(D, "project", o)}<div class="pg two"><div>
<p class="lede">${D.counts.open} things open, ${p.work.filter((w) => w.status === "needs_you").length} waiting on you. Atlas keeps this list; you never have to.</p>
<ul class="wl">${groups.flatMap((g) => p.work.filter((w) => w.status === g)).map(item).join("")}</ul>
<h2>Done this week <i>${D.counts.done}</i></h2><ul class="dl">${p.done.map((d) => `<li>${icon("check", 14)}<span>${e(d.title)}</span><time>${e(d.when)}</time></li>`).join("")}</ul></div>
<aside class="facts"><section><h3>Runs on</h3><ul class="kv"><li><span>${device("server", 20)}Build box</span><em class="ok">Online</em></li><li><span>${device("vm", 20)}Staging VM</span><em class="warn">Reconnecting</em></li></ul><a href="#/compute">Open the map</a></section>
<section><h3>Site</h3><ul class="kv"><li><span>${device("site", 20)}${e(p.site)}</span><em class="ok">Up</em></li></ul></section>
<section><h3>Atlas remembers</h3><ul class="mem">${p.memory.map((m) => `<li>${e(m)}</li>`).join("")}</ul><a href="#/chat">Tell Atlas something new</a></section></aside></div>`;
}

function roster(D, o) {
  const doing = (b) => { const n = D.agents.filter((a) => a.bot === b.id).length; const w = n ? `${n} agent${n > 1 ? "s" : ""} working` : ""; return b.state === "needs_you" ? `Waiting on you${w ? `, ${w}` : ""}` : w || "Idle"; };
  if (o.phone) {
    return `<header class="ph"><h1>Bots</h1><a class="btn" href="#/bots">New bot</a></header>
<a class="strip" href="#/chat"><b>${D.counts.waiting} waiting on you</b><span>Atlas, Ledger</span>${icon("chev", 14)}</a>
<nav class="bots big">${D.bots.map((b) => `<a class="bot" href="#/chat">${av(b)}<span class="bt"><b>${e(b.name)}</b><time>${e(b.ago)}</time><span class="ln">${e(b.last)}</span>${b.state === "needs_you" ? `<i class="un">${D.waiting.filter((w) => w.bot === b.id).length}</i>` : stateDot(b)}</span></a>`).join("")}</nav>`;
  }
  return `<header class="ph"><div><h1>Bots</h1><p>${D.counts.bots} bots. ${D.counts.waiting} are waiting on you, ${D.counts.agents} agents are working.</p></div></header>
<div class="pg two wide"><table class="tb"><thead><tr><th>Bot</th><th>Does</th><th>Right now</th><th class="num">Last word</th></tr></thead><tbody>
${D.bots.map((b) => `<tr><td><a class="who" href="#/chat">${av(b)}<b>${e(b.name)}</b></a></td><td>${e(b.job)}</td><td><span class="now ${b.state}">${stateDot(b)}${doing(b)}</span></td><td class="dim num">${e(b.ago)}</td></tr>`).join("")}</tbody></table>
<form class="newbot" onsubmit="return false"><h2>New bot</h2><p>Give it a name and a job. It gets its own thread and its own memory.</p>
<label>Name<input value="Press"></label><label>Job<input value="Drafts the monthly newsletter"></label>
<label>How it should work<textarea rows="3">Ask me before sending anything. Keep drafts short.</textarea></label>
<button class="btn dark">Create bot</button></form></div>`;
}

function nodePanel(D, o) {
  const n = node(D, o.node);
  const s = D.selected;
  const own = n.id === s.id;
  const list = D.agents.filter((a) => a.machine === n.id);
  const body = own
    ? `<nav class="tabs sm">${s.tabs.map((t) => `<a class="${t === s.tab ? "on" : ""}">${t}</a>`).join("")}</nav>
<ul class="ag">${list.map((a) => `<li>${av(bot(D, a.bot), "sm")}<div><b>${e(a.task)}</b><span>${e(bot(D, a.bot).name)} · ${e(a.since)}</span></div></li>`).join("")}</ul>
<ul class="kv plain">${s.config.slice(1, 3).map(([k, v]) => `<li><span>${e(k)}</span><em>${e(v)}</em></li>`).join("")}</ul>`
    : `<ul class="kv plain"><li><span>Role</span><em>${e(n.role)}</em></li></ul>`;
  return `<aside class="np"><div class="np-h">${device(n.type, 32)}<div><b>${e(n.name)}</b><span class="st ${n.state}">${n.state === "up" ? "Online" : n.state === "warn" ? "Reconnecting" : "Offline"}</span></div></div>${body}
<button class="btn wide">Start an agent here</button></aside>`;
}

function compute(D, o) {
  const c = D.counts;
  return `<header class="ph"><div><h1>Compute</h1><p>${o.phone ? `${c.up} of ${c.runners} runners online · ${c.agents} agents working` : `${c.up} of ${c.runners} runners online, ${c.warn} reconnecting, ${c.down} offline. ${c.agents} agents working.`}</p></div>
<div class="acts"><button class="btn">Add machine</button><button class="btn">Add site</button>${o.phone ? "" : `<button class="btn dark">Let a bot map it</button>`}</div></header>
<div class="mapwrap"><div class="map">${topology({ nodes: D.nodes, links: D.links, selected: o.node, phone: o.phone, W: o.phone ? o.vw - 32 : o.vw - 704, H: o.phone ? 470 : o.vh - 172 })}
<div class="key"><span><i class="kd up"></i>Up</span><span><i class="kd warn"></i>Connecting</span><span><i class="kd down"></i>Down</span></div></div>${nodePanel(D, o)}</div>`;
}

function settingsView(D, o) {
  const s = D.settings;
  const seg = (c) => `<span class="seg${c.locked ? " lock" : ""}"><i class="${c.mode === "own" ? "on" : ""}">On their own</i><i class="${c.mode === "ask" ? "on" : ""}">Ask me first</i></span>`;
  const areas = `<nav class="areas">${s.areas.map((a) => `<a class="${a === s.area ? "on" : ""}">${a}</a>`).join("")}</nav>`;
  return `<header class="ph"><div><h1>${o.phone ? "Approvals" : "Settings"}</h1>${o.phone ? `<p>Settings</p>` : ""}</div></header>
<div class="set">${o.phone ? "" : areas}<div class="set-b">
<section class="srow"><div class="sl"><h2>What bots may do on their own</h2><p>Everything else stops and asks you, once, where you are. Two kinds always ask.</p></div>
<ul class="panel">${s.classes.map((c) => `<li><div><b>${e(c.title)}</b><span>${e(c.note)}</span></div>${seg(c)}</li>`).join("")}</ul></section>
<section class="srow"><div class="sl"><h2>Where a question reaches you</h2><p>One question, one place at a time. Answering it anywhere clears it everywhere.</p>
<div class="pv"><span>On your phone it looks like this</span><div class="push"><b>Atlas</b> Put the login fix live on northwind.example?<div><i>Accept</i><i>Not now</i></div></div></div></div>
<ul class="panel">${s.arrive.map((a) => `<li><div><b>${e(a.title)}</b><span>${e(a.note)}</span></div><span class="tg${a.on ? " on" : ""}${a.locked ? " lock" : ""}"></span></li>`).join("")}</ul></section>
</div></div>`;
}

const VIEWS = { chat, project: projectView, bots: roster, compute, settings: settingsView };

export function render(route, D, o) {
  const view = (VIEWS[route] || chat)(D, o);
  if (!o.phone) return `<div class="frame">${side(route, D)}<main class="sheet v-${route}">${view}</main></div>`;
  const tabs = route === "chat" || route === "project" ? "" : `<nav class="tabbar">${[["bots", "Bots"], ["compute", "Compute"], ["settings", "Settings"]].map(([id, label]) => `<a class="${route === id ? "on" : ""}" href="#/${id}">${icon(id, 22)}<span>${label}</span>${id === "bots" ? `<i class="un">${D.counts.waiting}</i>` : ""}</a>`).join("")}</nav>`;
  return `<main class="sheet phone v-${route}">${view}</main>${tabs}`;
}
