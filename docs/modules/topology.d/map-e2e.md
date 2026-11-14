- **Promises:**

  - Nothing in the topology code, the discovery prompt and collector, the map's web files, the topology docs or the topology tests and fixtures belongs to one person's setup: every address is a documentation or private-range address and every name is a reserved example or an invented one. `tests/test_topology_generic_guard.py` holds that, and a change that adds a real host name, address, account or key shape fails it.
- **Tests:**

  - `tests/test_topology_end_to_end.py` runs the Compute map on a clean workspace through the real hub with two synthetic runner machines, a synthetic Caddy config and a fake DNS resolver: the owner's "Start agent work on map" press, the agents' reports, the owner's approvals over the decision route, the map read and the four tabs of a device through the routes the screens call (taken from the screens' own path constants), then a second agent connection with only the two read tools answering "how do I connect to X" and "where is site Y hosted". It also shows the steps are needed: when the owner rejects or never answers, or nothing was started, the second agent is told `unknown:`.

  - `tests/test_topology_generic_guard.py` scans the topology code, prompt, collector, web files, docs, tests and fixtures for real-setup details derived from structure (addresses outside the documentation and private ranges, public-looking host names and e-mail domains, home-directory paths, ssh user names, hardware addresses, key and token shapes) and proves with a planted violation of each kind that it fires.
