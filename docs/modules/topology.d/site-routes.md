- **Owns:**

  - Site route discovery. `site_dns.py` is the DNS step: one lookup of one public name through an injected resolver (`lookup`, `public_name`, `system_resolver`). `proxy_adapters.py` is the reverse proxy adapter interface (`ProxyAdapter`, `Found`, `register`, `parse`, `serving`, `primary`) with the `caddy` and `nginx_proxy_manager` adapters. `site_routes.py` maps both to nodes, links and a route (`derive`) and hands the result to `proposals.propose` (`discover`). `site_route_api.py` is the MCP tool `topology_discover_site`, mounted by one import in `topology/__init__.py`.

- **Promises:**

  - Site route discovery is read-only and never writes the store: its result is a proposal like any other, so a changed upstream is a change the owner approves. An adapter reads only the text handed to it, runs nothing, and lets out only site names and upstream targets; credentials in a proxy config are never stored. Only public domain names are looked up, and a name or upstream outside the map ends the route in an explicit `External / unknown` node.

- **Tests:**

  - `tests/test_topology_site_adapters.py` covers the DNS step with a fake resolver (alias chain, no records, non-public names, a failing or hanging resolver, the system resolver) and both adapters on invented configs in each form they read, the dropped credentials, withheld lines, no file, process or network use, wildcard matching and a third adapter. `tests/test_topology_site_routes.py` covers the derived route per proxy, apps on the same and on another machine, the external hop for a domain and for an upstream, a proxy or CDN in front, refusals, and through the real gateway: one proposal, approve, a changed upstream proposed and rejected, an unchanged rerun, no secret in any stored file, the MCP tool, the wing grant and the owner's own run. `tests/spaces_surfaces.json` carries `topology_discover_site`.
