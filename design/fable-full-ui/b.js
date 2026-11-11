// Direction B, "Deck": decisions first. Home is Today: what waits on you, as a short stack you
// clear with one tap each, beside what the agents are doing. No sidebar; a chat is a focus mode.
import { e, icon, device, topology, initial } from "./kit.js";

const av = (b, cls = "") => `<span class="av ${cls}" style="--h:${b.hue}" data-kind="${b.kind}">${initial(b.name)}</span>`;
const bot = (D, id) => D.bots.find((b) => b.id === id);
const node = (D, id) => D.nodes.find((n) => n.id === id);
const pill = (D, s) => `<span class="pill ${s}">${D.STATUS[s]}</span>`;
const NAV = [["today", "Today"], ["bots", "Bots"], ["compute", "Compute"], ["settings", "Settings"]];
const section = (route) => (route === "chat" || route === "project" ? "bots" : route);

function bar(route, D) {
  return `<header class="bar"><div class="col"><a class="brand" href="#/today"><svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><rect x="2" y="2" width="18" height="18" rx="6" fill="#fff"/><path d="M7 11.5l3 3 5.5-6.5" stroke="#000" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg><b>Commonplace</b></a>
<nav>${NAV.map(([id, label]) => `<a class="${section(route) === id ? "on" : ""}" href="#/${id}">${label}${id === "today" ? `<i class="n">${D.counts.waiting}</i>` : ""}</a>`).join("")}</nav>
<span class="ws">${e(D.ws.name)}</span><span class="av me">${D.ws.user.initial}</span></div></header>`;
}

function decision(D, w, o, big = true) {
  const b = bot(D, w.bot);
  const done = w.id === "w1" && o.accepted;
  const body = w.kind === "approve"
    ? `<ul class="facts">${w.facts.map((f) => `<li>${icon("check", 14)}${e(f)}</li>`).join("")}</ul>
<div class="dbtn">${done ? `<span class="okd">${icon("check")}Accepted. Going live on Build box.</span>` : `<button class="btn inv" data-accept>${e(w.accept)}</button><button class="btn">${e(w.decline)}</button><a href="#/chat">Open the chat</a>`}</div>`
    : `<div class="dbtn opts">${w.options.map((x) => `<button class="btn">${e(x)}</button>`).join("")}<a href="#/chat">Answer in your own words</a></div>`;
  return `<article class="dec${big ? "" : " inl"}"><header>${av(b, "sm")}<b>${e(b.name)}</b><span>${e(b.job)}</span><time>${e(w.ago)} ago</time></header>
<h2>${e(w.title)}</h2><p>${e(w.why)}</p>${body}</article>`;
}

const composer = (label, to) => `<form class="composer" onsubmit="return false">${to ? `<span class="to">To ${e(to)}${icon("down", 12)}</span>` : icon("clip", 18)}<input placeholder="${e(label)}" aria-label="${e(label)}">${icon("mic", 18)}<button class="send" aria-label="Send">${icon("send")}</button></form>`;

function today(D, o) {
  const run = D.agents.map((a) => `<li>${av(bot(D, a.bot), "sm")}<div><b>${e(a.task)}</b><span>${e(bot(D, a.bot).name)} on ${e(node(D, a.machine).name)}</span></div><time>${e(a.since)}</time></li>`).join("");
  const left = D.waiting.filter((w) => !(w.id === "w1" && o.accepted)).length;
  return `<div class="col pg"><div class="title"><h1>Today</h1><p>${left} thing${left === 1 ? "" : "s"} wait on you. ${D.counts.agents} agents are working on ${new Set(D.agents.map((a) => a.machine)).size} machines.</p></div>
<div class="grid"><section class="stack"><h3>Waiting on you <i>${left}</i></h3>${D.waiting.map((w) => decision(D, w, o)).join("")}</section>
<section class="side"><div class="card"><h3>Working now <i>${D.counts.agents}</i></h3><ul class="rows">${run}</ul></div>
<div class="card"><h3>Finished since yesterday <i>${D.project.done.slice(0, 3).length}</i></h3><ul class="rows fin">${D.project.done.slice(0, 3).map((d) => `<li>${icon("check", 14)}<div><b>${e(d.title)}</b></div><time>${e(d.when)}</time></li>`).join("")}</ul></div></section></div></div>
<div class="dock"><div class="col">${composer(o.phone ? "Ask for anything" : "Tell a bot what you need", "Atlas")}</div></div>`;
}

const strip = (D, o) => `<nav class="chips"><div class="col">${o.phone ? `<a class="back" href="#/bots" aria-label="Bots">${icon("back", 18)}</a>` : ""}${D.bots.filter((b) => !o.phone || b.id === "atlas").map((b) => `<a class="${b.id === "atlas" ? "on" : ""}" href="#/chat">${av(b, "sm")}<span>${e(b.name)}</span>${b.state === "needs_you" ? `<i class="wd"></i>` : ""}</a>`).join("")}<a class="pj" href="#/project">${o.phone ? "Project" : "What Atlas is doing"}${icon("chev", 14)}</a></div></nav>`;

function chat(D, o) {
  const msg = (m) => {
    if (m.role === "you") return `<div class="m you"><p>${e(m.text)}</p></div>`;
    return `<div class="m bot"><p>${e(m.text)}</p>
${m.did ? `<ul class="did">${m.did.map((d) => `<li>${icon("check", 14)}${e(d)}</li>`).join("")}</ul>` : ""}
${m.work ? `<a class="work" href="#/project">${pill(D, m.work.status)}<b>${e(m.work.title)}</b><span class="wn">${e(m.work.note)}</span>${icon("chev", 14)}</a>` : ""}
${m.waiting ? decision(D, D.waiting.find((w) => w.id === m.waiting), o, false) : ""}
${m.suggest ? `<div class="sug">${m.suggest.map((s) => `<button class="btn">${e(s)}</button>`).join("")}</div>` : ""}</div>`;
  };
  return `${strip(D, o)}<div class="thread"><div class="tcol">${D.thread.map(msg).join("")}</div></div><div class="dock"><div class="tcol">${composer("Message Atlas")}</div></div>`;
}

function roster(D, o) {
  const doing = (b) => { const n = D.agents.filter((a) => a.bot === b.id).length; return b.state === "needs_you" ? `<span class="amb">Waiting on you</span>` : n ? `${n} agent${n > 1 ? "s" : ""} working` : "Idle"; };
  const tile = (b) => `<a class="card tile" href="#/chat"><header>${av(b)}<div><b>${e(b.name)}</b><span>${e(b.job)}</span></div></header><p>${e(b.last)}</p><footer><span>${doing(b)}</span><time>${e(b.ago)}</time></footer></a>`;
  return `<div class="col pg"><div class="title"><h1>Bots</h1><p>${D.counts.bots} bots, one thread each. Tap one to talk.</p><button class="btn inv">New bot</button></div>
<div class="tiles">${D.bots.map(tile).join("")}</div></div>`;
}

function projectView(D, o) {
  const p = D.project;
  const b = bot(D, p.bot);
  const row = (w) => `<li>${pill(D, w.status)}<div><b>${e(w.title)}</b><span>${e(w.note)}</span></div><span class="wh">${e(w.where)}</span><time>${e(w.when)}</time></li>`;
  return `<div class="col pg"><div class="card obj">${av(b, "lg")}<div><h1>${e(b.name)}</h1><p>${e(b.job)} · ${e(p.site)} · ${D.counts.open} open, ${p.work.filter((w) => w.status === "needs_you").length} waiting on you</p></div><a class="btn inv" href="#/chat">Talk to Atlas</a></div>
<div class="grid"><section class="card"><h3>Open work <i>${D.counts.open}</i></h3><ul class="rows wk">${p.work.map(row).join("")}</ul></section>
<section class="side"><div class="card"><h3>Runs on</h3><ul class="rows mach"><li>${device("server", 22)}<div><b>Build box</b></div><em class="up">Online</em></li><li>${device("vm", 22)}<div><b>Staging VM</b></div><em class="warn">Reconnecting</em></li></ul></div>
<div class="card"><h3>Atlas remembers <i>${p.memory.length}</i></h3><ul class="mem">${p.memory.map((m) => `<li>${e(m)}</li>`).join("")}</ul></div>
<div class="card"><h3>Done this week <i>${D.counts.done}</i></h3><ul class="rows fin">${p.done.map((d) => `<li>${icon("check", 14)}<div><b>${e(d.title)}</b></div><time>${e(d.when)}</time></li>`).join("")}</ul></div></section></div></div>`;
}

function compute(D, o) {
  const c = D.counts;
  const n = node(D, o.node);
  const s = D.selected;
  const list = D.agents.filter((a) => a.machine === n.id);
  const detail = n.id === s.id
    ? `<nav class="tabs">${s.tabs.map((t) => `<a class="${t === s.tab ? "on" : ""}">${t}</a>`).join("")}</nav><ul class="rows">${list.map((a) => `<li>${av(bot(D, a.bot), "sm")}<div><b>${e(a.task)}</b><span>${e(bot(D, a.bot).name)}</span></div><time>${e(a.since)}</time></li>`).join("")}</ul>`
    : `<p class="role">${e(n.role)}</p>`;
  return `<div class="col pg map-pg"><div class="title"><h1>Compute</h1><p>${c.up} of ${c.runners} runners online${o.phone ? "" : `, ${c.warn} reconnecting, ${c.down} offline`}. ${c.agents} agents working.</p><div class="acts"><button class="btn">Add machine</button><button class="btn">Add site</button>${o.phone ? "" : `<button class="btn inv">Let a bot map it</button>`}</div></div>
<div class="mapbox">${topology({ nodes: D.nodes, links: D.links, selected: o.node, phone: o.phone, W: o.phone ? o.vw - 32 : Math.min(o.vw - 48, 1200) - 340, H: o.phone ? 440 : o.vh - 196 })}<div class="key"><span><i class="kd up"></i>Up</span><span><i class="kd warn"></i>Connecting</span><span><i class="kd down"></i>Down</span></div>
<aside class="card np"><header>${device(n.type, 30)}<div><b>${e(n.name)}</b><span class="st ${n.state}">${n.state === "up" ? "Online" : n.state === "warn" ? "Reconnecting" : "Offline"}</span></div></header>${detail}<button class="btn wide">Start an agent here</button></aside></div></div>`;
}

function settingsView(D, o) {
  const s = D.settings;
  const seg = (c) => (c.locked ? `<span class="fixed">${c.mode === "own" ? "On their own" : "Always asks"}</span>` : `<span class="seg"><i class="${c.mode === "own" ? "on" : ""}">On their own</i><i class="${c.mode === "ask" ? "on" : ""}">Ask me first</i></span>`);
  return `<div class="col pg"><div class="title"><h1>Settings</h1></div>
<nav class="tabs areas">${s.areas.map((a) => `<a class="${a === s.area ? "on" : ""}">${a}</a>`).join("")}</nav>
<div class="grid set"><section class="card"><h3>What bots may do on their own</h3><ul class="rows opt">${s.classes.map((c) => `<li><div><b>${e(c.title)}</b><span>${e(c.note)}</span></div>${seg(c)}</li>`).join("")}</ul></section>
<section class="side"><div class="card"><h3>Where a question reaches you</h3><ul class="rows opt">${s.arrive.map((a) => `<li><div><b>${e(a.title)}</b><span>${e(a.note)}</span></div><span class="tg${a.on ? " on" : ""}${a.locked ? " lock" : ""}"></span></li>`).join("")}</ul></div>
<div class="card"><h3>On your phone it looks like this</h3><div class="push"><b>Atlas</b><p>Put the login fix live on northwind.example?</p><div><i>Accept</i><i>Not now</i></div></div></div></section></div></div>`;
}

const VIEWS = { today, chat, bots: roster, project: projectView, compute, settings: settingsView };

export function render(route, D, o) {
  const r = VIEWS[route] ? route : "today";
  const view = VIEWS[r](D, o);
  if (!o.phone) return `<div class="app v-${r}">${bar(r, D)}<main>${view}</main></div>`;
  const ic = { today: "today", bots: "bots", compute: "compute", settings: "settings" };
  const tabs = r === "chat" ? "" : `<nav class="tabbar">${NAV.map(([id, label]) => `<a class="${section(r) === id ? "on" : ""}" href="#/${id}">${icon(ic[id], 22)}<span>${label}</span>${id === "today" ? `<i class="n">${D.counts.waiting}</i>` : ""}</a>`).join("")}</nav>`;
  return `<div class="app phone v-${r}"><main>${view}</main>${tabs}</div>`;
}
