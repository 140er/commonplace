// A synthetic topology in the shape of GET /api/topology/map, shared by the node checks
// (tests/topology_map_view.mjs) and the browser harness (tests/topology_harness.html).
// Every name is invented.
const node = (key, kind, name, role = "", extra = {}) => ({ subject: `node:${key}`, wing: "topology", key, values: { kind, name, ...(role ? { role } : {}) }, stale: false, conflicted: false, last: {}, ...extra });
const link = (from, to, method, values = {}, extra = {}) => ({ subject: `link:${from}>${to}:${method}`, wing: "topology", id: `${from}>${to}:${method}`, from, to, method, values: { method, ...values }, stale: false, conflicted: false, ...extra });
const hop = (id, status, known = true) => { const [from, rest] = id.split(">"); const [to, method] = rest.split(":"); return { link: id, from, to, method, known, stale: false, status }; };

export function fixture({ gate = "degraded" } = {}) {
  const ends = {
    "orchard.example>nimbus-dns:dns": ["up", "up"],
    "nimbus-dns>nimbus-cdn:proxy": ["up", "up"],
    "nimbus-cdn>gate:reverse_proxy": ["up", gate],
    "gate>maple:reverse_proxy": ["up", "up"],
    "maple>orchard-web:hosts": ["up", "up"],
    "maple>shop-api:hosts": ["up", "down"],
    "maple>birch:hosts": ["up", "up"],
    "shop.orchard.example>nimbus-dns:dns": ["unknown", "unknown"],
    "studio-mac>maple:tailscale": ["up", "up"],
    "studio-mac>maple:direct": ["down", "down"],
    "tower>birch:jump_host": ["degraded", "up"],
    "pocket>maple:relay": ["unknown", "up"],
  };
  const links = Object.keys(ends).map((id) => { const [from, rest] = id.split(">"); const [to, method] = rest.split(":"); return link(from, to, method, method === "jump_host" ? { via: "maple" } : {}); });
  const nodes = [
    node("orchard.example", "website", "orchard.example", "storefront"),
    node("shop.orchard.example", "website", "shop.orchard.example", "checkout"),
    node("nimbus-dns", "cloud", "Nimbus DNS", "DNS provider"),
    node("nimbus-cdn", "cloud", "Nimbus CDN", "CDN"),
    node("gate", "router", "Gate", "edge proxy", { conflicted: true }),
    node("maple", "server", "Maple", "app host"),
    node("birch", "vm", "Birch", "build runner"),
    node("orchard-web", "container", "orchard-web", "site app"),
    node("shop-api", "container", "shop-api", "checkout API"),
    node("studio-mac", "mac", "Studio Mac", "desk"),
    node("tower", "pc", "Tower", "workstation"),
    node("pocket", "phone", "Pocket", "on the go"),
    { ...node("old-nas", "server", "Old NAS"), values: {}, stale: true, last: { kind: "server", name: "Old NAS", role: "archive" } },
  ];
  return {
    graph: {
      wing: "topology",
      nodes,
      links,
      routes: [
        { subject: "route:orchard.example", wing: "topology", site: "orchard.example", values: {}, stale: false, conflicted: false, hops: [hop("orchard.example>nimbus-dns:dns", "up"), hop("nimbus-dns>nimbus-cdn:proxy", "up"), hop("nimbus-cdn>gate:reverse_proxy", gate), hop("gate>maple:reverse_proxy", "up"), hop("maple>orchard-web:hosts", "up")] },
        { subject: "route:shop.orchard.example", wing: "topology", site: "shop.orchard.example", values: {}, stale: false, conflicted: false, hops: [hop("shop.orchard.example>nimbus-dns:dns", "unknown"), hop("nimbus-dns>shop-edge:proxy", "unknown", false)] },
      ],
    },
    status: {
      nodes: Object.fromEntries(nodes.map((item) => [item.key, { maple: "up", birch: "degraded", "shop-api": "down", "studio-mac": "up", tower: "up" }[item.key] || "unknown"])),
      links: Object.fromEntries(Object.entries(ends).map(([id, [from, to]]) => [id, { from, to }])),
    },
  };
}

/** `count` machines in a chain with a few cross links: the "few hundred nodes" case. */
export function largeFixture(count = 300) {
  const kinds = ["server", "vm", "pc", "mac", "container", "router", "cloud", "website", "phone"];
  const nodes = Array.from({ length: count }, (_, i) => node(`n${String(i).padStart(3, "0")}`, kinds[i % kinds.length], `Node ${i}`, i % 4 ? "" : "worker"));
  const links = [];
  for (let i = 1; i < count; i++) links.push(link(nodes[i - 1].key, nodes[i].key, "direct"));
  for (let i = 0; i + 17 < count; i += 5) links.push(link(nodes[i].key, nodes[i + 17].key, "tailscale"));
  return { graph: { wing: "topology", nodes, links, routes: [] }, status: { nodes: {}, links: Object.fromEntries(links.map((item, i) => [item.id, { from: ["up", "degraded", "down", "unknown"][i % 4], to: "up" }])) } };
}
