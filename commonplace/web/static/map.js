/* Adapted from design/memory-map-mix/map.js: seeded force layout, canvas rendering,
   pan/zoom, theme and people layouts, neighbour focus and link pulse. Only REST
   nodes and stored links enter the graph; no inferred links or sample memories. */
import { escape as esc } from "./api.js";

export function mountMap(root, graph, onOpen) {
  let disposed = false;
  const controller = new AbortController();
  const listen = (element, kind, handler, opts = {}) => element.addEventListener(kind, handler, { ...opts, signal: controller.signal });
  let seed = 20261030;
  const rnd = () => {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const palette = ["#f7941d", "#7fd18b", "#a9bdf5", "#d9dbd6"];
  const THEMES = (graph.themes || []).map((t, i) => ({ ...t, color: palette[i % palette.length], index: i }));
  for (const x of graph.nodes) if (x.theme && !THEMES.some((t) => t.id === x.theme)) THEMES.push({ id: x.theme, label: x.theme, color: palette[THEMES.length % palette.length], index: THEMES.length });
  const color = Object.fromEntries(THEMES.map((t) => [t.id, t.color]));
  const PEOPLE = graph.nodes.filter((x) => x.kind === "person");
  const nodes = graph.nodes.map((x) => ({ ...x, id: String(x.id), name: x.label, text: x.label, title: x.label, num: String(x.id).replace(/^(card:|c)/, ""), theme: x.theme || THEMES[0]?.id || "shared", adj: new Set(), people: [] }));
  const byId = Object.fromEntries(nodes.map((x) => [x.id, x]));
  const links = graph.links.map((l) => ({ ...l, s: byId[l.source], t: byId[l.target] })).filter((l) => l.s && l.t && l.s !== l.t);
  for (const l of links) { l.s.adj.add(l.t.id); l.t.adj.add(l.s.id); }
  for (const x of nodes) {
    x.deg = x.adj.size; x.r = x.kind === "person" ? 7 : x.kind === "card" ? 5 : 3.2 + Math.min(4.5, Math.sqrt(x.deg) * 1.1);
    x.people = [...x.adj].filter((id) => byId[id].kind === "person");
  }
  const mems = nodes.filter((x) => x.kind === "memory");
  const weeks = graph.weeks || [];
  let peek = null;
  // ---------- Layout ----------
  const TI = Object.fromEntries(THEMES.map((t, i) => [t.id, i]));
  const ang = (i, k) => (i / k) * Math.PI * 2 - Math.PI / 2;
  const themeAnchor = (t) => { const a = ang(TI[t] || 0, Math.max(1, THEMES.length)); return [Math.cos(a) * 360, Math.sin(a) * 300]; };
  const PI_ = Object.fromEntries(PEOPLE.map((p, i) => [p.id, i]));
  const personAnchor = (p) => { const a = ang(PI_[p] || 0, Math.max(1, PEOPLE.length)); return [Math.cos(a) * 380, Math.sin(a) * 300]; };

  nodes.forEach((x) => {
    const [ax, ay] = themeAnchor(x.theme);
    x.x = ax * 0.5 + (rnd() - 0.5) * 220; x.y = ay * 0.5 + (rnd() - 0.5) * 220; x.vx = x.vy = 0;
  });

  let view = "map", alpha = 1;
  const REST = { card: 46, fact: 110, theme: 70 };

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
      const rest = view === "people" && l.kind === "fact" ? 220 : (REST[l.kind] || 70);
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
  const $ = (s) => root.querySelector(s);
  const stage = $("#stage"), canvas = $("#map"), ctx = canvas.getContext("2d"), tip = $("#tip"), panel = document.querySelector("#card-panel"), pct = $("#pct");
  const off = new Set(), match = { q: "", set: null, week: null };
  let cam = { x: 0, y: 0, k: 1 }, W = 0, H = 0, dpr = 1;
  let hover = null, open = null, dragging = null, touched = false;

  const visible = (x) => (!off.has(x.theme) || x.kind === "person") && (!match.set || match.set.has(x.id));
  const phone = () => matchMedia("(max-width: 640px)").matches;
  const panelW = () => (!panel.hidden && !phone() ? 520 + 6 : 0);
  const sheetH = () => 0;

  function resize() {
    const r = stage.getBoundingClientRect();
    W = r.width; H = r.height; dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    draw();
  }

  function fit() {
    const vis = nodes.filter(visible);
    if (!vis.length || !W || !H) { cam = { x: W / 2, y: H / 2, k: 1 }; return; }
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
  // A short ID-style code per node, stable across renders.
  const code = (x) => {
    let h = 2166136261;
    for (const ch of x.id) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
    const hx = (h >>> 0).toString(16).toUpperCase().padStart(8, "0");
    const pre = x.kind === "memory" ? "MEM" : x.kind === "person" ? "PRS" : "CRD";
    return `${pre}-${hx.slice(0, 4)}-${hx.slice(4, 7)}`;
  };
  // Focus timing: links draw out from the focused node, then a pulse runs along them.
  let focusAt = 0, lastFocus = null;
  const DRAW_MS = 420, PULSE_MS = 1500;

  function focusSet() {
    const f = hover || open;
    if (!f) return null;
    const s = new Set(f.adj); s.add(f.id); return s;
  }

  function draw() {
    if (!W || disposed) return;
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
    if (f !== lastFocus) { lastFocus = f; focusAt = performance.now(); }
    const age = performance.now() - focusAt;
    const grow = still ? 1 : Math.min(1, age / DRAW_MS), ease = 1 - Math.pow(1 - grow, 3);
    const lit = (x) => (fs ? fs.has(x.id) : q ? q.has(x.id) : true);
    drawWhen(fs, q);

    // Theme names over their clusters in the Themes layout.
    if (view === "themes") {
      ctx.textAlign = "center";
      for (const t of THEMES) {
        if (off.has(t.id)) continue;
        const ms = mems.filter((m) => m.theme === t.id);
        if (!ms.length) continue;
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

    // Links: quiet hairlines; the focused node's links draw out in orange, then a light pulse runs along them.
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
      const hot = links.filter((l) => (l.s === f || l.t === f) && visible(l.s) && visible(l.t));
      const ends = (l) => (l.s === f ? [l.s, l.t] : [l.t, l.s]);
      ctx.strokeStyle = ACC; ctx.lineWidth = 1.5 / cam.k;
      for (const l of hot) {
        const [a, b] = ends(l);
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(a.x + (b.x - a.x) * ease, a.y + (b.y - a.y) * ease); ctx.stroke();
      }
      if (!still && grow >= 1) {
        const ph = ((age - DRAW_MS) % PULSE_MS) / PULSE_MS;
        ctx.lineWidth = 2.6 / cam.k;
        for (const l of hot) {
          const [a, b] = ends(l), len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
          const seg = Math.min(0.45, 30 / len), t1 = ph * (1 + seg), t0 = Math.max(0, t1 - seg), t1c = Math.min(1, t1);
          if (t0 >= 1) continue;
          const at = (t) => [a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t];
          const g = ctx.createLinearGradient(...at(t0), ...at(t1c));
          g.addColorStop(0, "rgba(232,233,229,0)"); g.addColorStop(1, INK);
          ctx.strokeStyle = g;
          ctx.beginPath(); ctx.moveTo(...at(t0)); ctx.lineTo(...at(t1c)); ctx.stroke();
          if (t1 <= 1) { const [hx, hy] = at(t1), s = 2 / cam.k; ctx.fillStyle = INK; ctx.fillRect(hx - s, hy - s, s * 2, s * 2); }
        }
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
    // ID tag on the focused node: a leader line to a dark block with the code.
    if (f) {
      const [sx, sy] = toScreen(f), gap = f.r * cam.k + 6, c = code(f);
      ctx.font = FONT(500, 11); const cw = ctx.measureText(c).width;
      const tx = sx + gap + 18, ty = sy - gap - 20;
      ctx.globalAlpha = ease; ctx.strokeStyle = ACC; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(sx + gap - 2, sy - gap + 2); ctx.lineTo(tx, ty + 9); ctx.stroke();
      ctx.fillStyle = TERM; ctx.fillRect(tx, ty, cw + 12, 18);
      ctx.fillStyle = ACC; ctx.fillRect(tx, ty, 3, 18);
      ctx.fillStyle = INK; ctx.fillText(c, tx + 8, ty + 9.5);
      ctx.globalAlpha = 1;
    }
    pct.textContent = Math.round(cam.k * 100) + "%";

    placeTip();
    if (f && !still && !raf && !anim) anim = requestAnimationFrame(() => { anim = 0; if (hover || open) draw(); });
  }
  let anim = 0;


  function placeTip() {
    if (!hover || dragging?.moved) { tip.hidden = true; return; }
    tip.textContent = hover.label;
    tip.hidden = false;
    const [x, y] = toScreen(hover);
    tip.style.left = Math.max(6, Math.min(W - tip.offsetWidth - 6, x + 16)) + "px";
    tip.style.top = Math.max(6, Math.min(H - tip.offsetHeight - 6, y - tip.offsetHeight - 16)) + "px";
  }
  function brackets(x) {
    const s = x.r + 5, a = 4;
    ctx.strokeStyle = INK; ctx.lineWidth = 1.5 / cam.k; ctx.globalAlpha = 1; ctx.beginPath();
    for (const [dx, dy] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
      const cx = x.x + dx * s, cy = x.y + dy * s;
      ctx.moveTo(cx, cy - dy * a); ctx.lineTo(cx, cy); ctx.lineTo(cx - dx * a, cy);
    }
    ctx.stroke();
  }
  const weekMax = Math.max(1, ...weeks.map((w) => w.count));
  const barsEl = root.querySelector("#bars");
  barsEl.innerHTML = weeks.map((w) => `<button data-week="${esc(w.week)}" aria-label="${esc(w.week)}: ${w.count} memories"><i class="all"></i><span>${esc(w.week.slice(5))}</span></button>`).join("");
  function drawWhen() {
    for (const [i, bar] of [...barsEl.children].entries()) {
      bar.firstChild.style.height = (weeks[i].count / weekMax * 100) + "%";
      bar.classList.toggle("sel", match.week === weeks[i].week);
    }
  }
  function weekOf(value) {
    if (!value) return "";
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Amsterdam", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date(value));
    const p = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    const date = new Date(Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day)));
    date.setUTCDate(date.getUTCDate() + 4 - (date.getUTCDay() || 7));
    const year = date.getUTCFullYear();
    const week = Math.ceil((((date - Date.UTC(year, 0, 1)) / 86400000) + 1) / 7);
    return `${year}-W${String(week).padStart(2, "0")}`;
  }
  function filterWeek() {
    match.set = match.week ? new Set(nodes.filter((x) => weekOf(x.at) === match.week).map((x) => x.id)) : null;
    fit(); draw();
  }
  listen(barsEl, "click", (event) => {
    const b = event.target.closest("button"); if (!b) return;
    match.week = match.week === b.dataset.week ? null : b.dataset.week; filterWeek();
  });
  const legend = root.querySelector("#legend");
  legend.innerHTML = THEMES.map((t) => `<li><button data-theme="${esc(t.id)}" aria-pressed="true"><i class="theme-${t.index % 4}"></i>${esc(t.label || t.id)}<span class="c">${t.count ?? mems.filter((m) => m.theme === t.id).length}</span></button></li>`).join("");
  listen(legend, "click", (event) => {
    const b = event.target.closest("button"); if (!b) return;
    const t = b.dataset.theme; off.has(t) ? off.delete(t) : off.add(t);
    b.setAttribute("aria-pressed", String(!off.has(t))); fit(); draw();
  });
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
  const local = (event) => { const r = canvas.getBoundingClientRect(); return [event.clientX - r.left, event.clientY - r.top]; };
  listen(canvas, "pointerdown", (event) => {
    const [sx, sy] = local(event); canvas.setPointerCapture(event.pointerId);
    dragging = { node: hit(sx, sy), sx, sy, cx: cam.x, cy: cam.y, moved: false }; touched = true;
    canvas.classList.add("drag");
  });
  listen(canvas, "pointermove", (event) => {
    const [sx, sy] = local(event);
    if (dragging) {
      if (Math.abs(sx - dragging.sx) + Math.abs(sy - dragging.sy) > 3) dragging.moved = true;
      if (!dragging.moved) return;
      if (dragging.node) {
        dragging.node.fx = (sx - cam.x) / cam.k; dragging.node.fy = (sy - cam.y) / cam.k; alpha = Math.max(alpha, .25); run();
      } else { cam.x = dragging.cx + sx - dragging.sx; cam.y = dragging.cy + sy - dragging.sy; draw(); }
      return;
    }
    const x = hit(sx, sy);
    if (hover !== x) { hover = x; canvas.classList.toggle("over", !!x); draw(); }
  });
  const pointerEnd = () => {
    const d = dragging; dragging = null; canvas.classList.remove("drag");
    if (!d) return;
    if (!d.moved && d.node) { open = d.node; onOpen(d.node); }
    draw();
  };
  listen(canvas, "pointerup", pointerEnd);
  listen(canvas, "pointercancel", () => { dragging = null; canvas.classList.remove("drag"); });
  listen(canvas, "pointerleave", () => { if (!dragging) { hover = null; draw(); } });
  listen(canvas, "dblclick", (event) => {
    const x = hit(...local(event)); if (x && x.fx != null) { x.fx = x.fy = null; alpha = Math.max(alpha, .2); run(); }
  });
  listen(canvas, "wheel", (event) => { event.preventDefault(); zoomAt(Math.exp(-event.deltaY * .0015), ...local(event)); }, { passive: false });
  listen(root.querySelector(".zoom"), "click", (event) => {
    const b = event.target.closest("button"); if (!b) return;
    if (b.dataset.zoom === "fit") { fit(); draw(); }
    else zoomAt(b.dataset.zoom === "in" ? 1.25 : .8, W / 2, H / 2);
  });
  root.querySelectorAll("[data-view]").forEach((b) => listen(b, "click", () => {
    view = b.dataset.view;
    root.querySelectorAll("[data-view]").forEach((other) => other.setAttribute("aria-selected", String(other === b)));
    alpha = 1; refit = true; run();
  }));
  let raf = 0, refit = false;
  function run() {
    if (raf || disposed) return;
    const step = () => {
      if (disposed) return;
      tick(); tick(); if (refit) fit(); draw();
      raf = alpha > .02 || dragging ? requestAnimationFrame(step) : 0;
      if (!raf) refit = false;
    };
    raf = requestAnimationFrame(step);
  }
  // Keep the canvas equivalent available to keyboard and assistive-tech users.
  const accessible = document.createElement("ul"); accessible.className = "sr-only";
  accessible.innerHTML = nodes.map((x) => `<li><button data-node="${esc(x.id)}">${esc(x.kind)}: ${esc(x.label)}</button></li>`).join("");
  listen(accessible, "click", (event) => { const b = event.target.closest("button"); if (b) onOpen(byId[b.dataset.node]); });
  root.append(accessible);
  settle(140);
  const observer = new ResizeObserver(() => { resize(); if (!touched) fit(); draw(); }); observer.observe(stage);
  document.fonts?.ready.then(() => { if (!disposed) draw(); });
  const dispose = () => { disposed = true; controller.abort(); observer.disconnect(); cancelAnimationFrame(raf); cancelAnimationFrame(anim); accessible.remove(); };
  dispose.graph = graph;
  return dispose;
}
