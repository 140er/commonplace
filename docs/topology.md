# Topology store

The single source of truth for the Compute map: machines, how they reach each other, and how a website is routed to the app that serves it. Everything is a structured fact in the workspace's `topology` wing. The map, the agent tools and the live status all read from this store; nothing else holds a second copy.

Code: `commonplace/topology/model.py` (what may be stored) and `commonplace/topology/store.py` (how it is stored and read). Tests: `tests/test_topology_store.py`. How agents write and the owner approves is under Proposals below; how an agent finds a website's route is under Site route discovery; how one button maps the user's machines is under Map discovery.

## Facts

One fact is `(subject, predicate, object)` in one workspace, wing `topology`:

| Subject | Predicates | Example |
| --- | --- | --- |
| `node:<key>` | `kind`, `name`, `role`, `hostname`, `aliases`, `tailscale_name`, `runner_id`, `wings`, `hardware`, `os`, `resources` | `node:web1 os "Linux 6.8"` |
| `link:<from>><to>:<method>` | `method`, `status`, `via`, `alias`, `port` | `link:laptop>web1:tailscale status "up"` |
| `route:<site>` | `hops` | `route:example.test hops ["example.test>cdn:dns", "cdn>web1:reverse_proxy"]` |

The object is canonical JSON. A key is lowercase letters, digits, dots, dashes and underscores (up to 63 characters), so a domain name is a valid key for a website node.

Facts are stored per field, not per record. Two observers can therefore each contribute fields to the same node, and a disagreement about one field does not touch the others.

The facts live in the workspace file `topology.sqlite3` (tables `topology_facts`, `topology_observations`, `topology_conflicts`), next to the other workspace stores. The file is therefore encrypted with the workspace when workspace encryption is on, is covered by backups, and holds one workspace only. The hub's control database holds no topology text. The facts are not rows of the memory knowledge graph: graph facts point at a chat range and carry person-approval semantics, while a topology fact points at an observer and its evidence, expires, and can be contested. The wing name is the access rule (see Access).

### Node kinds

`pc`, `mac`, `phone`, `vm`, `server`, `router` (router or edge), `cloud` (cloud or CDN, including a DNS provider), `website`, `container` (container or app).

A node's first write must include `kind`. Node fields:

| Field | Value |
| --- | --- |
| `kind` | one of the kinds above |
| `name`, `role`, `hardware`, `os` | one line of text, up to 200 characters |
| `hostname`, `tailscale_name` | a hostname or address the user chose to record |
| `aliases` | list of connection aliases (for example an ssh alias); stored sorted, without duplicates |
| `runner_id` | the Compute runner machine id (`mach_…`) when the node is a runner |
| `wings` | list of wing names the machine works in |
| `resources` | object with only `cpu`, `gpu` (text) and `cores`, `memory_gb`, `storage_gb` (numbers) |

### Link kinds and methods

A link says that `from` reaches `to` by one method. Two nodes may be linked by several methods at once; each is its own link.

Methods: `direct`, `jump_host`, `tailscale`, `relay`, `dns`, `proxy`, `reverse_proxy`, `hosts` (a machine hosts a container or app).

| Field | Value |
| --- | --- |
| `method` | the link's identity; always equal to the method in its id, written automatically |
| `status` | observed status: `up`, `degraded`, `down` or `unknown` |
| `via` | key of the node in between (the jump host or the relay) |
| `alias` | the name to use on this hop (ssh alias, Tailscale name) |
| `port` | 1 to 65535 |

### Site routes

A route is the ordered chain of link ids from a website node to where it is served, for example DNS provider, proxy or CDN, reverse proxy, host, container. The first hop starts at the site and each hop starts where the previous one ended; a broken chain is refused. A route may name a link that has no facts yet. Reads resolve every hop against the link facts and say `known: false` for such a hop rather than inventing one.

## Provenance

Every write names an `Observer(kind, id)` and verbatim `evidence`:

- `kind` is `agent` (id: the connection id), `runner` (id: the machine id) or `owner` (id: the user id). The caller takes it from the authenticated principal, never from tool arguments.
- `evidence` is the text the observer saw (command output, a config line), 1 to 4000 characters, stored exactly as given.

Each observation is kept with `observer`, `evidence`, `first_observed_at`, `observed_at`, `expires_at` and `seen_count`. The same observer repeating the same evidence updates that observation's times and count; anything else adds a row. Observations are never deleted.

## Expiry

Each observation expires. The default lifetime is 30 days, and 15 minutes for a link's `status`; a write may pass `ttl_seconds` between 60 seconds and 365 days. A fact's `expires_at` is the latest expiry among its observations.

An expired fact is stale. It fades instead of lying:

- a record's `values` holds only fresh values; a stale `status` is simply absent there, and a route hop reports `status: "unknown"`;
- `fields` still returns the stale value with `stale: true`, its times and its provenance, so a reader can say "last seen up three days ago";
- a record whose identity field (`kind`, `method`, `hops`) is stale has `stale: true`;
- a fresh observation of a different value replaces a stale value without a conflict.

Nothing is deleted when it expires.

## Conflicts

A write of a different value for a fact that has a current value goes one of two ways:

- **Superseded.** The old value is expired, or no other observer has a fresh observation of it (the observer is correcting itself). The new value becomes current; the old one stays in history with `superseded_by`.
- **Conflict.** Another observer still vouches for the current value. The current value stays. The new value is stored as a contender and a conflict record links the two, each with its own provenance. Reads show the contender under the field's `conflicts` and in the graph's `conflicts` list. The same disagreement reported again adds an observation to the same record.

A conflict is closed by `resolve_conflict` (accepted or rejected, by the owner), by the contender's only observer later agreeing with the current value (withdrawn), or by an authoritative write or a retraction (replaced). A rejected value reported again opens a new conflict.

`replace=True` is the authoritative write for a change the owner approved: it supersedes the current value whoever observed it and closes the open conflicts on that fact.

Fact states: `current`, `contender`, `superseded`, `rejected`, `withdrawn`, `retracted`. Conflict states: `open`, `accepted`, `rejected`, `withdrawn`, `replaced`.

## The secret filter

The store holds aliases, hostnames, runner ids and wings. It never holds keys, tokens, passwords or private key material. Three rules enforce that on every write, including `retract` and the `facts_for` check that proposals use:

1. **Allow-list.** Only the fields named above exist. Any other field is refused; a field named like a secret (`password`, `api_key`, `ssh_private_key`, …) is refused as a secret.
2. **Shapes.** Short fields have strict shapes (a key, a hostname, an alias, a machine id, a port, a choice from a list), which leaves no room for a credential.
3. **Text scan.** Every text value, the evidence and the observer id are scanned with the hub's existing secret detectors (`commonplace.privacy.scan`, `commonplace.redact.redact`) plus a rule for spelled-out secrets ("the password is …"). A hit refuses the whole write.

A refusal raises `SecretRefused` with a message such as:

```
no: evidence looks like it holds a secret (private_key); topology stores aliases, hostnames, runner ids and wings, never keys, tokens, passwords or private key material
```

The message names the field and the kind of secret, never the value. Evidence is not cleaned and stored: verbatim evidence either passes or the write is refused, and a refused write stores nothing. Naming a key file by path or alias (`IdentityFile ~/.ssh/id_ed25519`) is allowed; its contents are not.

## Access

The store does not authorize; the caller does, with the existing wing rules, before calling it:

- read: `policy.can_read_wing(TOPOLOGY_WING)`;
- write: `policy.can_write_wing(TOPOLOGY_WING)`.

The owner reads every wing, so the topology is owner-readable by default. An agent sees it when its connection's wing grants include `topology`. A store is one workspace's file, so no read returns another workspace's facts.

## Tools and routes

Two read tools, with a matching REST route each, let every agent ask the map instead of guessing. Code: `commonplace/topology/answers.py` (the answers, pure functions over `read_graph`) and `commonplace/topology/extension.py` (the tools, the routes and the access check). Tests: `tests/test_topology_tools.py`.

Access is the same as any memory read: the connection needs the `memory:read` scope and `policy.can_read_wing(TOPOLOGY_WING)`. A connection without the scope does not see the tools; a connection that has the scope but no read grant on the `topology` wing gets `no: this connection cannot read the topology wing` (REST: 403) and no data. The owner reads every wing. The store is one workspace's file, so an answer never holds another workspace's facts, and it holds no secrets because none are stored. Both tools are in every tool profile, so an agent with only these tools can answer both questions.

| Tool | REST | Arguments |
| --- | --- | --- |
| `topology_get` | `GET /api/topology` | `node` (a machine or site key) or `site` (a website's route); neither returns the whole graph |
| `topology_route` | `GET /api/topology/route` | `target` (required), `from` (the machine you start from) |

Every answer carries `as_of` (when it was read) and says `found: false` with an `answer` starting `unknown:` when nothing is recorded; an unknown key is an answer, not an error. Bad arguments are refused with a `no:` message (REST: 400).

### `topology_get`

- `node`: `found`, the node record (values, fields with provenance and expiry, conflicts), `links` (every link that starts or ends at it) and `routes` (when the node is a site).
- `site`: `found`, the route record with every hop resolved, and the site's node if it has one.
- neither: `counts`, `nodes`, `links`, `routes` and the open `conflicts`.

Records have the shape described under Reads: `values` holds fresh values only, `fields` also holds stale values with `stale: true`.

### `topology_route`

The target decides which question is answered. A target that has a route, or is a `website` node, is a hosting question; any other target is a connection question.

**Where is site Y hosted** (`mode: "hosting"`): the recorded route in order (for example DNS provider, proxy or CDN, reverse proxy, host, container). `status` is `complete` when every hop is known and fresh and the route itself was confirmed recently, `partial` when not, and `unknown` when the site has no route. `unknown_hops`, `stale_hops` and `down_hops` name the links; `hosted_on` is the node the route ends at; `answer` is one plain sentence. A hop with no link facts has `known: false` and is never filled in.

**How do I connect to X** (`mode: "connect"`): with `from`, up to three loop-free paths over the links whose method is `direct`, `jump_host`, `tailscale`, `relay` or `hosts`, best first. A way ranks by hop count, with a penalty for a hop that is degraded, down, unknown or stale, so a fresh working way comes before a doubtful one. Without `from`, the recorded single-hop ways in to the target (`status: "partial"`; pass `from` for the full path). `node` holds what to use on the target: `aliases`, `hostname`, `tailscale_name`, `runner_id`, `wings` (fresh values; `stale_fields` names the ones that expired).

A route's `status` is `down` if any hop is down, else `unknown` if any hop has no fresh status or is stale, else `degraded` if any hop is, else `up`. A start that is not recorded, a target that is not recorded, and a target nothing reaches each answer `status: "unknown"` with `routes: []` and the reason in `answer`.

A hop shows `link`, `from` and `to` (`key`, `known`, and `kind` and `name` when recorded), `method`, `known`, `stale`, `status` (`unknown` when the last status expired, with `status_observed_at`, `status_expires_at` and a `status_note` so a reader can say "last seen up three days ago"), and `via`, `alias`, `port` when set and fresh.

## Python API

```python
from commonplace.topology import TOPOLOGY_WING, Observer, SecretRefused, TopologyError, model, store
```

`topo` is the workspace's `TopologyStore`:

```python
topo = store.open_topology(gateway, workspace_id)   # in the hub: one cached store per workspace
topo = store.TopologyStore(workspace_dir)           # in tests: a directory, or a callable returning it
```

`now` is an optional timezone-aware `datetime` (tests pass a fake clock). All refusals raise `TopologyError` (a `ValueError`); `SecretRefused` and `store.NotFound` are subclasses. Messages start with `no:` and are safe to show.

### Writes

```python
store.upsert(topo, items, *, observer, evidence,
             ttl_seconds=None, replace=False, now=None) -> list[dict]
store.upsert_node(topo, key, fields, **write) -> list[dict]
store.upsert_link(topo, source, target, method, fields=None, **write) -> list[dict]
store.upsert_route(topo, site, hops, **write) -> list[dict]
store.retract(topo, subject, *, observer, evidence, predicate=None, now=None) -> int
store.resolve_conflict(topo, conflict_id, *, accept, actor, now=None) -> dict
model.facts_for(items) -> list[tuple[str, str, object]]
```

`items` is a list of `{"node": key, "fields": {...}}`, `{"link": [from, to, method], "fields": {...}}` (fields optional) and `{"route": site, "hops": [link ids]}`. One call is one transaction: everything is validated first, and a refusal or a crash stores nothing.

`upsert` returns one entry per fact: `subject`, `predicate`, `fact_id` and `action`, which is `added`, `refreshed`, `superseded` (with `previous`) or `conflict` (with `conflict_id` and `current`).

`retract` takes a subject (`node:<key>`, `link:<id>`, `route:<site>`), or one field of it, off the map and returns how many current facts it retracted. The facts stay in history and the retraction is recorded as an observation. Retracting a node does not retract the links and routes that mention it.

`facts_for` validates items, secret filter included, without storing anything.

### Reads

```python
store.get_node(topo, key, *, now=None) -> dict | None
store.get_link(topo, source, target, method, *, now=None) -> dict | None
store.get_route(topo, site, *, now=None) -> dict | None
store.read_graph(topo, *, now=None) -> dict
store.list_conflicts(topo, *, open_only=True, now=None) -> list[dict]
store.history(topo, subject, *, predicate=None, now=None) -> list[dict]
```

A record:

```python
{
    "subject": "node:web1", "wing": "topology",
    "key": "web1",                    # link: "id", "from", "to", "method"; route: "site", "hops"
    "values": {"kind": "server"},     # fresh values only
    "stale": False,                   # the identity field has expired
    "conflicted": False,              # some field has an open conflict
    "fields": {
        "kind": {
            "fact_id": "tf_…", "value": "server", "state": "current",
            "observed_at": "2026-01-01T10:00:00Z", "expires_at": "2026-01-31T10:00:00Z",
            "stale": False,
            "provenance": [{"observer": {"kind": "runner", "id": "mach_…"}, "evidence": "…",
                            "first_observed_at": "…", "observed_at": "…", "expires_at": "…",
                            "stale": False, "seen_count": 1}],
            "conflicts": [],          # contenders: the same shape plus "conflict_id"
        },
    },
}
```

A route's `hops` is a list of `{"link", "from", "to", "method", "known", "stale", "status"}`.

`read_graph` returns `{"wing", "nodes", "links", "routes", "conflicts"}`. A conflict is `{"conflict_id", "subject", "predicate", "state", "created_at", "resolved_by", "resolved_at", "current", "contender"}`, where both values have the field shape above. `history` returns every value a subject held, newest first, each with `predicate`, `state` and `superseded_by`.

## Proposals: agents write, the owner approves

Agents do not call the store. They hand in facts through one tool, `topology_propose`, and the hub decides what may be written at once and what the owner must approve. Code: `commonplace/topology/proposals.py` (the rules) and `commonplace/topology/proposal_api.py` (the tool, the routes and the decision listener). Tests: `tests/test_topology_proposals.py`.

```
topology_propose(items=[...], remove=[...], evidence="...", ttl_seconds=None, board=None)
POST /api/topology/proposals      the same arguments as JSON
```

`items` are the write items of the store. `remove` lists subjects to take off the map (`"route:example.test"`), or `{"subject": "node:web1", "field": "os"}` for one field. `evidence` is required and kept verbatim. The caller needs write access to the `topology` wing; the observer comes from the authenticated caller, never from the arguments.

The request is compared with the map as it is now, fact by fact:

| Caller | Fact | What happens |
| --- | --- | --- |
| a runner, about itself | new | applied at once |
| a runner, about itself | same value | refreshed: one more observation, a later expiry |
| a runner, about itself | different value | proposed |
| a runner, the `status` of its own link | different value | written at once as an observation (the optional path report of Live status); counted as `observed` |
| a runner, about anything else | new or different | proposed |
| any other agent | new or different | proposed |
| any other agent | same value | nothing; counted as `unchanged` |
| the signed-in owner | new, different or removed | applied at once, as the authoritative value |
| anyone but the owner | removed | proposed |

A runner is the worker connection of an enabled machine whose grant includes the `topology` wing; its observer is `runner` with the machine id. "About itself" is a node whose `runner_id` is that machine, a new node the runner declares with its own `runner_id`, and a link with such a node at either end. Routes, other nodes, and binding itself to a node that already exists are not about itself.

Everything proposed in one call is one proposal and one decision. The reply holds `applied` and `proposal` (each `null` or a row), `unchanged` and `observed`. The same observer proposing exactly the same change while its decision is still open gets that proposal back instead of a second card. The decision is opened before anything is written: when it cannot be opened (no board the caller may write), the call fails and stores no fact.

### The decision

A proposal opens one ask for the workspace owner through the ordinary ask path, so it shows up in "needs you" like any other question. It carries two structured options:

- **Approve**, with the map after the change as its preview;
- **Reject**, with the map before the change as its preview.

```
Before:                 After:
node web1               node web1
  os: Linux 6.8           os: Linux 6.9
  role: (not set)         role: web server
```

A removed value reads `(removed)`. The ask's context holds the proposal id, the observer and the evidence. Nothing is cut short: a change that does not fit one preview is refused with "propose it in smaller parts", so the owner never approves a line they could not read.

The owner answers the card as they answer any ask. Then:

- **Approve** applies the change with the proposing observer and its evidence as provenance, as an authoritative write (`replace=True`). The old values stay in history.
- **Reject**, or a written reply without a choice, changes nothing. The old fact stays. The same change proposed again within seven days, by any observer, gets that rejected proposal back instead of a new card; a different change, or the owner's own write, is not held back. Proposals never reach the store before approval, so a rejected proposal leaves no conflict record and a repeat opens none.
- The proposal is **void**, and nothing is applied, when the card was edited, withdrawn or asked again before the owner answered it, when the answer does not come from a workspace owner, or when the map no longer matches the "before" that was shown. A void proposal can simply be proposed again.

Only the owner can answer: the board's decide path refuses everyone else, and the proposal checks the answer again against the card's own history. An owner's connection that holds `cards:decide` answers on the owner's behalf, as for any ask.

The answer is applied when the card is decided. If the hub stopped in between, the next read of the proposals settles it; applying is safe to repeat.

### History and undo

Every applied change, a runner's self-report and an owner's own write included, is kept as a row with each fact's value before and after.

```
GET  /api/topology/proposals[?state=pending|applied|rejected|void|undone]
GET  /api/topology/proposals/<id>
POST /api/topology/proposals/<id>/undo        the signed-in workspace owner only
```

A row: `id`, `state`, `origin` (`proposal`, `self_report` or `owner`), `observer`, `proposed_by`, `evidence`, `changes` (`subject`, `predicate`, `before`, `after`; `null` means no value), `card`, `created_at`, `decided_by`, `decided_at`, `note`, `undone_by`, `undone_at`. Reading needs read access to the `topology` wing.

Undo puts back what the change replaced: a changed or removed value is written again as the owner's authoritative value, and a subject or field the change added is retracted. A fact that changed again since is left alone and listed under `skipped`. Undo deletes nothing; the undone values stay in the store's history.

### The secret filter

A proposal passes the same filter as a write (`model.facts_for`, `model.evidence_text`) before a row or a decision card exists. A refused proposal stores nothing and asks nothing, and the refusal names the field and the kind of secret, never the value.

### Storage

Proposals are rows of `topology_proposals` in `topology.sqlite3`, next to the facts. The table is added to an existing file by the first proposal; a workspace without proposals reads as an empty list and reading creates nothing.

## Live status

Live status of machines and of the two ends of every link, derived from signals that runners already send. There is no probing daemon: nothing in this section opens a connection to a machine.

Code: `commonplace/topology/status.py`. Tests: `tests/test_topology_status.py`.

### The read

The one read the read tools (`topology_get`) and the canvas call:

```python
from commonplace.topology import status

status.read_status(gateway, workspace_id, *, graph=None, now=None, config=None) -> dict
```

`graph` is a `store.read_graph` result the caller already holds (so the map and its status describe the same moment); when `None` the function reads the workspace's graph itself. `now` is an optional timezone-aware `datetime` (tests pass a fake clock). `config` is a `status.StatusConfig`; `None` means `StatusConfig.from_env()`. The caller authorizes (`policy.can_read_wing(TOPOLOGY_WING)`) before calling; the read opens no workspace other than `workspace_id` and creates no topology file.

The pure core, which tests and other callers can use with their own signals, takes no gateway:

```python
status.derive(graph, signals, *, now, config) -> dict      # signals: dict[machine_id, MachineSignals]
status.machine_status(signals, *, now, config) -> dict     # one MachineSignals -> one status record
status.collect_signals(gateway, workspace_id, *, now) -> dict[str, MachineSignals]   # the only reader of the hub's tables
```

The result:

```python
{
    "as_of": "2026-01-01T10:00:00Z",
    "config": {"green_within_s": 180, "red_after_s": 900, "path_fresh_s": 900, "path_unknown_after_s": 3600},
    "machines": {"mach_…": STATUS},      # every enrolled runner machine, with or without a node
    "nodes":    {"web1": STATUS},        # every node of the graph
    "links":    {"laptop>web1:tailscale": {**STATUS, "ends": {"from": STATUS, "to": STATUS}}},
}
```

A `STATUS` record:

```python
{"status": "up" | "degraded" | "down" | "unknown",
 "color": "green" | "orange" | "red" | "grey",     # grey is unknown, never green
 "reason": "<code>",
 "last_signal_at": "2026-01-01T09:59:12Z" | None,  # machines and nodes; for a link end: the path report
 "last_signal_source": "<signal name>" | None,
 "age_s": 48 | None}
```

A link's record is a `STATUS` plus `ends`; each end is a `STATUS` plus `basis` (`node` when the end's own node decided it, `path` when the path report did). `last_signal_at` and `age_s` of a link end are those of the record that decided it.

The status words are the ones a link's `status` fact already uses, so a link's reported status and its derived status compare directly.

### Rules

One rule set, applied in this order. Times are ages at `now`; a signal dated in the future counts as age 0.

**Machine** (an enrolled runner machine; a node is its machine when its `runner_id` names it):

| Condition | Status | Reason |
| --- | --- | --- |
| machine revoked | down | `revoked` |
| machine disabled | down | `disabled` |
| no signal ever | unknown | `never_reported` |
| newest signal older than `red_after_s` | down | `no_heartbeat` |
| newest signal older than `green_within_s` | degraded | `late_heartbeat` |
| a start is leased or starting | degraded | `start_in_progress` |
| otherwise | up | `heartbeat` |

The newest signal is the latest of the signals below. A machine that never produced one is unknown, not green, and a card start never makes an unknown machine green.

**Link path.** A runner (or an agent) may report a path with `upsert_link(..., {"status": ...})`; that is the optional path report. The report's age is `now - observed_at` of the link's newest `status` observation, stale or not:

| Report | Age | Path status | Reason |
| --- | --- | --- | --- |
| none | | unknown | `no_report` |
| any | up to `path_fresh_s` | as reported | `reported` |
| `up` | up to `path_unknown_after_s` | degraded | `late_report` |
| `down` or `degraded` | up to `path_unknown_after_s` | as reported | `late_report` |
| any | older | unknown | `report_stale` |

A path that is `down` while another link between the same two nodes (any direction, any other method) is `up` is `degraded` with reason `alternate_path_up`: the target is still reachable, one path is failing.

**Link end.** An end shows the worse of its node's status and the path's status, ignoring `unknown`; when both are unknown the end is unknown, with the reason of the one that saw a signal (a stale report) when there is one. Worse means `down` over `degraded` over `up`. A node that is not a runner machine has no signals of its own, so its end shows the path report alone.

**Link.** The link's own status is the worse of its two ends by the same rule.

### Signals that exist today

Each signal is read from state the hub already keeps; none is new.

| Signal | Source | What it proves |
| --- | --- | --- |
| `machine_connection` | `connections.last_used_at` of the machine's connection (`runner_machines.machine_connection_id`), touched by the hub on every authenticated runner request, at most once a minute | the runner reached the hub |
| `worker_connection` | `connections.last_used_at` of the machine's worker connection (`runner_machines.worker_connection_id`) | an agent started on the machine is working |
| `chat_poll` | `chat_runners_seen.seen_at` | the runner's chat relay polled |
| `start_success` | newest `runner_jobs.updated_at` in state `started` for the machine | the machine launched a card start |
| `start_in_progress` | any `runner_jobs` row of the machine in state `leased` or `starting` | a start is underway (orange) |

`runner_machines.enabled` and `revoked_at` give the disabled and revoked rows of the rule table.

### What does not exist, and the gap

- **No per-path reachability signal from runners.** Nothing on the hub carries a runner's view of a direct, jump host, Tailscale or relay path. The only path signal is the optional `status` fact of a link written through the store; until runners write it, every link's path is `unknown` / `no_report` and a link end shows its node alone. The Compute stack (not on develop) may add such a report; it must write the same `status` fact so this code needs no change.
- **No heartbeat for a non-runner node** (router, cloud, website, container): they stay `unknown` unless a path report names them.
- **No failed-start signal in the rules.** A failed or uncertain start does not change a machine's status; a machine that fails starts but keeps polling is `up`.
- **The thresholds are per hub, not per machine.** A machine configured with `poll_seconds` near 300 needs `green_within_s` of at least twice that.

### Configuration

Thresholds are configuration, not constants. `StatusConfig` holds them; build it with `StatusConfig(green_within_s=…)`, `StatusConfig.from_mapping({...})` or `StatusConfig.from_env()`.

| Field | Environment variable | Default | Meaning |
| --- | --- | --- | --- |
| `green_within_s` | `COMMONPLACE_TOPOLOGY_GREEN_S` | 180 | a signal this young is green |
| `red_after_s` | `COMMONPLACE_TOPOLOGY_RED_S` | 900 | no signal for longer is red |
| `path_fresh_s` | `COMMONPLACE_TOPOLOGY_PATH_FRESH_S` | 900 | a path report this young is trusted as reported |
| `path_unknown_after_s` | `COMMONPLACE_TOPOLOGY_PATH_STALE_S` | 3600 | a path report older than this is unknown |

Each is a whole number of seconds; `green_within_s` must be below `red_after_s` and `path_fresh_s` below `path_unknown_after_s`, or the config is refused with a `TopologyError`.

## Map read

The map canvas in the web app draws from `GET /api/topology` (the `topology_get` read with no `node` and no `site`), the same answer agents get. Beside `nodes`, `links`, `routes` and `conflicts` the whole-graph answer holds `status`: one state per node and one per end of a link, `up`, `degraded`, `down` or `unknown`. It is `status.read_status` of the same graph (see Live status), reduced to the one word that is drawn by `answers.status_words`; reasons and times stay in that read. A node that is not a runner machine and a link without a path report are `unknown`, never `up`. A node or site read has no `status` block.

```json
{
  "scope": "graph", "found": true, "as_of": "2026-11-12T10:00:00Z",
  "counts": {"nodes": 1, "links": 1, "routes": 0, "conflicts": 0},
  "nodes": [{"subject": "node:web1", "key": "web1", "values": {"kind": "server", "name": "Web 1"},
             "fields": {"kind": {"value": "server", "stale": false}}, "stale": false, "conflicted": false}],
  "links": [], "routes": [], "conflicts": [],
  "status": {
    "nodes": {"web1": "unknown"},
    "links": {"laptop>web1:tailscale": {"from": "up", "to": "up"}}
  }
}
```

The read needs the `memory:read` scope and a read grant on the `topology` wing, like every topology read. A stale node keeps its last `kind`, `name` and `role` in `fields` (each with `stale: true`), so the canvas draws a faded icon with its label and marks it stale.

## The map in the app

The owner opens the map from **Compute map** under **Workspace** in the account menu, or from the command palette (`#/compute-map`). It is drawn as SVG by `commonplace/web/static/topology-map.js` with `topology-layout.js` (layout, pan and zoom maths) and `topology-icons.js` (one icon per node kind, drawn for this app). Nothing is loaded from another origin.

- **Devices** are icons with the name and the role underneath. A stale device is faded with a dashed outline and says "stale"; a device with an open conflict says "conflict"; a device that a link or route names but that has no facts is drawn as an unknown device that says "not recorded".
- **Links** are lines with a status mark at each end. A status is a shape and a word as well as a colour: circle for up, triangle for degraded, square for down, ring for unknown. Each mark and each device carries its status as text for a screen reader and as a tooltip, and the legend names the four shapes. Two methods between the same two devices run side by side.
- **Layout** is automatic and the same on every load: rows from site to DNS or CDN, edge, machine and app, with the devices people sit at last. A device dragged somewhere stays there. Dragged places and the chosen view are remembered in the browser, per workspace; **Reset layout** forgets the places.
- **Moving around:** drag the background to pan, scroll or pinch to zoom, or use the buttons. With the canvas focused, the arrow keys pan, `+` and `-` zoom and `0` fits everything. On a focused device, Enter selects it and Shift with an arrow key moves it.
- **Route** highlights one site's route as a path, dims the rest, and writes the hops out in words, including a hop that is unknown or stale.
- **Live:** the map reads again every five seconds while the tab is visible and redraws only when something changed. If a read fails, the last map stays and a line says it is not updating.
- **Empty workspace:** the empty state has one button, "Start agent work on map". The canvas only offers it: a screen that gives the map an `onStartWork` handler turns it on (both screens do, with `map-discovery.js`, which posts to `/api/topology/discovery` and writes what happened on each machine under the button), and a screen without a handler shows it off and says so.
- **In the Deck look** the map is the Compute screen (`#/compute`; `#/compute-map` goes there and the account menu has no second entry). It draws this same canvas in Deck colours inside one card, with the legend under it and the chosen device's details (Physical, Config, Agents, Sites) in a second card. Links are routed around every device and label, so none runs through one. Names and roles keep their size (12px and 11px) at every zoom; the first view and **Fit** fit the graph's width and the card is as tall as the graph, so the page scrolls. When the topology read is empty or fails, it draws the hub and the enrolled runner machines from `GET /api/runners` instead, with the states Ready, Waiting for attestation and Off and nothing about liveness, sites or routes. Above it are one button each for **Add machine** and **Add site** (see "Add machine and add site"); a member who may not see the machines sees neither.

Selecting a device sends a `topology:select` event (`detail.key`) from the map's root element, which is where the detail panel attaches (see below).

## Node detail read

`GET /api/topology/node?key=<node key>` is what the node detail panel reads. It lives in `commonplace/topology/node_panel.py` and `node_agents.py`. Access is the `topology` wing grant (`policy.can_read_wing`), the same rule as the agent tools, so it is not owner-only; what the agents part adds is decided separately, below. It reads one workspace's store and creates no file.

```json
{
  "as_of": "2026-01-01T10:20:00Z",
  "node": {"key": "web1", "found": true, "stale": false, "conflicted": false,
           "fields": {"os": {"value": "Linux 6.8", "stale": false,
                             "observed_at": "2026-01-01T10:00:00Z", "expires_at": "2026-01-31T10:00:00Z",
                             "sources": [{"kind": "agent", "id": "conn_…", "observed_at": "…", "seen_count": 1, "evidence": "uname -sr"}],
                             "contenders": 0}}},
  "sites": [{"site": "example.test", "role": "hosted", "stale": false, "observed_at": "…", "expires_at": "…",
             "sources": [], "hops": 3, "at": [{"index": 2, "link": "web1>app:hosts", "from": "web1", "to": "app",
                                               "method": "hosts", "known": true, "stale": false, "status": "up",
                                               "observed_at": "…", "status_observed_at": "…"}]}],
  "agents": {"applicable": true, "machine": {"id": "mach_…", "label": "web1-runner", "enabled": true, "revoked": false},
             "connections": [{"kind": "machine", "name": "…", "last_used_at": "…", "revoked": false}],
             "runs": [{"job": "…", "state": "started", "board": "atlas", "card": 442, "project": "…", "topic": "…",
                       "thread_id": "…", "updated_at": "…", "started_at": "…", "log_ref": "…"}],
             "coordinators": [{"board": "atlas", "lease": {"epoch": 4, "holder": "…", "expires_at": "…", "live": true},
                               "open_steps": 2, "threads": [{"thread_id": "…", "card": 444, "state": "started", "updated_at": "…"}]}]}
}
```

- `node.fields` holds only the node fields of the allow-list (see Facts), with a stale value kept and marked `stale: true`; a key nothing is recorded for is `{"key": …, "found": false}`. `sources` are the observers of the value, newest first, with the first line of their evidence (up to 200 characters; evidence has already passed the secret filter).
- `sites` lists every route that starts at, ends at or passes through the node. `role` is `site` (the node is the website), `hosted` (the node ends the route, or is the machine that `hosts` what ends it) or `through`. `at` has the node's hops with their freshness; a hop without link facts says `known: false` and carries no times.
- `agents` is for a node whose `runner_id` is a machine enrolled in this workspace; any other node answers `{"applicable": false, "reason": "not_a_runner" | "machine_not_found"}`. Runs are start jobs of that machine that are queued, leased, starting or uncertain, or started with no end reported in the last six hours. Coordinators are boards whose live orchestrator config runs on the machine, with the checkpoint's lease and thread builders.
- Grants: `connections` is `null` unless the caller is a signed-in workspace owner. A run or coordinator is listed only when the caller may read the board's wing. A caller without the `topology` wing gets `403`, a request without a session `401`, a key that is not a node key `400`.

## The node detail panel

`commonplace/web/static/topology-detail.js` (with `topology-detail.css`) is a read-only panel for the device selected on the canvas. It creates its element on the first `topology:select`, reads the node read and `GET /api/topology/route?target=<key>` (the `topology_route` answer), and reads them again every five seconds while the device stays selected and the tab is visible. The canvas mounts it with one call; it changes the canvas only by setting the canvas's own route picker.

| Tab | Shows | From |
| --- | --- | --- |
| Physical | hardware, OS, CPU, GPU, cores, memory, storage; the newest observation time | `node.fields` |
| Config | hostname, Tailscale name, ssh alias, runner id, wings and the links that end at the device, as rows and as copyable text; below, the `topology_route` answer exactly as an agent gets it (answer sentence, each way in or each hop with method, alias, jump host, port and status), copyable | `node.fields`, the map's links, `GET /api/topology/route` |
| Agents | the machine, its connections, its runs with a link to the card and the log, and each coordinator's lease with the thread builders its checkpoint lists, each linking to its card or board | `agents` |
| Sites | each site the device hosts or passes, the hops where it appears with status, and a button that highlights that route on the map | `sites` |

- Every value shows where it came from (observer kind and id, or "runner registry", "start job", "checkpoint", "connection") and how old it is; a stale value is marked `stale` in words, dashed and shows when it was last observed.
- A device with nothing to show has a plain empty state (no hardware recorded, not a runner machine, no agents working, no site routed), and a device the map draws but the store has no facts for says "not recorded".
- Nothing is editable: there is no input, textarea or write call. Changes go through agent proposals.
- Only fields of the store's allow-list are shown, so a field outside it never appears even if a read carried one. Every value is escaped; a log reference that is not an `http` or `https` link is shown as text.
- A read the caller may not make says so in words ("You may not read the details of this device"); a missing route answer leaves the rest of the tab working.

The Config tab depends on the agent tools' REST read (`GET /api/topology/route`, card 438). Without it the facts still show and the route part says the answer is not available.

## Site route discovery

How a website is routed is found by an agent, read-only, and handed to the owner as a proposal. Code: `commonplace/topology/site_dns.py` (the DNS step), `commonplace/topology/proxy_adapters.py` (the reverse proxy adapters), `commonplace/topology/site_routes.py` (the mapping to nodes, links and a route) and `commonplace/topology/site_route_api.py` (the tool). Tests: `tests/test_topology_site_adapters.py`, `tests/test_topology_site_routes.py`.

```
topology_discover_site(site="example.com",
                       proxies=[{"node": "web1", "kind": "caddy", "config": "<the config text>"}],
                       machines=[{"node": "web1", "addresses": ["203.0.113.10"]}],
                       apps=[{"node": "blog", "host": "web1", "port": 3000, "names": ["blog"]}],
                       ttl_seconds=None, board=None)
```

Only `site` is required. The caller needs write access to the `topology` wing, and that is checked before anything is looked up. The agent reads the proxy configuration on the user's machine itself, without changing anything there, and passes the text in; the hub never reaches into a machine.

The route is derived in three steps, and every hop keeps the lines it was derived from:

1. **DNS.** One lookup of the public name through the hub's resolver: the alias chain and the addresses. Only public domain names are looked up: an address, a single label, `localhost` and special-use suffixes such as `.local`, `.internal` or `.test` are refused without a lookup. The step never connects to what it finds. The answer is matched against the `hostname` of the nodes on the map and against `machines`, the addresses the agent saw for a node. The match is the first hop, `<site>><node>:dns`.
2. **Reverse proxy.** The config handed in for that node is read by its adapter, which returns the sites it names and their upstream targets. A node of kind `cloud` or `router` is a proxy or CDN in front: the route continues with a `proxy` hop to the machine whose config names the site.
3. **Upstream.** The upstream for the whole site becomes the `reverse_proxy` hop:

| The upstream is | The route continues to |
| --- | --- |
| the machine itself (`localhost`, a loopback address, a unix socket, the node's own name) | the app the map or `apps` knows on that port, else a new container node `<node>-port-<port>` |
| a container or app known from `apps` or the map | that node; through a `reverse_proxy` hop to its machine and a `hosts` hop when it runs on another machine |
| another machine on the map (by `hostname`, `tailscale_name`, an alias or its key) | that machine, then a `hosts` hop to the app known on that port, if any |
| a bare name nobody listed (`blog:3000`) | a new container node `<node>-<name>` on the proxy's machine |
| any other name or address, or a placeholder the config does not spell out | an external node |

A site whose entry has no upstream (the machine serves it itself), a machine without a config handed in and a config that does not name the site all end the route at the machine, with a note that says which.

**External / unknown.** When the name points at nothing on the map, or the upstream is nobody's, the route ends in an explicit node of kind `cloud` named `External / unknown`, keyed `ext-<address or name>` and with that name as its `hostname`. Nothing is guessed past it: a proxy config that names the site on a machine DNS does not lead to is reported as a note, not stitched onto the route. To continue such a route, name the node in front under `machines` (kind `cloud` for a proxy or CDN) or record its hostname on the map, and run the discovery again.

**A proposal, never a write.** The derived nodes, links and route go through `proposals.propose` with the caller as observer. Nodes already on the map are not written again. A first route is one decision for the owner; a rerun that finds the same route asks nothing; a changed upstream shows up as the route's hops before and after and is applied only when the owner approves. A name without DNS records proposes nothing and removes nothing. The reply holds `site`, `resolved`, `ends` (`app`, `host`, `edge` or `external`), `hops` (each with its `link` and `evidence`), `notes`, `withheld`, and `applied`, `proposal` and `unchanged` as for `topology_propose`.

### Reverse proxy adapters

An adapter gets configuration text and returns the sites it names and, per site, the upstream targets with the config line as evidence. Two ship:

| `kind` | Reads |
| --- | --- |
| `caddy` | a Caddyfile (site blocks, the one-site form, snippets in the same file, `handle`, `handle_path` and `route` blocks, `reverse_proxy` with upstreams or a `to` line), or Caddy's JSON config |
| `nginx_proxy_manager` | its proxy-host rows as JSON (the API's list or rows of the `proxy_host` table), or the nginx server files it generates |

What every adapter guarantees:

- It reads only the text handed to it. An `import` or `include` of another file is not followed, no environment variable is read, nothing is run and nothing is connected to.
- It keeps only site names and upstream targets. Basic-auth users and hashes, API tokens, TLS keys, headers and everything else in the config are never copied. Credentials inside an upstream URL are cut off. A site or upstream line that still looks like a secret to the secret filter is left out and counted in `withheld`.
- A refusal (unknown `kind`, no text, more than 512 KB) never repeats any of the config. A config it cannot make sense of yields no sites instead of an error.

Another proxy is one class:

```python
from commonplace.topology import proxy_adapters

class TraefikAdapter(proxy_adapters.ProxyAdapter):
    kind = "traefik"
    label = "Traefik"

    def read(self, text, found):
        handle = found.site(["example.com"], "line 3: rule Host(`example.com`)")
        found.upstream(handle, "http://app:3000", "line 7: url http://app:3000")

proxy_adapters.register(TraefikAdapter())
```

`found.site` and `found.upstream` are the only way anything leaves an adapter; they apply the rules above. Add the new `kind` to the fixtures in `tests/test_topology_site_adapters.py`, including the secret tests.

### Python API

```python
site_dns.lookup(site, resolver=None) -> {"name", "cnames", "addresses", "evidence"}
proxy_adapters.parse(kind, text) -> Parsed(sites, withheld)
site_routes.derive(site, proxies=None, machines=None, apps=None, *, graph, resolver=None) -> dict
site_routes.discover(ctx, site, proxies=None, machines=None, apps=None,
                     ttl_seconds=None, board=None, *, resolver=None) -> dict
```

A resolver is a callable `resolver(name) -> site_dns.DnsAnswer(addresses, cnames)`. The hub uses the system resolver unless `gateway.topology_resolver` is set; tests pass a fake. A lookup that fails or takes longer than five seconds raises `DnsUnavailable` and proposes nothing. `derive` computes the route from `store.read_graph`'s result without proposing it.

## Existing workspaces

The store is additive: one new file per workspace with three tables, created by the first write. No existing file or table changes. A workspace from before this feature has no `topology.sqlite3`; it reads as an empty map (`nodes`, `links`, `routes` and `conflicts` all empty), `get_*` return `None`, and reading does not create the file.

## Map discovery

"Start agent work on map" asks every runner machine of the workspace to describe itself and how it reaches the hub and the other machines. Code: `commonplace/topology/discovery.py` (the start), `discovery_api.py` (the routes) and `collector.py` (what runs on a machine). Tests: `tests/test_topology_discovery.py`.

```
POST /api/topology/discovery      {} or {"project": "...", "topic": "..."}   the signed-in workspace owner only
GET  /api/topology/discovery      the newest run, each started machine with its job's live state
```

A run answers `{"run", "note", "machines": [{machine, label, outcome, reason, card, job, job_state}]}`, one entry for every runner machine that is not revoked:

| Outcome | When | `reason` |
| --- | --- | --- |
| `started` | a card was made and started through `card_start` | none |
| `running` | the machine's earlier discovery is still queued, starting or underway (six hours at most after it started) | none |
| `unreachable` | the hub has no live signal from the machine (Live status: `down`, or `unknown`) or it is turned off | `never_reported`, `no_heartbeat`, `disabled` |
| `refused` | the machine cannot take the work | `not_attested`, `no_topology_wing`, `no_project_or_topic`, or the card-start refusal |

Starts are serialized per workspace, so two presses at once cannot start the same machine twice; a machine whose start fails unexpectedly is `refused` with `start_failed` and the other machines still start. A workspace with no runner machine answers `note: "no_runner_machines"`. The project and topic default to the machine's first grants.

**The run is a normal run.** Each machine gets one card, "Map discovery on <label>", on the board `compute-map` (wing `topology`), whose body is the discovery prompt. The card is started through the ordinary `card_start`, so the machine's node detail panel lists the run with its card and log like any other, and the owner's rules for starts apply (machine grants, requesters, runtime mode `approval-required`). A start the rules refuse leaves its card in the backlog.

**The collector** is `python3 -m commonplace.topology.collector --runner-id <id> [--hub-url <url>] [--max-peers N]`, run by the started agent, which prints JSON:

- `propose`: the machine's own node (`kind`, `name`, `hostname`, `runner_id`, `os`, `hardware`, `resources`, `tailscale_name`) and its own links (to the hub, to Tailscale peers, to the hosts of the user's ssh config, directly or by a jump host), each link with the `status` the collector saw. A runner's additions about itself apply at once.
- `propose_peers`: the other machines it saw (the hub, Tailscale peers, ssh config hosts) as nodes. These are the owner's decision, in one proposal; a second call, so a machine that reports itself meanwhile cannot void the first.
- `unreachable`: what it tried and could not reach, with the reason; the link of such a place is written `down`. `notes` and a one-line `summary` complete the output.

Node keys are the first label of the host name in lowercase (`beta.local` is `beta`), so two machines that see each other agree on the key.

**Read-only.** The collector reads only `ALLOWED_READS` (the OS release, CPU and memory files, the DMI product name and vendor, the ssh config) and runs only `ALLOWED_COMMANDS` (`tailscale status --json`, three `sysctl -n` reads, `nvidia-smi -L`), enforced in `Probe.read` and `Probe.run`. It writes no file, changes no configuration, reads no key file or credential (the ssh config's key file lines are dropped, not emitted), connects only to the hub and to the hosts the user's own ssh and Tailscale setup names (two seconds each), and its output passes the store's secret filter as it is. The prompt tells the agent to run that one command, pass its output on unchanged and run nothing else.

**Reruns.** The evidence holds no times, so the same machine produces the same evidence: the facts are observed again (freshness and `seen_count` move) and nothing is asked. A fact that really changed is a proposal for that fact alone. A proposal whose fact already holds the proposed value (another observer wrote it first) still applies; one whose fact changed to something else is void and is proposed again by the next run.

**Needs.** The machine's grants must include the `topology` wing, because the agent works on a card of that board and writes the topology; a machine without it is reported as `no_topology_wing` and is not widened. The machine needs the Commonplace client installed (the prompt tells the agent to install nothing and to say so on the card when it is missing).

## Add machine and add site

The Compute screen has one button for each. Code: `commonplace/topology/add_node.py` (the logic), `add_api.py` (the routes) and `discovery.py` (the start both reuse). Tests: `tests/test_topology_add.py`.

```
POST /api/topology/add-machine    {}                    the signed-in workspace owner only
POST /api/topology/add-site       {"site": "example.com"}   the signed-in workspace owner only
```

Both answer `{"kind": "machine" | "site", "mode", "machines", "prompt"}`:

| `mode` | When | The answer |
| --- | --- | --- |
| `started` | a runner machine took the work (or already has it underway) | `machines`: the discovery start's rows (`machine`, `label`, `outcome`, `reason`, `card`, `job`); `prompt` is `null` |
| `prompt` | no runner machine could take it: none enrolled, none reachable, none attested for the topology wing, or (for a machine) every one is already on the map | `prompt`: the generic prompt to give an agent on the machine; `command`: for a machine, the short command to run there (`null` for a site); `machines` says why each runner was not asked |

**Add machine** asks every runner machine of the workspace that has no node on the map yet (no node holds its `runner_id`) to run the discovery prompt: the same read-only collector and the same two `topology_propose` calls as "Start agent work on map". The press takes no input. The fallback is for a machine that is not enrolled: `python3 -m commonplace.topology.collector [--hub-url <hub>]`, whose `--runner-id` is optional (the node then has no `runner_id`), and a prompt that tells an agent in a session connected to the workspace to run it and propose the output. The prompt never creates or asks for credentials: enrolling the machine as a runner stays the owner's step in the app.

**Add site** asks one runner machine, the first that can take it, to trace the route of one domain with `topology_discover_site`: it reads the reverse proxy configuration on that machine, as it is, and passes it on. The domain is the only input and must be a public domain name (`site_dns.public_name`; otherwise 400 and nothing starts). A second press for the same domain while the run is underway answers `running` and starts nothing; another domain starts its own run. The fallback prompt is the same text, for an agent on the machine that runs the proxy.

Both runs are cards on the `compute-map` board, started through the ordinary `card_start`, with `kind` and `subject` in their rows of `topology_discovery_runs` so that they never appear as the map button's own result. The prompts are fixed generic texts (`MACHINE_PROMPT`, `SITE_PROMPT`): they hold no host name, address, account name, path or person; the only values filled in are the hub's address, the command and the domain. The new node or site is part of the map only after the owner approves the proposal in the app.
