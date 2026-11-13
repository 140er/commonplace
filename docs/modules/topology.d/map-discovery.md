- **Owns:**

  - Map discovery behind "Start agent work on map". `discovery.py` is the start: one card per runner machine on the `compute-map` board (wing `topology`), each started through the ordinary `card_start`, so every run is a normal run with a card, a job and a log. The card body is the generic discovery prompt (`card_body`). `discovery_api.py` is `POST` and `GET /api/topology/discovery` (the signed-in owner's button and the last run's outcomes), mounted by `install` in `topology/__init__.py`. Every machine of the run gets one row of `topology_discovery_runs` in `topology.sqlite3`: `started`, `running`, `unreachable` or `refused`, with a reason code.

  - `collector.py` is what the started agent runs on its machine (`python3 -m commonplace.topology.collector`): the machine's kind, hardware, OS and resources, and how it reaches the hub and the other machines (direct, jump host, Tailscale), as the two `topology_propose` calls `propose` (the machine and its own links) and `propose_peers` (the machines it only saw, for the owner to decide), with the places it could not reach in `unreachable`. All access goes through `Probe`, whose lists `ALLOWED_READS` and `ALLOWED_COMMANDS` are enforced in `read` and `run`.
- **Promises:**

  - Discovery is read-only and generic. The collector reads a short fixed list of files and runs a short fixed list of commands, writes nothing, never reads a key file or credential (an ssh config's key file lines are dropped, not emitted), and opens connections only to the hub and to hosts the user's own ssh and Tailscale setup names. Nothing about any one setup is built into the prompt or the collector.

  - What an agent finds is a proposal like any other: its own machine and links apply at once, other machines wait for the owner, a rerun with nothing changed writes only a fresh observation, and a changed fact is the only thing asked again. A machine that cannot be reached, is turned off, lacks the topology wing or has no project or topic is reported with its reason in the run, never skipped silently, and starts nothing.

  - A second press while a machine's discovery card is still being started or worked on starts nothing new and says `running`.
- **How it fails:**

  - A caller who is not a signed-in workspace owner is refused (403) and nothing starts. A workspace without runner machines answers `note: no_runner_machines`. A machine whose start is refused by the card-start rules is `refused` with that reason and its card is parked in the backlog.
- **Tests:**

  - `tests/test_topology_discovery.py` covers two fake runner machines through the real gateway (one normal start each, the expected nodes and links without manual entry, the run visible on the machine with its card), a rerun that only refreshes and the one real change that is asked, an unreachable peer, hub and machine, a second press, the owner-only route, and the read-only guarantee: the collector's source (no write, no process outside its list), its lists, the files it actually reads, no key file or secret in its output, and the prompt's wording. `tests/test_topology_discovery_ui.py` runs `tests/topology_discovery_ui.mjs`: the outcome lines, the button's states and the wiring on both screens.
