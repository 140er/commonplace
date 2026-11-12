- **Owns:**

  - `node_panel.py` is the read behind the node detail panel, `GET /api/topology/node?key=<node key>`: the node's fields with provenance (observer, first line of evidence, observed and expiry times, stale flag, contenders), the site routes that start at, end at or pass through it, and the agents part from `node_agents.py`. Access is the `topology` wing grant, like the agent tools; a key nothing is recorded for answers `found: false`.

  - `node_agents.py` is the agents part of that read, for a node whose `runner_id` is an enrolled machine of this workspace: its two connections (workspace owners only), its queued, leased, starting or uncertain start jobs and started runs with no end reported in the last six hours (each with its card and log reference), and the boards whose live orchestrator config runs on it, with the checkpoint's lease holder and thread builders. A run or board shows only to a caller that may read the board's wing. It adds no storage and writes nothing.

- **Tests:**

  - `tests/test_topology_node_panel.py` covers the node read over real HTTP: every field with source and freshness, an expired field marked stale, an unrecorded key, bad arguments and a missing session, the agents of a machine with its runs, a machine with nothing running, a device that is no runner, a runner id nobody enrolled, grants (no topology wing, topology without the board's wing, both, a member session), site roles and unknown hops, another workspace and the absence of tokens in the answer.
