- **Owns:**

  - `topology-discovery.js`: the map's "Start agent work on map" button posts to `/api/topology/discovery` and writes what happened on each machine (started, already running, not reachable, not started, each with its reason) under the button. `app.js` and `deck-compute.js` give the map this handler as `onStartWork`.
