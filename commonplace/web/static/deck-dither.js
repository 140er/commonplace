/* Commonplace × Capy — ordered Bayer dither engine.
 * Paints a procedural scene into a low-res buffer (one texel per dot),
 * then quantises every texel to a limited neon ramp with a 4x4 or 8x8
 * Bayer matrix and stamps it as a crisp LED dot grid. No blur, ever. */

const B4 = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5].map((v) => (v + 0.5) / 16);
const B8 = [
  0, 32, 8, 40, 2, 34, 10, 42, 48, 16, 56, 24, 50, 18, 58, 26,
  12, 44, 4, 36, 14, 46, 6, 38, 60, 28, 52, 20, 62, 30, 54, 22,
  3, 35, 11, 43, 1, 33, 9, 41, 51, 19, 59, 27, 49, 17, 57, 25,
  15, 47, 7, 39, 13, 45, 5, 37, 63, 31, 55, 23, 61, 29, 53, 21,
].map((v) => (v + 0.5) / 64);

const hex = (h) => {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};
const pack = ([r, g, b]) => (255 << 24) | (b << 16) | (g << 8) | r; // little-endian RGBA

const RAMPS = {
  purple: ["#121118", "#231d30", "#3b2257", "#5a2a8a", "#7a33c4", "#8d3be6", "#b44ef0", "#d946ef", "#ff47d1", "#ffb8f2"],
  cyan: ["#121118", "#14232c", "#0f3a4f", "#0f5a78", "#137e9e", "#1ab6d9", "#3ee6f7", "#b8f8ff"],
  grey: ["#121118", "#1d1c24", "#2a2833", "#3a3746", "#4d4960"],
  blue: ["#121118", "#191a3a", "#24267a", "#3236b8", "#4a4ff0", "#7c86ff", "#b9c0ff"],
};
const PACKED = {};
for (const k in RAMPS) PACKED[k] = RAMPS[k].map((h) => pack(hex(h)));
const GAP = pack(hex("#08080b"));
// Scene channels are bytes; this is exactly the reference power for every possible input.
const VALUES = Float64Array.from({ length: 256 }, (_, channel) => Math.pow(channel / 255, 1.15));

// deterministic RNG
function rng(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = s;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const hash2 = (x, y) => {
  let h = (x * 374761393 + y * 668265263) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
};

// smooth value noise + fbm, gives flat vector shapes a painted, mottled texture
function vnoise(x, y) {
  const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi;
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
  const a = hash2(xi, yi), b = hash2(xi + 1, yi), c = hash2(xi, yi + 1), d = hash2(xi + 1, yi + 1);
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}
const fbm = (x, y) => vnoise(x, y) * 0.55 + vnoise(x * 2.1, y * 2.1) * 0.3 + vnoise(x * 4.3, y * 4.3) * 0.15;

/* ---------- scenes: drawn with plain canvas vectors at dot resolution ---------- */
function poly(ctx, pts, fill, stroke, lw) {
  ctx.beginPath();
  pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.closePath();
  if (fill) { ctx.fillStyle = fill; ctx.fill(); }
  if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lw || 1; ctx.stroke(); }
}
function glowBlob(ctx, x, y, r, rgb, a) {
  const g = ctx.createRadialGradient(x, y, 0, x, y, r);
  g.addColorStop(0, `rgba(${rgb},${a})`);
  g.addColorStop(1, `rgba(${rgb},0)`);
  ctx.fillStyle = g;
  ctx.fillRect(x - r, y - r, r * 2, r * 2);
}

function lin(ctx, x0, y0, x1, y1, stops) {
  const g = ctx.createLinearGradient(x0, y0, x1, y1);
  stops.forEach(([o, c]) => g.addColorStop(o, c));
  return g;
}
function edges(ctx, pts, idx, color, lw) {
  ctx.strokeStyle = color;
  ctx.lineWidth = lw || 1;
  ctx.lineCap = "round";
  idx.forEach((i) => {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
  });
}

/* Capy Cyber: a shaded mecha bust over a dark city, lit from the upper left,
   with one magenta eye and a cyan rim from the nebula on the right. Most of the
   frame stays in the two darkest ramp steps; colour lives on edges only. */
function sceneCapy(ctx, W, H) {
  const R = rng(1311);
  const wide = W > H;
  const S = wide ? Math.min(H * 0.8, W * 0.75) : W * 1.05;
  const hx = wide ? Math.min(W * 0.76, W - S * 0.5) : W * 0.56;
  const hy = wide ? H * 0.4 : H * 0.27;
  const P = (x, y) => [hx + x * S, hy + y * S];
  const PP = (arr) => arr.map(([x, y]) => P(x, y));

  // atmosphere: a cool nebula on the far right, a faint violet haze top left
  for (let i = 0; i < 18; i++) glowBlob(ctx, W * (0.9 + R() * 0.2), H * R(), S * (0.12 + R() * 0.2), "34,150,190", 0.1 + R() * 0.1);
  glowBlob(ctx, W * 0.18, H * 0.08, S * 0.55, "80,36,140", 0.12);
  glowBlob(ctx, hx, hy, S * 0.7, "70,30,120", 0.16);

  // distant city: towers with dim tops and sparse window lights
  for (let i = 0; i < 46; i++) {
    const w = S * (0.025 + R() * 0.06), h = S * (0.12 + R() * 0.42) * (wide ? 1 : 0.7);
    const x = R() * W, y = H - h;
    ctx.fillStyle = lin(ctx, 0, y, 0, H, [[0, "rgb(46,32,72)"], [1, "rgb(12,9,18)"]]);
    ctx.fillRect(x, y, w, h);
    ctx.fillStyle = "rgb(120,70,180)";
    ctx.fillRect(x, y, w, 1);
    for (let k = 0; k < 5; k++) if (R() < 0.5) {
      ctx.fillStyle = R() < 0.6 ? "rgb(60,190,225)" : "rgb(210,80,230)";
      ctx.fillRect(x + R() * w, y + R() * h * 0.6, 1, 1);
    }
  }

  // a second, distant frame on the left: one dark blade with a single rim
  const blade = [[0, H * 0.7], [W * 0.15, H * 0.56], [W * 0.17, H * 0.58], [W * 0.03, H * 0.8], [0, H * 0.84]];
  poly(ctx, blade, lin(ctx, 0, H * 0.56, W * 0.12, H * 0.84, [[0, "rgb(34,20,54)"], [1, "rgb(10,8,15)"]]));

  // shoulders and chest
  const body = PP([[-0.62, 0.36], [-0.2, 0.2], [0.36, 0.24], [0.9, 0.44], [1.1, 1.3], [-0.7, 1.3]]);
  poly(ctx, body, lin(ctx, ...P(-0.5, 0.2), ...P(0.4, 1.0), [[0, "rgb(72,44,116)"], [0.32, "rgb(28,17,44)"], [1, "rgb(8,6,12)"]]));
  edges(ctx, body, [1], "rgb(190,90,236)", 1.4);
  edges(ctx, body, [2], "rgb(60,190,225)", 1.2);
  ctx.strokeStyle = "rgb(8,6,12)"; ctx.lineWidth = 1.4;
  [[[-0.5, 0.45], [0.1, 0.4]], [[0.1, 0.4], [0.6, 0.62]], [[-0.1, 0.42], [-0.2, 1.2]], [[0.42, 0.55], [0.5, 1.2]]].forEach(([a, b]) => {
    ctx.beginPath(); ctx.moveTo(...P(...a)); ctx.lineTo(...P(...b)); ctx.stroke();
  });
  const paul = PP([[-0.86, 0.32], [-0.56, 0.16], [-0.32, 0.32], [-0.42, 0.7], [-0.88, 0.66]]);
  poly(ctx, paul, lin(ctx, ...P(-0.9, 0.12), ...P(-0.4, 0.8), [[0, "rgb(92,56,144)"], [0.35, "rgb(36,22,58)"], [1, "rgb(10,8,15)"]]));
  ctx.fillStyle = "rgb(70,210,240)";
  ctx.fillRect(...P(-0.55, 0.3), 2, 2);

  // neck and cables
  poly(ctx, PP([[-0.13, 0.12], [0.15, 0.12], [0.18, 0.3], [-0.16, 0.3]]), "rgb(16,11,26)");
  ctx.strokeStyle = "rgb(44,28,70)"; ctx.lineWidth = 1.2;
  for (let i = 0; i < 4; i++) {
    ctx.beginPath(); ctx.moveTo(...P(-0.1 + i * 0.07, 0.14));
    ctx.quadraticCurveTo(...P(-0.08 + i * 0.07, 0.24), ...P(-0.12 + i * 0.08, 0.32)); ctx.stroke();
  }

  // crest fins (behind the shell)
  const fin1 = PP([[-0.03, -0.38], [0.05, -0.8], [0.1, -0.37]]);
  const fin2 = PP([[0.2, -0.33], [0.58, -0.68], [0.3, -0.27]]);
  poly(ctx, fin1, lin(ctx, ...P(0, -0.8), ...P(0.05, -0.38), [[0, "rgb(120,60,186)"], [1, "rgb(34,20,56)"]]));
  poly(ctx, fin2, lin(ctx, ...P(0.55, -0.68), ...P(0.25, -0.3), [[0, "rgb(96,48,156)"], [1, "rgb(28,18,46)"]]));
  edges(ctx, fin1, [0], "rgb(230,100,245)", 1);
  edges(ctx, fin2, [0], "rgb(200,90,240)", 1);

  // helmet shell, lit from the upper left
  const shell = PP([[-0.33, -0.27], [-0.06, -0.42], [0.27, -0.36], [0.39, -0.12], [0.31, 0.1], [0.1, 0.21], [-0.18, 0.19], [-0.37, 0.0]]);
  poly(ctx, shell, lin(ctx, ...P(-0.3, -0.42), ...P(0.36, 0.22), [[0, "rgb(150,92,222)"], [0.4, "rgb(66,40,104)"], [1, "rgb(12,9,20)"]]));
  edges(ctx, shell, [0, 1], "rgb(226,110,250)", 1.6);
  edges(ctx, shell, [2, 3], "rgb(70,210,240)", 1.4);
  const brow = PP([[-0.3, -0.24], [-0.05, -0.38], [0.24, -0.32], [0.3, -0.17], [-0.02, -0.13], [-0.28, -0.07]]);
  poly(ctx, brow, lin(ctx, ...P(-0.3, -0.38), ...P(0.3, -0.1), [[0, "rgb(178,112,246)"], [1, "rgb(50,30,80)"]]), "rgb(10,7,16)", 1.4);
  edges(ctx, brow, [0], "rgb(240,120,250)", 1);
  poly(ctx, PP([[0.12, -0.02], [0.35, -0.08], [0.3, 0.1], [0.12, 0.18]]), lin(ctx, ...P(0.12, -0.05), ...P(0.3, 0.18), [[0, "rgb(52,32,84)"], [1, "rgb(16,11,26)"]]), "rgb(10,7,16)", 1.2);
  poly(ctx, PP([[-0.33, 0.04], [-0.12, 0.06], [-0.1, 0.17], [-0.3, 0.12]]), "rgb(30,19,48)", "rgb(10,7,16)", 1.2);
  // panel seams and cheek vents give the shell its machined detail
  ctx.strokeStyle = "rgb(10,7,16)"; ctx.lineWidth = 1.3;
  [[[-0.2, -0.33], [-0.12, -0.1]], [[0.08, -0.36], [0.1, -0.14]], [[-0.36, -0.12], [-0.22, -0.1]], [[0.2, 0.12], [0.02, 0.2]]].forEach(([a, b]) => {
    ctx.beginPath(); ctx.moveTo(...P(...a)); ctx.lineTo(...P(...b)); ctx.stroke();
  });
  for (let i = 0; i < 4; i++) poly(ctx, PP([[0.17, 0.02 + i * 0.03], [0.28, -0.01 + i * 0.03], [0.28, 0.0 + i * 0.03], [0.17, 0.03 + i * 0.03]]), "rgb(8,6,12)");
  poly(ctx, PP([[-0.26, -0.22], [-0.1, -0.31], [-0.14, -0.24]]), "rgb(205,140,252)");
  // visor and eye
  poly(ctx, PP([[-0.34, -0.05], [0.34, -0.12], [0.32, -0.03], [-0.31, 0.04]]), "rgb(5,4,9)");
  glowBlob(ctx, ...P(0.16, -0.075), S * 0.13, "240,60,200", 0.3);
  ctx.fillStyle = "rgb(240,70,205)";
  ctx.beginPath(); ctx.ellipse(...P(0.16, -0.075), S * 0.062, S * 0.02, -0.1, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "rgb(255,200,245)";
  ctx.beginPath(); ctx.ellipse(...P(0.17, -0.078), S * 0.022, S * 0.008, -0.1, 0, Math.PI * 2); ctx.fill();
  // jaw
  poly(ctx, PP([[-0.15, 0.19], [0.11, 0.21], [0.06, 0.33], [-0.1, 0.34]]), lin(ctx, ...P(-0.1, 0.19), ...P(0.05, 0.34), [[0, "rgb(42,26,66)"], [1, "rgb(12,9,18)"]]));

  // dust
  for (let i = 0; i < 160; i++) {
    ctx.fillStyle = R() < 0.85 ? "rgb(70,60,100)" : "rgb(200,90,235)";
    ctx.fillRect(R() * W, R() * H, 1, 1);
  }

  // keep the composer's ground quiet: a soft dark field in the centre of the stage
  ctx.save();
  ctx.translate(W / 2, wide ? H * 0.52 : H * 0.72);
  ctx.scale(1, wide ? 0.42 : 0.6);
  const q = ctx.createRadialGradient(0, 0, 0, 0, 0, W * (wide ? 0.34 : 0.7));
  q.addColorStop(0, "rgba(0,0,0,0.62)");
  q.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = q;
  ctx.fillRect(-W, -W, W * 2, W * 2);
  ctx.restore();
}

function scenePalace(ctx, W, H) {
  const R = rng(77);
  const vx = W * 0.6, vy = H * 0.46;
  glowBlob(ctx, vx, vy, H * 0.9, "70,30,130", 0.5);
  glowBlob(ctx, vx, vy, H * 0.28, "60,220,245", 0.85);
  // floor grid receding to the vanishing point
  ctx.strokeStyle = "rgb(150,60,220)";
  ctx.lineWidth = 1;
  for (let i = -14; i <= 14; i++) {
    ctx.beginPath();
    ctx.moveTo(vx, vy + H * 0.06);
    ctx.lineTo(vx + i * W * 0.12, H * 1.05);
    ctx.stroke();
  }
  for (let k = 1; k < 12; k++) {
    const y = vy + H * 0.06 + Math.pow(k / 11, 2.2) * H * 0.6;
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
  }
  // nested gates (arches) — the palace
  for (let k = 7; k >= 0; k--) {
    const s = Math.pow(0.72, k);
    const w = H * 0.9 * s, h = H * 1.1 * s;
    const x0 = vx - w / 2, y0 = vy + H * 0.06 - h;
    const lum = 60 + (7 - k) * 22;
    ctx.fillStyle = `rgb(${lum * 0.45 | 0},${lum * 0.25 | 0},${lum * 0.8 | 0})`;
    ctx.strokeStyle = k % 2 ? "rgb(220,80,240)" : "rgb(70,210,240)";
    ctx.lineWidth = Math.max(1, 2.4 * s);
    // two pillars + arch
    ctx.fillRect(x0 - w * 0.12, y0 + h * 0.3, w * 0.12, h * 0.7);
    ctx.fillRect(x0 + w, y0 + h * 0.3, w * 0.12, h * 0.7);
    ctx.beginPath();
    ctx.moveTo(x0, y0 + h); ctx.lineTo(x0, y0 + h * 0.35);
    ctx.quadraticCurveTo(x0 + w / 2, y0 - h * 0.12, x0 + w, y0 + h * 0.35);
    ctx.lineTo(x0 + w, y0 + h);
    ctx.stroke();
  }
  // stars
  for (let i = 0; i < 260; i++) {
    ctx.fillStyle = R() < 0.3 ? "rgb(90,220,245)" : "rgb(210,120,250)";
    ctx.fillRect(R() * W, R() * H * 0.55, 1, 1);
  }
  glowBlob(ctx, W * 0.08, H * 0.15, H * 0.4, "200,60,230", 0.3);
}

function sceneNebula(ctx, W, H) {
  const R = rng(4242);
  for (let i = 0; i < 40; i++) {
    const t = i / 40;
    const x = W * (0.15 + t * 0.85 + (R() - 0.5) * 0.15);
    const y = H * (0.75 - t * 0.5 + (R() - 0.5) * 0.35);
    glowBlob(ctx, x, y, H * (0.1 + R() * 0.22), t > 0.55 ? "40,190,230" : "150,50,220", 0.12 + R() * 0.18);
  }
  // planet with rim light
  const px = W * 0.74, py = H * 0.62, pr = H * 0.32;
  ctx.fillStyle = "rgb(24,16,40)";
  ctx.beginPath(); ctx.arc(px, py, pr, 0, Math.PI * 2); ctx.fill();
  const g = ctx.createRadialGradient(px - pr * 0.5, py - pr * 0.6, pr * 0.2, px, py, pr);
  g.addColorStop(0, "rgba(120,60,200,0.7)");
  g.addColorStop(1, "rgba(30,20,50,0)");
  ctx.fillStyle = g; ctx.beginPath(); ctx.arc(px, py, pr, 0, Math.PI * 2); ctx.fill();
  ctx.strokeStyle = "rgb(70,220,245)"; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(px, py, pr, Math.PI * 1.05, Math.PI * 1.75); ctx.stroke();
  ctx.strokeStyle = "rgb(220,80,240)"; ctx.lineWidth = 1.2;
  ctx.beginPath(); ctx.ellipse(px, py, pr * 1.7, pr * 0.28, -0.25, 0, Math.PI * 2); ctx.stroke();
  for (let i = 0; i < 420; i++) {
    const b = R();
    ctx.fillStyle = b < 0.2 ? "rgb(150,240,255)" : b < 0.5 ? "rgb(200,110,250)" : "rgb(90,80,120)";
    ctx.fillRect(R() * W, R() * H, 1, 1);
  }
}

const SCENES = { "capy-cyber": sceneCapy, "palace-gate": scenePalace, "nebula-quiet": sceneNebula };

/* ---------- renderer ---------- */
function StageDither(canvas, opts) {
  this.canvas = canvas;
  this.ctx = canvas.getContext("2d");
  this.doc = canvas.ownerDocument;
  this.win = this.doc.defaultView;
  this.opts = Object.assign({ scene: "capy-cyber", bayer: 4, vignette: 0.6, dim: 0, dot: 4 }, opts || {});
  this.levels = null;
  this._tw = [];
  this.motion = this.win.matchMedia("(prefers-reduced-motion: reduce)");
  this.motionChanged = () => {
    if (this._twinkle) this.win.clearInterval(this._twinkle);
    this._twinkle = null;
    this._tw.forEach(([i, j, col]) => {
      this.ctx.fillStyle = col;
      this.ctx.fillRect(i * this.cs, j * this.cs, this.cs - this.gap, this.cs - this.gap);
    });
    this._tw = [];
    if (!this.motion.matches && this.opts.twinkle !== false) this._twinkle = this.win.setInterval(() => this.twinkle(), 140);
  };
  this.motion.addEventListener("change", this.motionChanged);
  this.observer = new this.win.ResizeObserver(() => this.render());
  this.observer.observe(canvas.parentElement || canvas);
  this.motionChanged();
  this.render();
}

StageDither.prototype.destroy = function () {
  this.observer.disconnect();
  this.motion.removeEventListener("change", this.motionChanged);
  if (this._twinkle) this.win.clearInterval(this._twinkle);
  this._twinkle = null;
};

// Reference value, hue and quantisation passes, exposed for fixed-input checks.
function valuePass(mx, i, j, cols, rows, vignette, dim, field = null) {
  const k = j * cols + i;
  const tex = field ? field.tex[k] : fbm(i / 16, j / 16);
  let v = (VALUES[mx] ?? Math.pow(mx / 255, 1.15)) * (0.45 + tex * 0.85) + (field ? field.hash[k] : hash2(i, j)) * 0.08 + tex * 0.05;
  const edge = field ? field.edge[k] : 1 - vignette * Math.min(1, Math.max(0, (Math.hypot(i - cols / 2, j - rows / 2) / Math.hypot(cols / 2, rows / 2) - 0.35) / 0.65)) ** 1.4;
  v *= edge;
  return v * (1 - dim);
}
function rampFor(r, g, b, threshold) {
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
  if ((mx ? (mx - mn) / mx : 0) < 0.22 || mx < 24) return "grey";
  return (g - r) / 110 + 0.5 > threshold ? "cyan" : b > r * 1.6 && g < 60 ? "blue" : "purple";
}
function quantize(v, ramp, threshold) {
  const n = RAMPS[ramp].length - 1;
  return Math.max(0, Math.min(n, Math.floor(v * n + threshold - 0.5)));
}

StageDither.prototype.set = function (o) {
  Object.assign(this.opts, o);
  this.render();
};

StageDither.prototype.render = function () {
  const c = this.canvas, o = this.opts;
  const rect = c.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const started = this.win.performance.now();
  const dpr = Math.min(this.win.devicePixelRatio || 1, 2);
  // Never larger than the window: a canvas without CSS size grows with its own bitmap, and each render would double it.
  const fit = (size, limit) => limit > 0 ? Math.min(size, Math.ceil(limit * dpr)) : size;
  const W = fit(Math.round(rect.width * dpr), this.win.innerWidth), H = fit(Math.round(rect.height * dpr), this.win.innerHeight);
  if (c.width !== W) c.width = W;
  if (c.height !== H) c.height = H;
  const cs = Math.max(2, Math.round(o.dot * dpr));
  const gap = Math.max(1, Math.round(dpr));
  const cols = Math.ceil(W / cs), rows = Math.ceil(H / cs);
  this.cs = cs; this.gap = gap; this.cols = cols; this.rows = rows;

  // 1. paint the scene at one texel per dot
  // Scenes are seeded and static. Only scene/geometry changes need a new vector buffer.
  if (!this.source || this.source.cols !== cols || this.source.rows !== rows || this.source.scene !== o.scene) {
    const off = this.doc.createElement("canvas");
    off.width = cols; off.height = rows;
    const octx = off.getContext("2d");
    octx.fillStyle = "#000"; octx.fillRect(0, 0, cols, rows);
    (SCENES[o.scene] || sceneCapy)(octx, cols, rows);
    this.source = { cols, rows, scene: o.scene, pixels: octx.getImageData(0, 0, cols, rows).data };
  }
  const src = this.source.pixels;

  // 2. quantise with Bayer and stamp dots
  // At 2x, each reference pixel is an exact 2×2 square (including gaps).
  // Stamp that smaller raster, then scale it with nearest-neighbour canvas drawing.
  // This preserves every output byte while avoiding four times the JS pixel writes.
  const scale = cs % 2 === 0 && gap % 2 === 0 && W % 2 === 0 && H % 2 === 0 ? 2 : 1;
  const paintW = W / scale, paintH = H / scale, paintCs = cs / scale, paintGap = gap / scale;
  const img = this.ctx.createImageData(paintW, paintH);
  const buf = new Uint32Array(img.data.buffer);
  buf.fill(GAP);
  const M = o.bayer === 8 ? B8 : B4, N = o.bayer === 8 ? 8 : 4;
  const levels = new Uint8Array(cols * rows), ramps = new Uint8Array(cols * rows);
  const names = Object.keys(RAMPS), codes = Object.fromEntries(names.map((name, index) => [name, index]));
  // Cache deterministic noise and the vignette in the same texel pass, without changing the math.
  // Scene, dim and Bayer changes reuse it; geometry/vignette changes invalidate it.
  const fresh = !this.field || this.field.cols !== cols || this.field.rows !== rows || this.field.vignette !== o.vignette;
  if (fresh) this.field = { cols, rows, vignette: o.vignette, tex: new Float64Array(cols * rows), hash: new Float64Array(cols * rows), edge: new Float64Array(cols * rows) };
  const field = this.field, ccx = cols / 2, ccy = rows / 2, maxd = Math.hypot(ccx, ccy);
  const counts = Object.fromEntries(Object.entries(RAMPS).map(([name, ramp]) => [name, new Array(ramp.length).fill(0)]));
  for (let j = 0; j < rows; j++) {
    for (let i = 0; i < cols; i++) {
      const k = (j * cols + i) * 4;
      const r = src[k], g = src[k + 1], b = src[k + 2];
      const index = j * cols + i;
      if (fresh) {
        field.tex[index] = fbm(i / 16, j / 16);
        field.hash[index] = hash2(i, j);
        const d = Math.hypot(i - ccx, j - ccy) / maxd;
        field.edge[index] = 1 - o.vignette * Math.min(1, Math.max(0, (d - 0.35) / 0.65)) ** 1.4;
      }
      const v = valuePass(Math.max(r, g, b), i, j, cols, rows, o.vignette, o.dim, field);
      const th = M[(j % N) * N + (i % N)];
      const ramp = rampFor(r, g, b, th);
      const R = PACKED[ramp];
      const lvl = quantize(v, ramp, th);
      counts[ramp][lvl]++;
      levels[index] = lvl; ramps[index] = codes[ramp];
      const col = R[lvl];
      const x0 = i * paintCs, y0 = j * paintCs, s = paintCs - paintGap;
      for (let y = y0; y < y0 + s && y < paintH; y++) {
        const row = y * paintW;
        for (let x = x0; x < x0 + s && x < paintW; x++) buf[row + x] = col;
      }
    }
  }
  if (scale === 1) this.ctx.putImageData(img, 0, 0);
  else {
    this.paint ||= this.doc.createElement('canvas');
    this.paint.width = paintW; this.paint.height = paintH;
    this.paint.getContext('2d').putImageData(img, 0, 0);
    this.ctx.imageSmoothingEnabled = false;
    this.ctx.drawImage(this.paint, 0, 0, W, H);
  }
  this.levels = levels;
  this.ramps = ramps;
  this._tw = [];
  this.measurement = { cols, rows, dots: cols * rows, counts,
    lowFraction: Object.values(counts).reduce((n, steps) => n + steps[0] + steps[1] + steps[2], 0) / (cols * rows),
    ms: this.win.performance.now() - started, dpr, dot: o.dot, dim: o.dim, scene: o.scene };
  c.dataset.dim = String(o.dim);
  c.dataset.dot = String(o.dot);

};

// a handful of dots shimmer one level up, like a live display
StageDither.prototype.twinkle = function () {
  if (!this.levels || this.doc.hidden) return;
  const ctx = this.ctx, cs = this.cs, s = cs - this.gap;
  this._tw.forEach(([i, j, col]) => { ctx.fillStyle = col; ctx.fillRect(i * cs, j * cs, s, s); });
  this._tw = [];
  for (let t = 0; t < 26; t++) {
    const i = (Math.random() * this.cols) | 0, j = (Math.random() * this.rows) | 0;
    const index = j * this.cols + i;
    const ramp = Object.keys(RAMPS)[this.ramps[index]], lvl = this.levels[index];
    if (lvl < 2) continue;
    const R = RAMPS[ramp];
    this._tw.push([i, j, R[lvl]]);
    ctx.fillStyle = R[Math.min(R.length - 1, lvl + 2)];
    ctx.fillRect(i * cs, j * cs, s, s);
  }
};

export { StageDither, RAMPS, B4, B8, SCENES, valuePass, rampFor, quantize };
