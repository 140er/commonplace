/* Memory map: a force layout on one canvas. No libraries, nothing from the network.
   URL state for renders: ?view=themes|people &open=<node id> &hover=<node id> &q=<filter> &off=<theme,theme> &pin=<id,id> */
(() => {
  "use strict";

  // ---------- Seeded random, so every render has the same map ----------
  let seed = 20261030;
  const rnd = () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const pick = (a) => a[Math.floor(rnd() * a.length)];
  const color = Object.fromEntries(THEMES.map((t) => [t.id, t.color]));
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const fmtDate = (d) => { const [y, m, day] = d.slice(0, 10).split("-"); return `${+day} ${MONTHS[m - 1]} ${y}${d.length > 10 ? " " + d.slice(11) : ""}`; };

  // ---------- Graph ----------
  const nodes = [], byId = {}, links = [];
  const add = (n) => { n.adj = new Set(); nodes.push(n); byId[n.id] = n; return n; };
  const link = (a, b, kind, label) => {
    if (!a || !b || a === b || a.adj.has(b.id)) return;
    links.push({ s: a, t: b, kind, label }); a.adj.add(b.id); b.adj.add(a.id);
  };

  PEOPLE.forEach((p) => add({ id: p.id, kind: "person", name: p.name }));
  CARDS.forEach((c) => add({ ...c, id: "c" + c.id, kind: "card", num: c.id }));

  let n = 0;
  WRITTEN.forEach(([theme, topic, date, src, text, people, cards]) =>
    add({ id: "m" + ++n, kind: "memory", theme, topic, date, src, text, people, cards }));

  const COUNTS = { commonplace: 64, computercraft: 52, home: 40, family: 30, running: 46, reading: 30, money: 26 };
  const fill = (line) => line.replace(/\{([^}]+)\}/g, (_, a) => pick(a.split("|"))).replace(/#/g, () => 2 + Math.floor(rnd() * 90));
  const SRC = ["phone", "claude-code:vm", "codex:vm", "notes", "discord"];
  for (const t of THEMES) {
    const f = FILL[t.id];
    for (let i = 0; i < COUNTS[t.id]; i++) {
      const day = Math.floor(rnd() * 330);
      const dt = new Date(Date.UTC(2025, 11, 1) + day * 864e5);
      const date = dt.toISOString().slice(0, 10) + ` ${String(6 + Math.floor(rnd() * 17)).padStart(2, "0")}:${String(Math.floor(rnd() * 60)).padStart(2, "0")}`;
      const people = rnd() < 0.22 ? [pick(f.people)] : [];
      add({ id: "m" + ++n, kind: "memory", theme: t.id, topic: pick(f.topics), date, src: pick(SRC), text: fill(pick(f.lines)), people, cards: [] });
    }
  }
  const mems = nodes.filter((x) => x.kind === "memory");

  // Links: mentions and card links from the memory itself; nearby memories in the same topic;
  // a few across topics and themes, the way real notes point at each other.
  for (const m of mems) {
    m.people.forEach((p) => link(m, byId[p], "mention"));
    m.cards.forEach((c) => link(m, byId["c" + c], "card"));
  }
  const byTopic = {};
  mems.forEach((m) => (byTopic[m.theme + "/" + m.topic] ||= []).push(m));
  Object.values(byTopic).forEach((list) => {
    list.sort((a, b) => a.date.localeCompare(b.date));
    list.forEach((m, i) => {
      if (i) link(m, list[Math.max(0, i - 1 - Math.floor(rnd() * Math.min(i, 4)))], "near");
      if (i > 1 && rnd() < 0.5) link(m, list[i - 2], "near");
      if (i > 2 && rnd() < 0.35) link(m, list[Math.floor(rnd() * i)], "near");
    });
  });
  mems.forEach((m) => {
    if (rnd() < 0.18) link(m, pick(mems.filter((o) => o.theme === m.theme)), "near");
    if (rnd() < 0.035) link(m, pick(mems), "tunnel");
  });
  // Written memories in the same topic point at each other; facts between people are edges.
  const written = mems.slice(0, WRITTEN.length);
  written.forEach((a) => written.forEach((b) => { if (a.theme === b.theme && a.topic === b.topic) link(a, b, "near"); }));
  FACTS.forEach(([s, p, o, from, to]) => { if (byId[o] && byId[s]) link(byId[s], byId[o], "fact", p); });

  nodes.forEach((x) => {
    x.deg = x.adj.size;
    x.r = x.kind === "person" ? 7 : x.kind === "card" ? 5 : 3.2 + Math.min(4.5, Math.sqrt(x.deg) * 1.1);
    if (x.kind !== "memory") x.theme = dominantTheme(x);
  });
  function dominantTheme(x) {
    const c = {};
    x.adj.forEach((id) => { const t = byId[id].theme; if (t) c[t] = (c[t] || 0) + 1; });
    return Object.entries(c).sort((a, b) => b[1] - a[1])[0]?.[0] || "commonplace";
  }

  // ---------- Layout ----------
  const TI = Object.fromEntries(THEMES.map((t, i) => [t.id, i]));
  const ang = (i, k) => (i / k) * Math.PI * 2 - Math.PI / 2;
  const themeAnchor = (t) => { const a = ang(TI[t], THEMES.length); return [Math.cos(a) * 360, Math.sin(a) * 300]; };
  const PI_ = Object.fromEntries(PEOPLE.map((p, i) => [p.id, i]));
  const personAnchor = (p) => { const a = ang(PI_[p], PEOPLE.length); return [Math.cos(a) * 380, Math.sin(a) * 300]; };

  nodes.forEach((x) => {
    const [ax, ay] = themeAnchor(x.theme);
    x.x = ax * 0.5 + (rnd() - 0.5) * 220; x.y = ay * 0.5 + (rnd() - 0.5) * 220; x.vx = x.vy = 0;
  });

  let view = "map", alpha = 1;
  const REST = { mention: 70, card: 46, near: 22, tunnel: 120, fact: 110 };

  function target(x) {
    if (view === "themes") {
      if (x.kind === "person") return null;
      return [...themeAnchor(x.theme), 0.1];
    }
    if (view === "people") {
      if (x.kind === "person") return [...personAnchor(x.id), 0.5];
      const p = x.kind === "memory" ? x.people[0] : null;
      if (p) { const [px, py] = personAnchor(p); return [px * 0.78, py * 0.78, 0.08]; }
      return [0, 0, 0.03];
    }
    return [0, 0, 0.02];
  }

  function tick() {
    const N = nodes.length;
    for (let i = 0; i < N; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < N; j++) {
        const b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy;
        if (d2 > 90000) continue;
        if (d2 < 0.01) { dx = rnd() - 0.5; dy = rnd() - 0.5; d2 = 0.5; }
        const d = Math.sqrt(d2);
        let f = (-38 * alpha) / d2;
        const min = a.r + b.r + 4;
        if (d < min) f -= ((min - d) / d) * 0.5;
        const fx = dx * f, fy = dy * f;
        a.vx += fx; a.vy += fy; b.vx -= fx; b.vy -= fy;
      }
    }
    for (const l of links) {
      const a = l.s, b = l.t;
      const dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
      const rest = view === "people" && l.kind === "fact" ? 220 : REST[l.kind];
      const k = ((d - rest) / d) * alpha * (0.6 / Math.min(a.deg, b.deg));
      const w = a.deg / (a.deg + b.deg);
      a.vx += dx * k * (1 - w); a.vy += dy * k * (1 - w); b.vx -= dx * k * w; b.vy -= dy * k * w;
    }
    for (const x of nodes) {
      const t = target(x);
      if (t) { x.vx += (t[0] - x.x) * t[2] * alpha; x.vy += (t[1] - x.y) * t[2] * alpha; }
      if (x.fx != null) { x.x = x.fx; x.y = x.fy; x.vx = x.vy = 0; continue; }
      x.vx *= 0.58; x.vy *= 0.58; x.x += x.vx; x.y += x.vy;
    }
    alpha = Math.max(0, alpha * 0.985);
  }
  const settle = (ticks) => { for (let i = 0; i < ticks; i++) tick(); };

  // ---------- View state ----------
  const $ = (s) => document.querySelector(s);
  const stage = $("#stage"), canvas = $("#map"), ctx = canvas.getContext("2d"), tip = $("#tip"), panel = $("#panel"), pct = $("#pct");
  const off = new Set(), match = { q: "", set: null, week: null };
  let cam = { x: 0, y: 0, k: 1 }, W = 0, H = 0, dpr = 1;
  let hover = null, open = null, dragging = null, touched = false;

  const visible = (x) => !off.has(x.theme) || x.kind === "person";
  const phone = () => matchMedia("(max-width: 640px)").matches;
  const panelW = () => (open && !phone() ? 488 + 6 : 0);
  const sheetH = () => (open && phone() ? H * 0.58 : 0);

  function resize() {
    const r = stage.getBoundingClientRect();
    W = r.width; H = r.height; dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    draw();
  }

  function fit() {
    const vis = nodes.filter(visible);
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    vis.forEach((x) => { x0 = Math.min(x0, x.x); y0 = Math.min(y0, x.y); x1 = Math.max(x1, x.x); y1 = Math.max(y1, x.y); });
    const left = phone() ? 0 : 200, w = W - left - panelW() - (phone() ? 0 : 48), h = H - sheetH() - (phone() ? 104 : 68);
    const pad = phone() ? 28 : 56;
    cam.k = Math.min(2.4, Math.min((w - pad * 2) / (x1 - x0 || 1), (h - pad * 2) / (y1 - y0 || 1)));
    cam.x = left + w / 2 - ((x0 + x1) / 2) * cam.k;
    cam.y = h / 2 - ((y0 + y1) / 2) * cam.k;
  }
  // Pan only when the node would sit under the panel or off screen.
  function centerOn(x, force) {
    const w = W - panelW(), h = H - sheetH(), [sx, sy] = toScreen(x), m = 80;
    if (!force && sx > m && sx < w - m && sy > m && sy < h - m) return;
    cam.x = w / 2 - x.x * cam.k; cam.y = h / 2 - x.y * cam.k;
  }
  function zoomAt(f, sx, sy) {
    const k = Math.max(0.2, Math.min(5, cam.k * f));
    touched = true;
    cam.x = sx - ((sx - cam.x) * k) / cam.k; cam.y = sy - ((sy - cam.y) * k) / cam.k; cam.k = k;
    draw();
  }
  const toScreen = (x) => [x.x * cam.k + cam.x, x.y * cam.k + cam.y];

  // ---------- Drawing ----------
  const css = getComputedStyle(document.documentElement);
  const C = (v) => css.getPropertyValue(v).trim();
  const INK = C("--ink"), ACC = C("--accent"), QUIET = C("--quiet"), S2 = C("--surface-2"), S3 = C("--surface-3"), TERM = C("--term"), WAIT = C("--wait");
  const FONT = (w, s) => `${w} ${s}px "Geist Mono", ui-monospace, monospace`;

  function focusSet() {
    const f = hover || open;
    if (!f) return null;
    const s = new Set(f.adj); s.add(f.id); return s;
  }

  function draw() {
    if (!W) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = TERM; ctx.fillRect(0, 0, W, H);

    // The site's chunky grid, as dots that move with the map.
    const g = 48 * cam.k;
    if (g > 14) {
      ctx.fillStyle = "#151715";
      for (let x = ((cam.x % g) + g) % g; x < W; x += g)
        for (let y = ((cam.y % g) + g) % g; y < H; y += g) ctx.fillRect(Math.round(x) - 1, Math.round(y) - 1, 2, 2);
    }

    ctx.save(); ctx.translate(cam.x, cam.y); ctx.scale(cam.k, cam.k);
    const fs = focusSet(), f = hover || open, q = peek || match.set;
    const lit = (x) => (fs ? fs.has(x.id) : q ? q.has(x.id) : true);
    drawWhen(fs, q);

    // Theme names over their clusters in the Themes layout.
    if (view === "themes") {
      ctx.textAlign = "center";
      for (const t of THEMES) {
        if (off.has(t.id)) continue;
        const ms = mems.filter((m) => m.theme === t.id);
        const cx = ms.reduce((s, m) => s + m.x, 0) / ms.length, cy = ms.reduce((s, m) => s + m.y, 0) / ms.length;
        const ds = ms.map((m) => Math.hypot(m.x - cx, m.y - cy)).sort((a, b) => a - b);
        const far = ds[Math.floor(ds.length * 0.85)], d = Math.hypot(cx, cy) || 1;
        const lx = cx + (cx / d) * (far + 24 / cam.k), ly = cy + (cy / d) * (far + 24 / cam.k);
        ctx.textAlign = Math.abs(cx / d) < 0.35 ? "center" : cx > 0 ? "left" : "right";
        ctx.textBaseline = "middle";
        ctx.font = FONT(700, 20 / cam.k); ctx.fillStyle = t.color; ctx.globalAlpha = fs || q ? 0.35 : 1;
        ctx.font = FONT(700, 20 / cam.k);
        const tw = ctx.measureText(t.id).width, al = ctx.textAlign;
        const x0 = al === "center" ? lx - tw / 2 : al === "right" ? lx - tw : lx;
        const minX = (220 - cam.x) / cam.k, maxX = (W - panelW() - 56 - cam.x) / cam.k - tw;
        const minY = (24 - cam.y) / cam.k, maxY = (H - 90 - cam.y) / cam.k;
        ctx.textAlign = "left";
        ctx.fillText(t.id, Math.min(maxX, Math.max(minX, x0)), Math.min(maxY, Math.max(minY, ly)));
      }
      ctx.textAlign = "left"; ctx.globalAlpha = 1;
    }

    // Links: quiet hairlines; the focused node's links in orange.
    ctx.lineWidth = 1 / cam.k;
    for (const l of links) {
      if (!visible(l.s) || !visible(l.t)) continue;
      const hot = f && (l.s === f || l.t === f);
      if (hot) continue;
      const on = lit(l.s) && lit(l.t);
      ctx.strokeStyle = l.kind === "fact" ? "rgba(232,233,229,0.22)" : "rgba(232,233,229,0.09)";
      ctx.globalAlpha = fs || q ? (on ? 0.6 : 0.25) : 1;
      ctx.beginPath(); ctx.moveTo(l.s.x, l.s.y); ctx.lineTo(l.t.x, l.t.y); ctx.stroke();
    }
    ctx.globalAlpha = 1;
    if (f) {
      ctx.strokeStyle = ACC; ctx.lineWidth = 1.5 / cam.k;
      for (const l of links) if ((l.s === f || l.t === f) && visible(l.s) && visible(l.t)) {
        ctx.beginPath(); ctx.moveTo(l.s.x, l.s.y); ctx.lineTo(l.t.x, l.t.y); ctx.stroke();
      }
    }

    // Nodes: squares. Memories in their theme's colour, people in ink, cards as dark tiles.
    const rank = (x) => (lit(x) ? 2 : 0) + (x.kind === "memory" ? 0 : 1);
    const order = [...nodes].sort((a, b) => rank(a) - rank(b));
    for (const x of order) {
      if (!visible(x)) continue;
      const on = lit(x);
      ctx.globalAlpha = on ? 1 : 0.22;
      const s = x.r;
      if (x.kind === "memory") ctx.fillStyle = color[x.theme];
      else if (x.kind === "person") ctx.fillStyle = INK;
      else ctx.fillStyle = S3;
      if (x === f) ctx.fillStyle = ACC;
      ctx.fillRect(x.x - s, x.y - s, s * 2, s * 2);
      if (x.kind === "card") { ctx.fillStyle = x === f ? TERM : INK; ctx.fillRect(x.x - s + 2, x.y - 1, s * 2 - 4, 2); }
      if (x.fx != null) { ctx.fillStyle = ACC; ctx.fillRect(x.x + s - 1, x.y - s - 3, 4, 4); }
      if (x === open) brackets(x);
    }
    ctx.globalAlpha = 1;
    ctx.restore();

    // Labels at screen size: people always, cards when close enough, focused neighbours by name.
    ctx.textBaseline = "middle";
    const taken = [];
    const free = (r) => taken.every((t) => r[0] > t[0] + t[2] || r[0] + r[2] < t[0] || r[1] > t[1] + t[3] || r[1] + r[3] < t[1]);
    for (const x of nodes) {
      if (!visible(x) || x.kind === "memory") continue;
      if (x.kind === "card" && cam.k < 0.7 && !(fs && fs.has(x.id))) continue;
      const on = lit(x);
      const [sx, sy] = toScreen(x);
      const label = x.kind === "person" ? x.name : "#" + x.num;
      ctx.font = FONT(x.kind === "person" ? 700 : 500, x.kind === "person" ? 12 : 11);
      const tw = ctx.measureText(label).width, gap = x.r * cam.k + 6;
      const spots = [[sx + gap, sy], [sx - gap - tw, sy], [sx - tw / 2, sy + gap + 6], [sx - tw / 2, sy - gap - 6]];
      const spot = spots.find(([a, b]) => free([a - 3, b - 9, tw + 6, 18]));
      if (!spot) continue;
      const [lx, ly] = spot;
      taken.push([lx - 3, ly - 9, tw + 6, 18]);
      ctx.globalAlpha = on ? 1 : 0.3;
      ctx.fillStyle = TERM; ctx.fillRect(lx - 3, ly - 9, tw + 6, 18);
      ctx.fillStyle = x === f ? ACC : x.kind === "person" ? INK : QUIET;
      ctx.fillText(label, lx, ly + 0.5);
    }
    ctx.globalAlpha = 1;
    pct.textContent = Math.round(cam.k * 100) + "%";
    placeTip();
  }

  function brackets(x) {
    const s = x.r + 5, a = 4;
    ctx.strokeStyle = INK; ctx.lineWidth = 1.5 / cam.k; ctx.globalAlpha = 1;
    ctx.beginPath();
    for (const [dx, dy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      const cx = x.x + dx * s, cy = x.y + dy * s;
      ctx.moveTo(cx, cy - dy * a); ctx.lineTo(cx, cy); ctx.lineTo(cx - dx * a, cy);
    }
    ctx.stroke();
  }

  // ---------- Hover read-out ----------
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  function placeTip() {
    const x = hover;
    if (!x || x === open || x.kind === "person" || dragging?.moved) { tip.hidden = true; return; }
    if (x.kind === "memory") { tip.className = "tip"; tip.innerHTML = `<b>${x.theme} · ${fmtDate(x.date).slice(0, -6)}</b><span class="t">${esc(x.text)}</span>`; }
    else { tip.className = "tip ink"; tip.innerHTML = `<b>#${x.num} · ${x.state}</b><span class="t">${esc(x.title)}</span>`; }
    tip.hidden = false;
    const [sx, sy] = toScreen(x), off_ = x.r * cam.k + 12;
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    let lx = sx - 6, ty = sy - th - off_;
    if (lx + tw > W - panelW() - 8) lx = sx + 6 - tw;
    if (ty < 8) ty = sy + off_;
    tip.style.left = lx + "px"; tip.style.top = ty + "px";
  }

  // ---------- Panel ----------
  const sq = (t) => `<i class="sq" style="background:${color[t]}"></i>`;
  const memRow = (m) => `<li data-go="${m.id}">${sq(m.theme)}<span class="txt">${esc(m.text)}</span><time>${fmtDate(m.date).slice(0, -11).trim()}</time></li>`;
  const sec = (title, count, body) => (count ? `<section class="sec"><h3><b>${title}</b>${count}</h3>${body}</section>` : "");
  const neighbours = (x, kind) => [...x.adj].map((id) => byId[id]).filter((o) => o.kind === kind);
  const name = (id) => byId[id]?.name || id;
  // One line per fact, read as a sentence about this person: "lives in Utrecht", "Mila trains with Sam".
  const factRows = (pid) => FACTS.filter((f) => f[0] === pid || f[2] === pid).map(([s, p, o, from, to]) => {
    const line = s === pid
      ? `<span class="p">${esc(p)}</span> <span class="o">${esc(byId[o] ? name(o) : o)}</span>`
      : `<span class="o">${esc(name(s))}</span> <span class="p">${esc(p)}</span> ${esc(name(pid))}`;
    const when = from ? `${from} → ${to || ""}` : "";
    return `<li class="${to ? "ended" : ""}"><span>${line}</span><time>${when}</time></li>`;
  }).join("");
  const byDate = (a, b) => b.date.localeCompare(a.date);

  function renderPanel(x) {
    let top = "", rest = "";
    const close = `<span class="right"><button class="x" data-close aria-label="Close">×</button></span>`;
    if (x.kind === "memory") {
      top = `<div class="bar">${sq(x.theme)}<b>${x.theme}</b>/ ${x.topic}${close}</div>
        <div class="hit"><p class="q">${esc(x.text)}</p><p class="stamp"><span>jeffrey</span> · <span>${fmtDate(x.date)}</span> · <span>${x.src}</span></p></div>`;
      const ppl = neighbours(x, "person"), cards = neighbours(x, "card"), rel = neighbours(x, "memory").sort(byDate);
      rest = sec("people", ppl.length, `<ul class="rows">${ppl.map((p) => {
        const f = FACTS.find((f) => f[0] === p.id && !f[4] && f[3]) || FACTS.find((f) => f[0] === p.id);
        return `<li data-go="${p.id}"><span class="who">${esc(p.name)}</span><span class="txt">${f ? esc(f[1] + " " + (byId[f[2]] ? name(f[2]) : f[2])) : ""}</span><span class="k">${p.deg}</span></li>`;
      }).join("")}</ul>`)
        + sec("cards", cards.length, `<ul class="rows">${cards.map(cardRow).join("")}</ul>`)
        + sec("linked", rel.length, `<ul class="rows">${rel.map(memRow).join("")}</ul>`);
    } else if (x.kind === "person") {
      const ms = neighbours(x, "memory").sort(byDate), ppl = neighbours(x, "person");
      top = `<div class="bar"><b>person</b>${close}</div><h2>${esc(x.name)}<span class="n">${ms.length}</span></h2><ul class="facts">${factRows(x.id)}</ul>`;
      rest = sec("people", ppl.length, `<ul class="rows">${ppl.map((p) => `<li data-go="${p.id}"><span class="who">${esc(p.name)}</span><span class="txt"></span><span class="k">${p.deg}</span></li>`).join("")}</ul>`)
        + sec("memories", ms.length, `<ul class="rows">${ms.map(memRow).join("")}</ul>`);
    } else {
      const ms = neighbours(x, "memory").sort(byDate);
      top = `<div class="bar"><span class="id">#${x.num}</span>${x.board}<span class="state ${x.state}">${x.state}</span>${close}</div><h2>${esc(x.title)}</h2>`;
      rest = sec("memories", ms.length, `<ul class="rows">${ms.map(memRow).join("")}</ul>`);
    }
    panel.innerHTML = `<div class="tile top">${top}</div><div class="tile grow">${rest}</div>`;
  }
  const cardRow = (c) => `<li data-go="${c.id}"><span class="id">#${c.num}</span><span class="txt">${esc(c.title)}</span><span class="state ${c.state}">${c.state}</span></li>`;

  function openNode(x, center = true) {
    open = x;
    if (!x) { panel.hidden = true; draw(); return; }
    renderPanel(x); panel.hidden = false; panel.querySelector(".grow").scrollTop = 0;
    if (center) centerOn(x);
    draw();
  }
  panel.addEventListener("click", (e) => {
    if (e.target.closest("[data-close]")) return openNode(null);
    const go = e.target.closest("[data-go]");
    if (go) { hover = null; openNode(byId[go.dataset.go]); }
  });

  // ---------- Pointer: hover, click to open, drag to pin, drag the background to pan ----------
  function hit(sx, sy) {
    const wx = (sx - cam.x) / cam.k, wy = (sy - cam.y) / cam.k;
    let best = null, bd = Infinity;
    for (const x of nodes) {
      if (!visible(x)) continue;
      const d = Math.max(Math.abs(x.x - wx), Math.abs(x.y - wy)), reach = x.r + 5 / cam.k;
      if (d < reach && d < bd) { best = x; bd = d; }
    }
    return best;
  }
  const local = (e) => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };

  canvas.addEventListener("pointerdown", (e) => {
    const [sx, sy] = local(e), x = hit(sx, sy);
    canvas.setPointerCapture(e.pointerId);
    dragging = { node: x, sx, sy, cx: cam.x, cy: cam.y, moved: false };
    touched = true;
    canvas.classList.add("drag");
  });
  canvas.addEventListener("pointermove", (e) => {
    const [sx, sy] = local(e);
    if (dragging) {
      if (Math.abs(sx - dragging.sx) + Math.abs(sy - dragging.sy) > 3) dragging.moved = true;
      if (!dragging.moved) return;
      if (dragging.node) {
        const x = dragging.node;
        x.fx = (sx - cam.x) / cam.k; x.fy = (sy - cam.y) / cam.k;
        alpha = Math.max(alpha, 0.25); run();
      } else { cam.x = dragging.cx + sx - dragging.sx; cam.y = dragging.cy + sy - dragging.sy; draw(); }
      return;
    }
    const x = hit(sx, sy);
    if (x !== hover) { hover = x; canvas.classList.toggle("over", !!x); draw(); }
  });
  canvas.addEventListener("pointerup", () => {
    const d = dragging; dragging = null; canvas.classList.remove("drag");
    if (!d) return;
    if (!d.moved) openNode(d.node ? (d.node === open ? null : d.node) : null, false);
    else draw();
  });
  canvas.addEventListener("pointerleave", () => { if (!dragging && hover) { hover = null; draw(); } });
  // Double-click a pinned node to let it go.
  canvas.addEventListener("dblclick", (e) => {
    const x = hit(...local(e));
    if (x && x.fx != null) { x.fx = x.fy = null; alpha = Math.max(alpha, 0.2); run(); }
  });
  canvas.addEventListener("wheel", (e) => { e.preventDefault(); zoomAt(Math.exp(-e.deltaY * 0.0015), ...local(e)); }, { passive: false });

  document.querySelector(".zoom").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const z = b.dataset.zoom;
    if (z === "fit") { fit(); draw(); } else zoomAt(z === "in" ? 1.25 : 0.8, (W - panelW()) / 2, H / 2);
  });

  // ---------- Filter, themes, layout ----------
  const qIn = $("#q"), hits = $("#hits");
  function setQuery(v) { match.q = v.trim().toLowerCase(); refilter(); }
  function refilter() {
    if (!match.q && match.week == null) { match.set = null; hits.textContent = ""; draw(); return; }
    match.set = new Set();
    for (const x of nodes) {
      if (match.week != null && x.week !== match.week) continue;
      const hay = [x.text, x.name, x.title, x.theme, x.topic, ...(x.people || []).map(name)].join(" ").toLowerCase();
      if (hay.includes(match.q)) match.set.add(x.id);
    }
    hits.textContent = [...match.set].filter((id) => byId[id].kind === "memory").length;
    draw();
  }

  // ---------- When: memories per week, a strip under the map ----------
  // Hover a week to light its memories; click to keep it as a filter. Lit part of each bar follows
  // whatever is lit on the map (a hovered node's links, the filter), in orange.
  const T0 = Date.UTC(2025, 11, 1), WEEKS = 48;
  mems.forEach((m) => (m.week = Math.min(WEEKS - 1, Math.floor((Date.parse(m.date.slice(0, 10)) - T0) / (7 * 864e5)))));
  const when = $("#when");
  const weekMax = Math.max(...Array.from({ length: WEEKS }, (_, w) => mems.filter((m) => m.week === w).length));
  let peek = null;
  when.innerHTML = Array.from({ length: WEEKS }, (_, w) => {
    const d = new Date(T0 + w * 7 * 864e5), first = d.getUTCDate() <= 7;
    return `<button data-week="${w}" aria-label="Week of ${fmtDate(d.toISOString().slice(0, 10))}"><i class="all"></i><i class="lit"></i>${first ? `<span>${MONTHS[d.getUTCMonth()]}</span>` : ""}</button>`;
  }).join("");
  const bars = [...when.children];
  function drawWhen(fs, q) {
    const on = fs || q;
    const all = new Array(WEEKS).fill(0), lit = new Array(WEEKS).fill(0);
    for (const m of mems) {
      if (!visible(m)) continue;
      all[m.week]++;
      if (on && on.has(m.id)) lit[m.week]++;
    }
    bars.forEach((b, w) => {
      b.firstChild.style.height = (all[w] / weekMax) * 100 + "%";
      b.children[1].style.height = (lit[w] / weekMax) * 100 + "%";
      b.classList.toggle("sel", match.week === w);
    });
    when.classList.toggle("focus", !!on);
  }
  when.addEventListener("pointerover", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const w = +b.dataset.week;
    peek = new Set(mems.filter((m) => m.week === w).map((m) => m.id)); draw();
  });
  when.addEventListener("pointerleave", () => { peek = null; draw(); });
  when.addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const w = +b.dataset.week;
    match.week = match.week === w ? null : w; peek = null; refilter();
  });
  qIn.addEventListener("input", () => setQuery(qIn.value));
  qIn.addEventListener("keydown", (e) => { if (e.key === "Escape") { qIn.value = ""; setQuery(""); qIn.blur(); } });
  document.addEventListener("keydown", (e) => {
    if (e.target === qIn) return;
    if (e.key === "/") { e.preventDefault(); qIn.focus(); }
    else if (e.key === "Escape") openNode(null);
    else if (e.key === "+" || e.key === "=") zoomAt(1.25, (W - panelW()) / 2, H / 2);
    else if (e.key === "-") zoomAt(0.8, (W - panelW()) / 2, H / 2);
    else if (e.key === "0") { fit(); draw(); }
  });

  const legend = $("#legend");
  legend.innerHTML = THEMES.map((t) => `<li><button data-theme="${t.id}" aria-pressed="true"><i style="background:${t.color}"></i>${t.id}<span class="c">${mems.filter((m) => m.theme === t.id).length}</span></button></li>`).join("");
  legend.addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const t = b.dataset.theme;
    off.has(t) ? off.delete(t) : off.add(t);
    b.setAttribute("aria-pressed", String(!off.has(t)));
    if (open && !visible(open)) openNode(null);
    draw();
  });

  document.querySelectorAll(".views button").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  function setView(v, animate = true) {
    view = v;
    document.querySelectorAll(".views button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === v)));
    alpha = 1;
    if (animate) { refit = true; run(); }
  }

  // ---------- Animation ----------
  let raf = 0, refit = false;
  function run() {
    if (raf) return;
    const step = () => {
      tick(); tick();
      if (refit) fit();
      draw();
      raf = alpha > 0.02 || dragging ? requestAnimationFrame(step) : 0;
      if (!raf) refit = false;
    };
    raf = requestAnimationFrame(step);
  }

  // ---------- Start, from the URL ----------
  const P = new URLSearchParams(location.search);
  $("#count").textContent = mems.length;
  if (P.get("view")) setView(P.get("view"), false);
  settle(420);
  (P.get("off") || "").split(",").filter(Boolean).forEach((t) => legend.querySelector(`[data-theme="${t}"]`)?.click());
  (P.get("pin") || "").split(",").filter(Boolean).forEach((id) => { const x = byId[id]; if (x) { x.fx = x.x; x.fy = x.y; } });
  if (P.get("week")) match.week = +P.get("week");
  if (P.get("q") || P.get("week")) { qIn.value = P.get("q") || ""; setQuery(qIn.value); }

  // Refit on every resize until the user has moved the map themselves.
  new ResizeObserver(() => { resize(); if (!touched) start(); }).observe(stage);
  function start() {
    fit();
    if (P.get("open") && byId[P.get("open")]) {
      const x = byId[P.get("open")];
      open = x; renderPanel(x); panel.hidden = false;
      fit();
      if (x.kind === "memory") { cam.k = Math.max(cam.k, phone() ? 0.9 : 1.2); centerOn(x, true); }
    }
    if (P.get("hover") && byId[P.get("hover")]) hover = byId[P.get("hover")];
    draw();
  }

  document.fonts?.ready.then(draw);
})();
