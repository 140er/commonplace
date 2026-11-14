- **Owns:**
  `node_panel.py` tries a key that no map node holds as an enrolled runner machine's id, so the Compute screen can list a machine's jobs when the map has no machine nodes. `node_agents.read` takes `by_key` for that read.
- **Promises:**
  The answer carries the same `agents` block as the read through a node that names the machine, under the same wing grants (a connection list for owners only, a run or board only for a caller that may read its wing). No field is added.
- **How it fails:**
  A key that is not a machine of this caller's workspace, including another workspace's machine id, answers `node.found: false` with `agents` `{"applicable": false, "reason": "not_a_runner"}`, exactly as any unknown key does, so a machine id reveals nothing about other workspaces. A caller without the topology wing is refused as before.
- **Tests:**
  `tests/test_topology_node_panel.py` covers a runner id with no map node, an unknown key against another workspace's machine id, and the grants of an id read.
