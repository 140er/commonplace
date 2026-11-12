// Scripted reads of the node detail panel for the invented topology of tests/topology_fixture.mjs:
// GET /api/topology/node?key=<key> (`nodeRead`) and GET /api/topology/route?target=<key> (`routeRead`).
// One node of each kind has facts that fit it. Every name is invented.
import { fixture } from "./topology_fixture.mjs";

export const NOW = Date.now();
export const ago = (minutes) => new Date(NOW - minutes * 60000).toISOString();
const source = (id, minutes, evidence, kind = "agent") => ({ kind, id, observed_at: ago(minutes), seen_count: 1, evidence });
const field = (value, minutes, evidence, { stale = false, id = "conn_scout", kind = "agent" } = {}) => ({ value, stale, observed_at: ago(minutes), expires_at: ago(minutes - 60 * 24 * 30), sources: [source(id, minutes, evidence, kind)], contenders: 0 });

// Facts per node, as the read's `fields`.
const FIELDS = {
  maple: {
    kind: field("server", 20, "uname -sr"), name: field("Maple", 20, "hostnamectl"), role: field("app host", 20, "hostnamectl"),
    aliases: field(["maple-ssh"], 90, "Host maple-ssh"), tailscale_name: field("maple.example.ts.net", 90, "tailscale status"),
    runner_id: field("mach_0123456789abcdef", 3, "heartbeat", { id: "mach_0123456789abcdef", kind: "runner" }), wings: field(["atlas", "shared"], 3, "runner grant", { id: "mach_0123456789abcdef", kind: "runner" }),
    hardware: field("8-core workstation board", 20, "lscpu"), os: field("Linux 6.8", 20, "uname -sr"),
    resources: field({ cpu: "8-core x86", cores: 8, memory_gb: 32, storage_gb: 960 }, 20, "free -g; df -h"),
  },
  birch: { kind: field("vm", 40, "virsh list"), name: field("Birch", 40, "virsh list"), runner_id: field("mach_00000000000000ff", 3, "heartbeat", { id: "mach_00000000000000ff", kind: "runner" }), os: field("Linux 6.1", 40, "uname -sr") },
  "studio-mac": { kind: field("mac", 10, "system_profiler"), name: field("Studio Mac", 10, "system_profiler"), role: field("desk", 10, "owner note", { id: "user_maya", kind: "owner" }), hostname: field("studio.example.test", 10, "scutil --get HostName"), aliases: field(["studio"], 10, "Host studio"), hardware: field("Laptop, 16 GB", 10, "system_profiler"), os: field("macOS 15", 10, "sw_vers") },
  tower: { kind: field("pc", 600, "wmic"), name: field("Tower", 600, "wmic"), os: field("Windows 11", 600, "ver"), resources: field({ cores: 12, memory_gb: 64 }, 600, "wmic") },
  pocket: { kind: field("phone", 60, "owner note", { id: "user_maya", kind: "owner" }), name: field("Pocket", 60, "owner note", { id: "user_maya", kind: "owner" }) },
  gate: { kind: field("router", 50, "nginx -v"), name: field("Gate", 50, "nginx -v"), role: field("edge proxy", 50, "nginx -v"), hostname: field("gate.example.test", 50, "dig +short") },
  "nimbus-cdn": { kind: field("cloud", 300, "dns zone"), name: field("Nimbus CDN", 300, "dns zone"), role: field("CDN", 300, "dns zone") },
  "orchard-web": { kind: field("container", 15, "docker ps"), name: field("orchard-web", 15, "docker ps"), role: field("site app", 15, "docker ps") },
  "orchard.example": { kind: field("website", 15, "dns zone"), name: field("orchard.example", 15, "dns zone"), role: field("storefront", 15, "dns zone") },
  // Everything of this node has expired: the panel still says what it was and when it was seen.
  "old-nas": { kind: field("server", 60 * 24 * 40, "uname -sr", { stale: true }), name: field("Old NAS", 60 * 24 * 40, "uname -sr", { stale: true }), os: field("Linux 5.10", 60 * 24 * 40, "uname -sr", { stale: true }), hardware: field("4-bay NAS", 60 * 24 * 40, "lshw", { stale: true }) },
};

const AGENTS = {
  maple: {
    applicable: true,
    machine: { id: "mach_0123456789abcdef", label: "maple-runner", enabled: true, revoked: false },
    connections: [{ kind: "machine", name: "maple-runner (machine)", last_used_at: ago(1), revoked: false }, { kind: "worker", name: "maple-runner (worker)", last_used_at: ago(7), revoked: false }],
    runs: [{ job: "job_a1", state: "started", board: "atlas", card: 442, project: "demo", topic: "worker", thread_id: "thr_orchard_1", updated_at: ago(2), started_at: ago(30), log_ref: "https://logs.example.test/run/a1" }, { job: "job_a2", state: "queued", board: "atlas", card: 443, project: "demo", topic: "worker", thread_id: null, updated_at: ago(1), started_at: null, log_ref: null }],
    coordinators: [{ board: "atlas", lease: { epoch: 4, holder: "conn_lead", expires_at: ago(-5), live: true }, open_steps: 2, threads: [{ thread_id: "thr_builder_9", card: 444, state: "started", updated_at: ago(4) }] }],
  },
  birch: { applicable: true, machine: { id: "mach_00000000000000ff", label: "birch-runner", enabled: true, revoked: false }, connections: [{ kind: "machine", name: "birch-runner (machine)", last_used_at: ago(2), revoked: false }, { kind: "worker", name: "birch-runner (worker)", last_used_at: null, revoked: false }], runs: [], coordinators: [] },
};

const hopAt = (hop, at) => ({ link: hop.link, from: hop.from, to: hop.to, method: hop.method, known: hop.known, stale: hop.stale, status: hop.status, ...(hop.known ? { observed_at: ago(at) } : {}) });

/** The sites part of a node read, derived from the map fixture's routes. */
function sitesOf(key, payload) {
  const found = [];
  for (const route of payload.routes) {
    const mine = route.hops.map((hop, index) => ({ hop, index })).filter(({ hop }) => hop.from === key || hop.to === key);
    if (route.site !== key && !mine.length) continue;
    const last = route.hops.length - 1;
    const role = route.site === key ? "site" : mine.some(({ hop, index }) => (index === last && hop.to === key) || (hop.from === key && hop.method === "hosts")) ? "hosted" : "through";
    found.push({ site: route.site, role, stale: route.stale, observed_at: ago(15), expires_at: ago(-60 * 24 * 30), sources: [source("conn_scout", 15, "dns zone")], hops: route.hops.length, at: mine.map(({ hop, index }) => ({ index, ...hopAt(hop, 15) })) });
  }
  return found;
}

export function nodeRead(key, payload = fixture()) {
  const fields = FIELDS[key];
  const record = payload.nodes.find((node) => node.key === key);
  const stale = !!record?.stale;
  return structuredClone({
    as_of: new Date(NOW).toISOString(),
    node: fields ? { key, found: true, stale, conflicted: !!record?.conflicted, fields } : { key, found: false },
    sites: sitesOf(key, payload),
    agents: AGENTS[key] || { applicable: false, reason: "not_a_runner" },
  });
}

const hopView = (id, payload, observed = 5) => {
  const [from, rest] = id.split(">"); const [to, method] = rest.split(":");
  const link = payload.links.find((item) => item.id === id);
  const ends = payload.status.links[id];
  const brief = (key) => { const node = payload.nodes.find((item) => item.key === key); return node ? { key, known: true, name: node.values.name || key } : { key, known: false }; };
  return { link: id, from: brief(from), to: brief(to), method, known: !!link, stale: false, status: ends ? ends.to : "unknown", ...(link?.values?.via ? { via: link.values.via } : {}), ...(link ? { observed_at: ago(observed) } : { note: "no facts are recorded for this link" }) };
};

/** What topology_route answers for `target`: hosting for a site, the recorded ways in for a machine. */
export function routeRead(target, payload = fixture()) {
  const base = { target, as_of: new Date(NOW).toISOString() };
  const site = payload.routes.find((route) => route.site === target);
  if (site) {
    const hops = site.hops.map((hop) => hopView(hop.link, payload));
    return { ...base, mode: "hosting", found: true, status: hops.every((hop) => hop.known) ? "complete" : "partial", route: { site: target, stale: false, hops }, hosted_on: null, answer: `site '${target}' is routed ${[hops[0].from.key, ...hops.map((hop) => hop.to.key)].join(" -> ")}.` };
  }
  const node = payload.nodes.find((item) => item.key === target);
  if (!node) return { ...base, mode: "connect", found: false, status: "unknown", node: null, routes: [], answer: `unknown: nothing is recorded for '${target}'; it is not a known machine or site` };
  const inbound = payload.links.filter((link) => link.to === target && ["direct", "jump_host", "tailscale", "relay", "hosts"].includes(link.method));
  return { ...base, mode: "connect", found: true, node: { key: target, known: true }, status: inbound.length ? "partial" : "unknown", routes: inbound.map((link) => ({ status: payload.status.links[link.id].to, stale: false, hops: [hopView(link.id, payload)] })), answer: inbound.length ? `${inbound.length} recorded way(s) in to '${target}'; pass 'from' for the full path from one machine` : `unknown: no recorded way to reach '${target}'` };
}

/** A scripted `api` for the panel: the node and route reads of this fixture, call counts included. */
export function scriptedApi(payload = fixture()) {
  const calls = [];
  const api = async (path) => {
    calls.push(path);
    const url = new URL(path, "https://hub.example.test");
    if (url.pathname === "/api/topology/node") return structuredClone(nodeRead(url.searchParams.get("key"), payload));
    if (url.pathname === "/api/topology/route") return structuredClone(routeRead(url.searchParams.get("target"), payload));
    if (url.pathname === "/api/topology") return structuredClone(payload);
    throw Object.assign(new Error(`unscripted ${path}`), { status: 404 });
  };
  api.calls = calls;
  return api;
}
