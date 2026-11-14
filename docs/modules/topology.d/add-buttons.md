- **Owns:**

  - "Add machine" and "Add site" behind the Compute screen's two buttons. `add_node.py` is the logic: the generic prompts (`MACHINE_PROMPT`, `SITE_PROMPT`), `add_machine` and `add_site`. Both are `discovery.start` with a different `Work` (card title, body, which machines may take it, and `first_only` for a site), so a run is the same normal card start with a card, a job and a log. `add_api.py` is `POST /api/topology/add-machine` and `POST /api/topology/add-site`, mounted by `install` in `topology/__init__.py`. The collector's `--runner-id` is optional, for a machine that is not enrolled.
- **Promises:**

  - One press either starts a run (`mode: "started"`, with one row per machine that was asked and why the others were not) or, when no runner machine can take the work, answers `mode: "prompt"` with a ready, generic prompt and, for a machine, the short command to run on it. Nothing is made on the board or the map in the second case. A machine is asked only while it has no node on the map; a site is asked of one machine only. The only input is a domain name for a site; nothing for a machine.

  - The prompts contain nothing specific to any one setup: no host name, address, account name, path or person. What an agent finds reaches the owner as a proposal (`topology_propose`, `topology_discover_site`), and the map changes only when the owner approves it in the app.
- **How it fails:**

  - A caller who is not a signed-in workspace owner is refused (403) and nothing starts. A domain that is not a public domain name is refused (400) before anything starts.
- **Tests:**

  - `tests/test_topology_add.py` covers both buttons through the real gateway with fake runner machines: a run on a runner that is not on the map, the node or site appearing after approval only, a second press, the prompt fallback (no runner, none reachable, all already on the map), bad domains, the owner-only routes, that the discovery button's own result is not mixed with these runs, and that the prompts contain nothing specific to a setup. `tests/topology_add_ui.mjs` (run by `tests/test_topology_add_ui.py`) covers the buttons' requests, the copied prompt and its fallback.
