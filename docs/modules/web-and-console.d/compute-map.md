- **Owns:**

  - The Compute map screen at `#/compute-map` (`topology-map.js` with `topology-layout.js` and `topology-icons.js`, styled by `topology.css`): an SVG canvas with pan, zoom, dragged positions and a highlighted site route. It draws only what `GET /api/topology` returns, nodes, links, routes and the `status` word per node and link end, reads again every five seconds while visible, and keeps only dragged positions and the view in the browser per workspace. Contract: `docs/topology.md`, "The map in the app".

- **Promises:**

  - A status is never shown by colour alone (shape and text as well), a stale or unrecorded device is drawn as such instead of dropped or invented, and nothing is loaded from another origin.
