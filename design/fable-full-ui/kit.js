// Shared drawing helpers for the three directions: escaping, outline icons, device icons and the
// topology field. Looks come from each direction's stylesheet; nothing here sets a colour.

export const e = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

const I = {
  chat: '<path d="M3 4.5h10v6.5H7.5L5 13v-2H3z"/>',
  bots: '<rect x="3" y="5.5" width="10" height="7" rx="2"/><path d="M8 5.5V3M6 9h.01M10 9h.01"/>',
  compute: '<circle cx="3.5" cy="8" r="1.5"/><circle cx="12.5" cy="4" r="1.5"/><circle cx="12.5" cy="12" r="1.5"/><path d="M5 7.4l6-2.8M5 8.6l6 2.8"/>',
  settings: '<path d="M2.5 5h7M12.5 5h1M2.5 11h1M6.5 11h7"/><circle cx="11" cy="5" r="1.5"/><circle cx="5" cy="11" r="1.5"/>',
  today: '<path d="M3 8.5l3 3 7-7"/>',
  project: '<path d="M2.5 4.5h4l1.5 1.5h5.5v6.5h-11z"/>',
  search: '<circle cx="7" cy="7" r="4"/><path d="M10 10l3.5 3.5"/>',
  plus: '<path d="M8 3v10M3 8h10"/>',
  send: '<path d="M8 13V3M4 7l4-4 4 4"/>',
  back: '<path d="M10 3L5 8l5 5"/>',
  chev: '<path d="M6 3l5 5-5 5"/>',
  down: '<path d="M3 6l5 5 5-5"/>',
  menu: '<path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11"/>',
  dots: '<path d="M3.5 8h.01M8 8h.01M12.5 8h.01"/>',
  check: '<path d="M3.5 8.5l3 3 6-7"/>',
  mic: '<rect x="6" y="2.5" width="4" height="7" rx="2"/><path d="M3.5 8a4.5 4.5 0 009 0M8 12.5v1.5"/>',
  clip: '<path d="M11.5 7.5L7 12a2.5 2.5 0 01-3.5-3.5l5-5a1.7 1.7 0 012.4 2.4L6 10.8"/>',
  memory: '<path d="M4 2.5h8v11H4zM6.5 5.5h3M6.5 8h3"/>',
  x: '<path d="M4 4l8 8M12 4l-8 8"/>',
};
export const icon = (name, size = 16) => `<svg class="ic" width="${size}" height="${size}" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${I[name] || ""}</svg>`;

// Device drawings on a 40 x 40 field, outline only.
const DEV = {
  mac: '<rect x="6" y="7" width="28" height="19" rx="2"/><path d="M20 26v5M13 32h14"/>',
  pc: '<rect x="9" y="9" width="22" height="15" rx="1.5"/><path d="M5 29h30l-2 3H7z"/>',
  tower: '<rect x="13" y="5" width="14" height="30" rx="2"/><path d="M16.5 11h7M16.5 15h7"/><circle cx="20" cy="28" r="1.6"/>',
  server: '<rect x="7" y="8" width="26" height="10" rx="1.5"/><rect x="7" y="22" width="26" height="10" rx="1.5"/><path d="M11 13h.01M11 27h.01M16 13h6M16 27h6"/>',
  vm: '<rect x="6" y="6" width="28" height="28" rx="2" stroke-dasharray="3 3"/><rect x="13" y="13" width="14" height="14" rx="1.5"/>',
  phone: '<rect x="13" y="5" width="14" height="30" rx="3"/><path d="M18 30.5h4"/>',
  router: '<rect x="6" y="21" width="28" height="10" rx="2"/><path d="M12 21L9 9M28 21l3-12M11 26h.01M15 26h.01M21 26h8"/>',
  cloud: '<path d="M12 29a7 7 0 01-.8-13.9A9 9 0 0128.6 13 7.5 7.5 0 0129 29z"/>',
  site: '<rect x="5" y="8" width="30" height="24" rx="2"/><path d="M5 14.5h30M9 11.3h.01M12.5 11.3h.01M10 20h12M10 25h8"/>',
  hub: '<circle cx="20" cy="20" r="13"/><circle cx="20" cy="20" r="4"/><path d="M20 7v9M20 24v9M7 20h9M24 20h9"/>',
};
export const device = (type, size = 40) => `<svg class="dev" width="${size}" height="${size}" viewBox="0 0 40 40" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${DEV[type] || DEV.server}</svg>`;

/** The topology as one SVG, laid out for a field of W x H pixels so type is never scaled.
 *  Links are lines with a status dot near each end; nodes are a device drawing over a name and a
 *  role. `phone` picks the portrait arrangement and drops the role line. */
export function topology({ nodes, links, selected, phone = false, W = 1100, H = 660 }) {
  const R = phone ? 23 : 28;
  const key = phone ? ["px", "py"] : ["x", "y"];
  const span = (k) => { const v = nodes.map((n) => n[k]); return [Math.min(...v), Math.max(...v) - Math.min(...v)]; };
  const [x0, xs] = span(key[0]);
  const [y0, ys] = span(key[1]);
  const padX = phone ? 44 : 84;
  const padT = R + 14;
  const padB = R + (phone ? 26 : 46);
  const at = (n) => [Math.round(padX + ((n[key[0]] - x0) / xs) * (W - 2 * padX)), Math.round(padT + ((n[key[1]] - y0) / ys) * (H - padT - padB))];
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  const line = ([a, b, sa, sb]) => {
    const [x1, y1] = at(byId[a]);
    const [x2, y2] = at(byId[b]);
    const off = (R + 9) / Math.hypot(x2 - x1, y2 - y1);
    const p = (t) => [x1 + (x2 - x1) * t, y1 + (y2 - y1) * t];
    const [ax, ay] = p(off);
    const [bx, by] = p(1 - off);
    const worst = [sa, sb].includes("down") ? "down" : [sa, sb].includes("warn") ? "warn" : "up";
    return `<line class="lk ${worst}" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/><circle class="dot ${sa}" cx="${ax}" cy="${ay}" r="4"/><circle class="dot ${sb}" cx="${bx}" cy="${by}" r="4"/>`;
  };
  const node = (n) => {
    const [x, y] = at(n);
    const s = phone ? 28 : 34;
    const name = phone ? n.name.replace("docs.northwind.example", "docs site").replace("northwind.example", "website").replace("Maya's ", "") : n.name;
    return `<g class="n ${n.state}${n.id === selected ? " sel" : ""}" data-node="${e(n.id)}" tabindex="0" role="button" aria-label="${e(n.name)}, ${e(n.role)}">
<circle class="pad" cx="${x}" cy="${y}" r="${R}"/>
<g transform="translate(${x - s / 2} ${y - s / 2})">${device(n.type, s)}</g>
<text class="nm" x="${x}" y="${y + R + 17}" text-anchor="middle">${e(name)}</text>${phone ? "" : `<text class="rl" x="${x}" y="${y + R + 34}" text-anchor="middle">${e(n.role)}</text>`}
</g>`;
  };
  return `<svg class="topo" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="group" aria-label="Map of machines, routes and sites">${links.map(line).join("")}${nodes.map(node).join("")}</svg>`;
}

export const initial = (name) => e(name.slice(0, 1).toUpperCase());
