### Runner machine id as a topology node key

- `GET /api/topology/node?key=...` accepts an enrolled runner machine's id when no map node has that key and answers with the same `agents` block (runs, coordinators, owner-only connections) as the read through a node that names the machine. The Compute screen needs it when the map has no machine nodes and lists enrolled runners instead.
- The lookup is limited to the caller's own workspace and to the same wing grants as before. A machine id of another workspace is answered exactly like an unknown key (`not_a_runner`, never `machine_not_found`), so the route is no way to learn which ids exist elsewhere. The id is only used when no node matches; a node that holds the key keeps its own answer.
