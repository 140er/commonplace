# Topology store

The single source of truth for the Compute map: machines, how they reach each other, and how a website is routed to the app that serves it. Everything is a structured fact in the workspace's `topology` wing. The map, the agent tools and the live status all read from this store; nothing else holds a second copy.

Code: `commonplace/topology/model.py` (what may be stored) and `commonplace/topology/store.py` (how it is stored and read). Tests: `tests/test_topology_store.py`.

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

## Map read

`GET /api/topology/map` is what the map canvas in the web app draws from. It is an interim, deliberately small read in its own module (`commonplace/topology/map_route.py`): the agent tools add the general graph read and the live status read adds status derived from runner heartbeats, and the canvas then reads those instead.

Only a signed-in workspace owner whose policy reads the `topology` wing gets an answer; anyone else gets `403`, and a request without a session `401`. It reads one workspace's store and creates no file.

```json
{
  "graph": {
    "wing": "topology",
    "nodes": [{"subject": "node:web1", "wing": "topology", "key": "web1",
               "values": {"kind": "server", "name": "Web 1", "role": "app host"},
               "stale": false, "conflicted": false, "last": {}}],
    "links": [{"subject": "link:laptop>web1:tailscale", "wing": "topology",
               "id": "laptop>web1:tailscale", "from": "laptop", "to": "web1", "method": "tailscale",
               "values": {"method": "tailscale", "status": "up"}, "stale": false, "conflicted": false}],
    "routes": [{"subject": "route:example.test", "wing": "topology", "site": "example.test",
                "values": {"hops": ["example.test>web1:reverse_proxy"]}, "stale": false, "conflicted": false,
                "hops": [{"link": "example.test>web1:reverse_proxy", "from": "example.test", "to": "web1",
                          "method": "reverse_proxy", "known": true, "stale": false, "status": "unknown"}]}]
  },
  "status": {
    "nodes": {"web1": "unknown"},
    "links": {"laptop>web1:tailscale": {"from": "up", "to": "up"}}
  }
}
```

- `graph` is `read_graph` without `fields` and without `conflicts`: no evidence and no observers, only what is drawn. A node also has `last`: the `kind`, `name` and `role` that have expired, so a stale node keeps its icon and label and is marked stale.
- `status` has one state per node and one per end of a link: `up`, `degraded`, `down` or `unknown`. Until the live status read exists, every node is `unknown` and both ends of a link carry the link's own fresh `status` fact; an expired or missing status is `unknown`, never `up`.

## The map in the app

The owner opens the map from **Compute map** under **Workspace** in the account menu, or from the command palette (`#/compute-map`). It is drawn as SVG by `commonplace/web/static/topology-map.js` with `topology-layout.js` (layout, pan and zoom maths) and `topology-icons.js` (one icon per node kind, drawn for this app). Nothing is loaded from another origin.

- **Devices** are icons with the name and the role underneath. A stale device is faded with a dashed outline and says "stale"; a device with an open conflict says "conflict"; a device that a link or route names but that has no facts is drawn as an unknown device that says "not recorded".
- **Links** are lines with a status mark at each end. A status is a shape and a word as well as a colour: circle for up, triangle for degraded, square for down, ring for unknown. Each mark and each device carries its status as text for a screen reader and as a tooltip, and the legend names the four shapes. Two methods between the same two devices run side by side.
- **Layout** is automatic and the same on every load: rows from site to DNS or CDN, edge, machine and app, with the devices people sit at last. A device dragged somewhere stays there. Dragged places and the chosen view are remembered in the browser, per workspace; **Reset layout** forgets the places.
- **Moving around:** drag the background to pan, scroll or pinch to zoom, or use the buttons. With the canvas focused, the arrow keys pan, `+` and `-` zoom and `0` fits everything. On a focused device, Enter selects it and Shift with an arrow key moves it.
- **Route** highlights one site's route as a path, dims the rest, and writes the hops out in words, including a hop that is unknown or stale.
- **Live:** the map reads again every five seconds while the tab is visible and redraws only when something changed. If a read fails, the last map stays and a line says it is not updating.
- **Empty workspace:** the empty state has one button, "Start agent work on map". The canvas only offers it; the discovery work behind it is separate, and until that is connected the button is off and says so.

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

## Existing workspaces

The store is additive: one new file per workspace with three tables, created by the first write. No existing file or table changes. A workspace from before this feature has no `topology.sqlite3`; it reads as an empty map (`nodes`, `links`, `routes` and `conflicts` all empty), `get_*` return `None`, and reading does not create the file.
