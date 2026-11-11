// Sample data shared by the three directions. Everything here is invented for the mock-ups:
// a workspace called Northwind, one person, six bots, six devices and two sites.
// Counts shown on screen are computed from these rows, never typed in twice.

export const ws = { name: "Northwind", user: { name: "Maya Okafor", first: "Maya", initial: "M" } };

export const bots = [
  { id: "atlas", name: "Atlas", kind: "project", job: "Website and sign-in", state: "needs_you", ago: "2 min", last: "The login fix is ready. Going live needs your OK.", hue: 196 },
  { id: "harbor", name: "Harbor", kind: "project", job: "Mobile app", state: "working", ago: "14 min", last: "Two agents are on the push-notification bug.", hue: 152 },
  { id: "ledger", name: "Ledger", kind: "project", job: "Invoices and exports", state: "needs_you", ago: "1 h", last: "Which currency should the export use?", hue: 78 },
  { id: "scout", name: "Scout", kind: "custom", job: "Reads the web and reports back", state: "working", ago: "31 min", last: "Reading 12 sources on passkey adoption.", hue: 38 },
  { id: "general", name: "General", kind: "general", job: "Anything that is not a project", state: "idle", ago: "Yesterday", last: "You: What did we decide about the offsite?", hue: 0 },
  { id: "notes", name: "Notes", kind: "custom", job: "Keeps my reading list", state: "idle", ago: "Mon", last: "Saved. 14 items on the list.", hue: 330 },
];

// Agents running right now: one row per agent.
export const agents = [
  { bot: "atlas", task: "Move images to the CDN", machine: "build", since: "43 min" },
  { bot: "harbor", task: "Push-notification bug", machine: "build", since: "14 min" },
  { bot: "harbor", task: "Push-notification bug, iOS build", machine: "studio", since: "11 min" },
  { bot: "scout", task: "Passkey adoption report", machine: "studio", since: "31 min" },
];

// What waits on the person, across all bots. One click to open, one to accept.
export const waiting = [
  { id: "w1", bot: "atlas", kind: "approve", title: "Put the login fix live on northwind.example", why: "This changes the public site.", facts: ["214 of 214 checks passed", "3 files changed", "Runs on Build box, about 2 minutes"], accept: "Accept", decline: "Not now", ago: "2 min" },
  { id: "w2", bot: "ledger", kind: "choose", title: "Which currency should the export use?", why: "The March export has invoices in euros and dollars.", options: ["Euros", "Dollars", "Keep both"], ago: "1 h" },
];

export const thread = [
  { role: "you", at: "10:02", text: "The login page sends people back to the start after they sign in. Fix it, and put it live today if the checks pass." },
  { role: "bot", at: "10:02", text: "On it. I gave this to the Atlas agents and one has started on Build box. I'll come back when there is something to look at.", did: ["Filed “Fix login redirect”", "Started an agent on Build box"] },
  { role: "bot", at: "10:31", text: "The fix is ready. All 214 checks passed, and people now land back on the page they came from.", work: { title: "Fix login redirect", status: "ready", note: "29 min on Build box · 3 files changed", link: "See the change" }, waiting: "w1" },
  { role: "you", at: "10:33", text: "While I look: are the footer links still pointing at the old help site?" },
  { role: "bot", at: "10:33", text: "Yes. 4 of the 9 footer links still go to the old help site. I can fix them in the same release.", suggest: ["Fix the 4 footer links", "Show me the links"] },
];

export const project = {
  bot: "atlas",
  name: "Atlas",
  job: "Website and sign-in",
  orchestrator: "Running",
  site: "northwind.example",
  work: [
    { title: "Fix login redirect", status: "needs_you", note: "Ready. Going live needs your OK.", where: "Build box", when: "2 min" },
    { title: "Move images to the CDN", status: "working", note: "An agent is on it.", where: "Build box", when: "43 min" },
    { title: "Rotate the staging keys", status: "blocked", note: "Waiting for Staging VM to reconnect.", where: "Staging VM", when: "6 min" },
    { title: "Update footer links", status: "queued", note: "Suggested by Atlas, not started.", where: "", when: "" },
    { title: "Add passkey sign-in", status: "queued", note: "Next after the login fix.", where: "", when: "" },
  ],
  done: [
    { title: "Clean up the old sign-up form", when: "Wed" },
    { title: "Renew the site certificate", when: "Tue" },
    { title: "Add the terms page", when: "Tue" },
    { title: "Fix the broken invoice email", when: "Mon" },
    { title: "Speed up the pricing page", when: "Mon" },
  ],
  memory: ["Ship only after the checks pass.", "Dana reviews anything about billing.", "No releases on Fridays."],
};

export const STATUS = {
  needs_you: "Needs you",
  working: "Working",
  blocked: "Blocked",
  queued: "Queued",
  ready: "Ready",
  done: "Done",
  idle: "Idle",
};

// Topology. x/y are desktop positions in a 1100 x 660 field, px/py phone positions in 390 x 590.
export const nodes = [
  { id: "phone", type: "phone", name: "Maya's phone", role: "Approvals", state: "up", x: 80, y: 200, px: 62, py: 62 },
  { id: "laptop", type: "pc", name: "Maya's laptop", role: "Runner, idle", state: "up", x: 80, y: 440, px: 195, py: 62 },
  { id: "hub", type: "hub", name: "Commonplace", role: "Hub", state: "up", x: 290, y: 320, px: 195, py: 190 },
  { id: "studio", type: "mac", name: "Studio Mac", role: "Runner, 2 agents", state: "up", x: 520, y: 90, px: 328, py: 62 },
  { id: "build", type: "server", name: "Build box", role: "Runner, 2 agents", state: "up", x: 520, y: 250, px: 62, py: 320 },
  { id: "vm", type: "vm", name: "Staging VM", role: "Runner, reconnecting", state: "warn", x: 520, y: 410, px: 195, py: 320 },
  { id: "tower", type: "tower", name: "Old tower", role: "Runner, offline 3 days", state: "down", x: 390, y: 580, px: 328, py: 320 },
  { id: "edge", type: "router", name: "Edge proxy", role: "Reverse proxy", state: "up", x: 720, y: 330, px: 128, py: 440 },
  { id: "cdn", type: "cloud", name: "CDN", role: "DNS and cache", state: "up", x: 880, y: 330, px: 262, py: 440 },
  { id: "site1", type: "site", name: "northwind.example", role: "Website", state: "up", x: 1030, y: 250, px: 128, py: 545 },
  { id: "site2", type: "site", name: "docs.northwind.example", role: "Docs site", state: "warn", x: 1030, y: 410, px: 290, py: 545 },
];

// A link has a status at each end: [from, to, status at from, status at to].
export const links = [
  ["phone", "hub", "up", "up"],
  ["laptop", "hub", "up", "up"],
  ["hub", "studio", "up", "up"],
  ["hub", "build", "up", "up"],
  ["hub", "vm", "up", "warn"],
  ["hub", "tower", "up", "down"],
  ["build", "edge", "up", "up"],
  ["vm", "edge", "warn", "up"],
  ["edge", "cdn", "up", "up"],
  ["cdn", "site1", "up", "up"],
  ["cdn", "site2", "up", "up"],
];

export const selected = {
  id: "build",
  tabs: ["Physical", "Config", "Agents", "Sites"],
  tab: "Agents",
  physical: [["Kind", "Linux server"], ["Processor", "16 cores"], ["Memory", "64 GB"], ["Place", "Office rack"]],
  config: [["Runner", "1.8.2"], ["May work for", "Atlas, Harbor"], ["Trust", "Attested by the owner, not verified"], ["Longest run", "4 h"]],
  sites: [["northwind.example", "through Edge proxy"]],
};

// Per-project tallies for overview tables. Atlas is counted from its own rows above.
const tally = (list) => Object.fromEntries(["needs_you", "working", "blocked", "queued"].map((k) => [k, list.filter((w) => w.status === k).length]));
export const tallies = {
  atlas: { ...tally(project.work), done: project.done.length },
  harbor: { needs_you: 0, working: 2, blocked: 0, queued: 3, done: 4 },
  ledger: { needs_you: 1, working: 0, blocked: 0, queued: 1, done: 2 },
};

const runners = nodes.filter((n) => n.role.startsWith("Runner"));
export const counts = {
  bots: bots.length,
  waiting: waiting.length,
  agents: agents.length,
  runners: runners.length,
  up: runners.filter((n) => n.state === "up").length,
  warn: runners.filter((n) => n.state === "warn").length,
  down: runners.filter((n) => n.state === "down").length,
  sites: nodes.filter((n) => n.type === "site").length,
  open: project.work.length,
  done: project.done.length,
};

export const settings = {
  areas: ["Profile", "Bots and models", "Approvals", "Notifications", "Machines", "People", "Plan", "Under the hood"],
  area: "Approvals",
  // mode: "own" (bots do it on their own) | "ask" (one tap from you); locked rows cannot change.
  classes: [
    { title: "Look things up and read", note: "Status, work, machines, memory.", mode: "own", locked: true },
    { title: "File and order work", note: "Your words become a task for the project's agents.", mode: "own" },
    { title: "Start agents on your machines", note: "Uses a machine you allowed for that project.", mode: "ask" },
    { title: "Run a command on a machine", note: "You see the exact command before it runs.", mode: "ask" },
    { title: "Put changes live", note: "Anything that changes a public site or a release.", mode: "ask", locked: true },
    { title: "Spend money or invite people", note: "Plans, seats and access.", mode: "ask", locked: true },
  ],
  arrive: [
    { title: "In the chat", note: "Always. The bot asks where you asked.", on: true, locked: true },
    { title: "On your phone", note: "A push message you can accept from the lock screen.", on: true },
    { title: "By email after 30 minutes", note: "Only when nobody answered.", on: false },
  ],
  model: { fast: "Local model on Studio Mac", heavy: "Each project's own agents" },
};

for (const [what, ok] of [
  ["agents on machines add up", nodes.filter((n) => /(\d+) agents/.test(n.role)).reduce((s, n) => s + Number(/(\d+) agents/.exec(n.role)[1]), 0) === agents.length],
  ["waiting matches bots that need you", bots.filter((b) => b.state === "needs_you").length === waiting.length],
  ["runner states add up", counts.up + counts.warn + counts.down === counts.runners],
]) console.assert(ok, what);
