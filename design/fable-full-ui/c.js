// Direction C, "Rooms": the project first. Each project is a room: a plain page of what is open,
// what waits on you and what got done, with its bot always sitting beside it. Home is the ledger
// of all rooms. The chat can take the stage, and then the project folds to a glance.
import { e, icon, device, topology, initial } from "./kit.js";

const bot = (D, id) => D.bots.find((b) => b.id === id);
const node = (D, id) => D.nodes.find((n) => n.id === id);
const mark = (s) => `<i class="mk ${s}"></i>`;
const state = (D, s) => `<span class="state ${s}">${mark(s)}${D.STATUS[s]}</span>`;
const num = (n) => (n ? `<b>${n}</b>` : `<span class="zero">0</span>`);

function side(route, D) {
  const s = D.settings;
  if (route === "settings") {
    return `<aside class="side"><a class="backl" href="#/bots">${icon("back", 14)}Settings</a><nav>${s.areas.map((a) => `<a class="${a === s.area ? "on" : ""}">${a}</a>`).join("")}</nav></aside>`;
  }
  const link = (b) => `<a class="${b.id === "atlas" && (route === "project" || route === "chat") ? "on" : ""}" href="#/project"><span class="lt">${initial(b.name)}</span>${e(b.name)}${b.state === "needs_you" ? `<i class="cnt">${D.waiting.filter((w) => w.bot === b.id).length}</i>` : b.state === "working" ? mark("working") : ""}</a>`;
  const projects = D.bots.filter((b) => b.kind === "project");
  const others = D.bots.filter((b) => b.kind !== "project");
  return `<aside class="side"><a class="brand" href="#/bots"><svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><path d="M3 3h6v6H3zM11 3h6v6h-6zM3 11h6v6H3z" fill="#0a0a0a"/><path d="M11 11h6v6h-6z" fill="var(--hue)"/></svg><b>Commonplace</b><span>${e(D.ws.name)}${icon("down", 12)}</span></a>
<nav><a class="${route === "bots" ? "on" : ""}" href="#/bots">${icon("today")}Home<i class="cnt">${D.counts.waiting}</i></a><a class="${route === "compute" ? "on" : ""}" href="#/compute">${icon("compute")}Compute</a></nav>
<h4>Projects</h4><nav>${projects.map(link).join("")}<a class="new" href="#/bots">${icon("plus", 14)}New project</a></nav>
<h4>Other bots</h4><nav>${others.map(link).join("")}</nav>
<nav class="end"><a href="#/settings">${icon("settings")}Settings</a><a><span class="lt me">${D.ws.user.initial}</span>${e(D.ws.user.name)}</a></nav></aside>`;
}

function ask(D, w, o) {
  if (o.accepted) return `<div class="ask done">${icon("check")}<b>Accepted.</b><span>Going live on Build box now.</span></div>`;
  return `<div class="ask"><div><b>${e(w.title)}</b><span>${e(w.why)} ${w.facts.map(e).join(" · ")}.</span></div><button class="btn dark" data-accept>${e(w.accept)}</button><button class="btn">${e(w.decline)}</button></div>`;
}

function thread(D, o, compact) {
  const msg = (m) => {
    if (m.role === "you") return `<div class="m you"><span class="who">You · ${m.at}</span><p>${e(m.text)}</p></div>`;
    return `<div class="m bot"><span class="who">Atlas · ${m.at}</span><p>${e(m.text)}</p>
${m.did ? `<ul class="did">${m.did.map((d) => `<li>${icon("check", 14)}${e(d)}</li>`).join("")}</ul>` : ""}
${m.work && !compact ? `<a class="work" href="#/project">${state(D, "needs_you")}<b>${e(m.work.title)}</b><span>${e(m.work.note)}</span></a>` : ""}
${m.waiting ? (compact ? `<a class="jump" href="#/project">${mark("needs_you")}Waiting for your OK, on the left</a>` : ask(D, D.waiting.find((w) => w.id === m.waiting), o)) : ""}
${m.suggest ? `<div class="sug">${m.suggest.map((s) => `<button class="btn">${e(s)}</button>`).join("")}</div>` : ""}</div>`;
  };
  const list = compact ? D.thread.slice(-3) : D.thread;
  return `<div class="thread">${list.map(msg).join("")}</div>
<form class="composer" onsubmit="return false"><input placeholder="Message Atlas" aria-label="Message Atlas">${icon("mic", 18)}<button class="send" aria-label="Send">${icon("send")}</button></form>`;
}

function workTable(D, p) {
  return `<table class="tb"><thead><tr><th>Work</th><th>State</th><th>Where</th><th class="num">Since</th></tr></thead><tbody>
${p.work.map((w) => `<tr><td><b>${e(w.title)}</b><span>${e(w.note)}</span></td><td>${state(D, w.status)}</td><td>${w.where ? e(w.where) : `<span class="zero">–</span>`}</td><td class="num">${w.when ? e(w.when) : `<span class="zero">–</span>`}</td></tr>`).join("")}</tbody></table>`;
}

function projectView(D, o) {
  const p = D.project;
  const w = D.waiting.find((x) => x.bot === p.bot);
  const t = D.tallies.atlas;
  const head = `<header class="ph">${o.phone ? `<a class="hb" href="#/bots" aria-label="Menu">${icon("menu", 20)}</a>` : ""}<div><h1>${e(p.name)}</h1><p>${e(p.job)}. ${D.counts.open} things open, ${t.needs_you} waiting on you.</p></div>${o.phone ? "" : `<span class="stamp">${mark("working")}${D.agents.filter((a) => a.bot === p.bot).length} agent working · ${e(p.site)}</span>`}</header>`;
  const page = `<div class="doc">${head}
<section><h2>Needs you <i>${o.accepted ? 0 : t.needs_you}</i></h2>${ask(D, w, o)}</section>
<section><h2>Open work <i>${D.counts.open}</i></h2>${o.phone ? `<ul class="wl">${p.work.map((x) => `<li><div><b>${e(x.title)}</b><span>${e(x.note)}</span></div>${state(D, x.status)}</li>`).join("")}</ul>` : workTable(D, p)}</section>
<section class="pair"><div><h2>Done this week <i>${D.counts.done}</i></h2><ul class="dl">${p.done.map((d) => `<li><span>${e(d.title)}</span><time>${e(d.when)}</time></li>`).join("")}</ul></div>
<div><h2>Atlas remembers <i>${p.memory.length}</i></h2><ul class="dl">${p.memory.map((m) => `<li><span>${e(m)}</span></li>`).join("")}</ul></div></section></div>`;
  if (o.phone) return `${page}<a class="askbar" href="#/chat"><span>Message Atlas</span>${icon("mic", 18)}<i class="send">${icon("send")}</i></a>`;
  return `${page}<aside class="chat"><header><b>Atlas</b><a href="#/chat">Open wide${icon("chev", 12)}</a></header>${thread(D, o, true)}</aside>`;
}

function chatView(D, o) {
  const p = D.project;
  const t = D.tallies.atlas;
  const head = `<header class="ph">${o.phone ? `<a class="hb" href="#/project" aria-label="Back to the project">${icon("back", 20)}</a>` : ""}<div><h1>Atlas</h1><p>${e(p.job)}</p></div>${o.phone ? "" : `<a class="btn" href="#/project">Back to the project</a>`}</header>`;
  const glance = `<aside class="glance"><h2>At a glance</h2><dl>${[["needs_you", "Needs you"], ["working", "Working"], ["blocked", "Blocked"], ["queued", "Queued"]].map(([k, label]) => `<div><dt>${label}</dt><dd class="${k}">${t[k]}</dd></div>`).join("")}</dl>
<h2>Runs on</h2><ul class="dl"><li><span>Build box</span><em class="up">Online</em></li><li><span>Staging VM</span><em class="warn">Reconnecting</em></li></ul>
<h2>Atlas remembers</h2><ul class="dl">${p.memory.map((m) => `<li><span>${e(m)}</span></li>`).join("")}</ul></aside>`;
  return `<div class="doc wide">${head}${thread(D, o, false)}</div>${o.phone ? "" : glance}`;
}

function home(D, o) {
  const projects = D.bots.filter((b) => b.kind === "project");
  const others = D.bots.filter((b) => b.kind !== "project");
  const cols = [["needs_you", "Needs you"], ["working", "Working"], ["blocked", "Blocked"], ["queued", "Queued"], ["done", "Done this week"]];
  const sum = (k) => projects.reduce((s, b) => s + D.tallies[b.id][k], 0);
  const head = `<header class="ph">${o.phone ? `<a class="hb" aria-label="Menu">${icon("menu", 20)}</a>` : ""}<div><h1>Home</h1><p>${D.counts.waiting} things wait on you across ${projects.length} projects.</p></div>${o.phone ? "" : `<button class="btn dark">New project</button>`}</header>`;
  const needs = `<section><h2>Needs you <i>${D.counts.waiting}</i></h2>${D.waiting.map((w) => (w.kind === "approve" ? ask(D, w, o).replace("<div><b>", `<div><em>${e(bot(D, w.bot).name)}</em><b>`) : `<div class="ask"><div><em>${e(bot(D, w.bot).name)}</em><b>${e(w.title)}</b><span>${e(w.why)}</span></div>${w.options.map((x) => `<button class="btn">${e(x)}</button>`).join("")}</div>`)).join("")}</section>`;
  const table = o.phone
    ? `<ul class="wl">${projects.map((b) => `<li><div><b>${e(b.name)}</b><span>${e(b.job)}</span></div><span class="tl">${D.tallies[b.id].needs_you ? state(D, "needs_you") : D.tallies[b.id].working ? state(D, "working") : state(D, "idle")}</span></li>`).join("")}</ul>`
    : `<table class="tb led"><thead><tr><th>Project</th>${cols.map(([, l]) => `<th class="num">${l}</th>`).join("")}<th>Last word</th></tr></thead><tbody>
${projects.map((b) => `<tr><td><a href="#/project"><b>${e(b.name)}</b><span>${e(b.job)}</span></a></td>${cols.map(([k]) => `<td class="num ${k}">${num(D.tallies[b.id][k])}</td>`).join("")}<td class="lw">${e(b.last)}</td></tr>`).join("")}
<tr class="tot"><td>All projects</td>${cols.map(([k]) => `<td class="num">${sum(k)}</td>`).join("")}<td></td></tr></tbody></table>`;
  return `<div class="doc full">${head}${needs}<section><h2>Projects <i>${projects.length}</i></h2>${table}</section>
<section><h2>Other bots <i>${others.length}</i></h2><ul class="wl ob">${others.map((b) => `<li><div><b>${e(b.name)}</b><span>${e(b.job)}</span></div><span class="lw">${e(b.last)}</span><time>${e(b.ago)}</time></li>`).join("")}</ul></section></div>`;
}

function compute(D, o) {
  const c = D.counts;
  const s = D.selected;
  const words = { up: "Online", warn: "Reconnecting", down: "Offline" };
  const row = (n) => {
    const open = n.id === o.node;
    const list = D.agents.filter((a) => a.machine === n.id);
    return `<li class="${open ? "open" : ""}" data-node="${e(n.id)}"><div class="mr">${device(n.type, 22)}<b>${e(n.name)}</b><span class="state ${n.state}">${mark(n.state)}${words[n.state]}</span></div>
${open && n.id === s.id ? `<nav class="tabs">${s.tabs.map((t) => `<a class="${t === s.tab ? "on" : ""}">${t}</a>`).join("")}</nav><ul class="dl">${list.map((a) => `<li><span>${e(a.task)}</span><time>${e(bot(D, a.bot).name)} · ${e(a.since)}</time></li>`).join("")}</ul><button class="btn">Start an agent here</button>` : open ? `<p class="role">${e(n.role)}</p>` : ""}</li>`;
  };
  const machines = D.nodes.filter((n) => n.role.startsWith("Runner"));
  const head = `<header class="ph">${o.phone ? `<a class="hb" href="#/bots" aria-label="Menu">${icon("menu", 20)}</a>` : ""}<div><h1>Compute</h1><p>${c.up} of ${c.runners} runners online${o.phone ? "" : `, ${c.warn} reconnecting, ${c.down} offline`}. ${c.agents} agents working.</p></div>${o.phone ? "" : `<div class="acts"><button class="btn">Add machine</button><button class="btn">Add site</button><button class="btn dark">Let a bot map it</button></div>`}</header>`;
  const W = o.phone ? o.vw - 32 : o.vw - 240 - 64 - 340 - 24;
  return `<div class="doc full map-pg">${head}<div class="cm"><ul class="ml">${machines.map(row).join("")}${o.phone ? "" : `<li class="sites"><div class="mr">${device("site", 22)}<b>${c.sites} sites</b><span class="state">northwind.example, docs</span></div></li>`}</ul>
<div class="map">${topology({ nodes: D.nodes, links: D.links, selected: o.node, phone: o.phone, W, H: o.phone ? 430 : o.vh - 150 })}<div class="key"><span><i class="kd up"></i>Up</span><span><i class="kd warn"></i>Connecting</span><span><i class="kd down"></i>Down</span></div></div></div></div>`;
}

function settingsView(D, o) {
  const s = D.settings;
  const seg = (c) => (c.locked ? `<span class="fixed">${c.mode === "own" ? "On their own" : "Always asks"}</span>` : `<span class="seg"><i class="${c.mode === "own" ? "on" : ""}">On their own</i><i class="${c.mode === "ask" ? "on" : ""}">Ask me first</i></span>`);
  return `<div class="doc full"><header class="ph">${o.phone ? `<a class="hb" href="#/bots" aria-label="Settings">${icon("back", 20)}</a>` : ""}<div><h1>Approvals</h1><p>What your bots do on their own, and what stops for one tap from you.</p></div></header>
<section class="srow"><div><h2>What bots may do on their own</h2><p>Two kinds always ask. The rest is yours to set, per workspace.</p></div>
<ul class="wl opt">${s.classes.map((c) => `<li><div><b>${e(c.title)}</b><span>${e(c.note)}</span></div>${seg(c)}</li>`).join("")}</ul></section>
<section class="srow"><div><h2>Where a question reaches you</h2><p>Answering it in one place clears it everywhere.</p></div>
<ul class="wl opt">${s.arrive.map((a) => `<li><div><b>${e(a.title)}</b><span>${e(a.note)}</span></div><span class="tg${a.on ? " on" : ""}${a.locked ? " lock" : ""}"></span></li>`).join("")}</ul></section></div>`;
}

const VIEWS = { project: projectView, chat: chatView, bots: home, compute, settings: settingsView };

export function render(route, D, o) {
  const r = VIEWS[route] ? route : "project";
  const view = VIEWS[r](D, o);
  return o.phone ? `<div class="app phone v-${r}">${view}</div>` : `<div class="app v-${r}">${side(r, D)}<main>${view}</main></div>`;
}
