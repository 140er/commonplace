## topology

- **Paths:** `commonplace/topology/`, `docs/topology.md`, `tests/test_topology_store.py`, `tests/test_topology_proposals.py`, `tests/test_topology_status.py`
- **Owns:**

  - The topology store behind the Compute map: nodes, links and site routes as structured `(subject, predicate, object)` facts in the workspace's `topology` wing, in the workspace file `topology.sqlite3` (`topology_facts`, `topology_observations`, `topology_conflicts`). `docs/topology.md` is the contract: node kinds, link methods, routes, provenance, expiry, conflicts, the secret filter and the Python read and write signatures.

  - `model.py` is the allow-list: every field and its shape, the observer, lifetimes, subjects and the secret filter. `store.py` holds `TopologyStore` (one workspace's file, opened like `bots.sqlite3`; `open_topology` caches one per workspace on the gateway) and writes and reads: `upsert` (and the node, link and route forms), `retract`, `resolve_conflict`, `get_node`, `get_link`, `get_route`, `read_graph`, `list_conflicts`, `history`.

  - The write side for agents. `proposals.py` holds the rules: `propose` compares a request with the map fact by fact, applies a runner's additions about itself (and the signed-in owner's own write) at once, and turns every other addition, change or removal into one proposal with one owner decision opened through the board's ask path (structured options Approve and Reject, the map after and before as their previews). `settle` applies or closes a proposal from its card's history, `undo` puts back what an applied change replaced, `listing` and `get` read the rows. The rows are the table `topology_proposals` in `topology.sqlite3`. `proposal_api.py` is the surface, mounted by one import in `topology/__init__.py` (`commonplace.topology` is one entry of `extensions.MODULES`): the MCP tool `topology_propose`, `/api/topology/proposals` (propose, list, read, undo) and the `card.decided` listener that calls `settle`.

  - `status.py` derives live status for machines, nodes and both ends of every link from signals the hub already keeps (the runner connections' last use, the chat relay's last poll, started and underway runner jobs) and the optional `status` path report of a link. `read_status(gateway, workspace_id, graph=, now=, config=)` is the one read; `derive` and `machine_status` are the pure core; `StatusConfig` holds the thresholds (arguments or `COMMONPLACE_TOPOLOGY_*` variables). No probing; a machine or path with no signal is `unknown`, never green. Rules, gaps and the signal list: `docs/topology.md`, "Live status".
- **Promises:**

  - Every fact keeps who observed it, when, from what verbatim evidence, and when it expires. A stale value leaves `values` and is still readable with `stale: true`; nothing is deleted.

  - Two observers disagreeing leaves the current value and files the other as a conflict record; only the same observer, an expired value, an owner decision or an authoritative `replace` write changes the current value, and the old one stays in history.

  - Keys, tokens, passwords and private key material are refused on write, in fields, evidence and observer ids. The refusal names the field and the kind of secret, never the value, and stores nothing.

  - An agent's change reaches the map only after the workspace owner answered Approve on the ask exactly as it was opened: an edited, withdrawn or re-asked card, an answer by anyone else, or a map that no longer matches the shown "before" voids the proposal and applies nothing. Reject keeps the old fact, and the same change is not asked again for seven days. A runner writes without a question only new facts about itself and the live `status` of its own links.

  - Every applied change keeps each fact's value before and after; undo is the signed-in owner's and deletes nothing. A proposal passes the secret filter before a row or a card exists.

  - The schema is additive and each write is one transaction. The store does not authorize: callers check the `topology` wing with the existing wing grants. A store is one workspace's file, so reads never cross workspaces; the control database holds no topology text.
- **How it fails:**

  - A threshold set to a non-whole or inconsistent number (green not below red) raises `TopologyError`. A value outside the allow-list, a broken route chain or secret-looking text raises `TopologyError` (`SecretRefused` for secrets) before anything is stored. A workspace without `topology.sqlite3` reads as an empty map, and reading does not create the file.

  - A proposal that does not fit one readable decision is refused ("propose it in smaller parts"). A caller without write access to the `topology` wing does not see `topology_propose` and gets 403 on the route. If the hub stops between the owner's answer and the write, the next read of the proposals settles it; a proposal whose card never opened is void after ten minutes.
- **Tests:**

  - `tests/test_topology_store.py` covers upsert and read back with provenance and expiry, refreshes, allow-list refusals, stale facts and route hops, conflicts and their resolutions, the authoritative write, retraction, secret refusals, workspace isolation, an old workspace without the file, reopening, a moved workspace directory, a crash inside a write and the wing grant.

  - `tests/test_topology_proposals.py` runs through the real gateway: one decision with a readable before and after, approve (also through `/api/cards/<n>/decide`), reject, a reply without a choice, removal, a runner's self-report and its rerun, a runner's change and its facts about others, a non-owner who cannot approve, an edited card, a map that moved on, a missed event, secret refusals, invalid requests, undo, the wing grant, the REST routes and an old workspace. `tests/spaces_surfaces.json` carries `topology_propose`.

  - `tests/test_topology_status.py` walks a machine and a link end through green, orange, red and green on a fake clock, covers a machine that never reported, start in progress, revoked and disabled machines, the newest signal winning, thresholds from arguments and the environment, path report ageing, one of several paths failing, stale link facts, the hub's real signals (connection use, chat poll, runner jobs), the single read with and without a supplied graph and workspace isolation.
