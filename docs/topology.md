# Topology store

The single source of truth for the Compute map: machines, how they reach each other, and how a website is routed to the app that serves it. Everything is a structured fact in the workspace's `topology` wing. The map, the agent tools and the live status all read from this store; nothing else holds a second copy.

Code: `commonplace/topology/model.py` (what may be stored) and `commonplace/topology/store.py` (how it is stored and read). Tests: `tests/test_topology_store.py`. How agents write and the owner approves is under Proposals below; how an agent finds a website's route is under Site route discovery.

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
| a runner, about anything else | new or different | proposed |
| any other agent | new or different | proposed |
| any other agent | same value | nothing; counted as `unchanged` |
| the signed-in owner | new, different or removed | applied at once, as the authoritative value |
| anyone but the owner | removed | proposed |

A runner is the worker connection of an enabled machine whose grant includes the `topology` wing; its observer is `runner` with the machine id. "About itself" is a node whose `runner_id` is that machine, a new node the runner declares with its own `runner_id`, and a link with such a node at either end. Routes, other nodes, and binding itself to a node that already exists are not about itself.

Everything proposed in one call is one proposal and one decision. The reply holds `applied` and `proposal` (each `null` or a row) and `unchanged`. The same observer proposing exactly the same change while its decision is still open gets that proposal back instead of a second card. The decision is opened before anything is written: when it cannot be opened (no board the caller may write), the call fails and stores no fact.

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
- **Reject**, or a written reply without a choice, changes nothing. The old fact stays.
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
